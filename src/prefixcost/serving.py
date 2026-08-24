"""Simulate serving the workload through a prefix cache, and count what it cost.

The simulation is deliberately small, because it only has to be right about one
thing: how many prompt tokens the server actually processes. A request whose first
k tokens are already resident pays for its remaining tokens and nothing else.
Attention arithmetic, batching and scheduling are all out of scope, and none of
them changes which tokens are prefill misses.

The cache is the trie, and that is the correction this file exists because of.

The first implementation stored whole prompts and matched a request against the
complete prompts it had seen before. Under an unbounded cache it reported 28504
prefill tokens where the trie says 24567, and the gap is the point: a real prefix
cache holds key and value blocks, so *any* prefix computed before is reusable,
including one that was never itself a complete request. Turn two of a conversation
computes a prefix that turn three reuses even though nothing ever sent that exact
prompt. Storing whole prompts models a request cache wearing a prefix cache's
name, and it understates the hit rate by a quarter here.

So residency is a flag on a trie node. Serving a request walks its path, finds the
deepest resident node, pays for the rest, and marks the whole path resident. Under
an unbounded cache the total prefill is then the node count exactly, which is
asserted rather than hoped for.

Eviction is from the leaves inwards, because a prefix cache cannot hold a child
without its parent: the child's keys and values were computed from the parent's.
Evicting an interior node while keeping its descendants would model something no
cache does and would flatter the hit rate.

The oracle policy is not implementable, since it evicts whatever is needed
furthest in the future. It is here to bound the LRU one, which is how the cost of
a *policy* gets separated from the cost of a *capacity*.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .config import Policy
from .errors import UsageError
from .trie import PrefixTrie, build_trie
from .workload import Workload

CACHE_POLICIES = ("lru", "oracle", "none")


@dataclass(frozen=True)
class ServedRequest:
    """What one request actually cost, given the cache state it met."""

    index: int
    tenant: int
    prompt_tokens: int
    prefill_tokens: int
    cached_tokens: int
    output_tokens: int

    @property
    def hit_share(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0


@dataclass
class ServingResult:
    """The whole run. Totals are integers, because they are counts of tokens."""

    served: tuple[ServedRequest, ...]
    prefill_tokens: int
    cached_tokens: int
    decode_tokens: int
    evictions: int
    policy_name: str
    capacity_tokens: int
    order: tuple[int, ...] = field(default=())

    @property
    def prompt_tokens(self) -> int:
        return self.prefill_tokens + self.cached_tokens

    @property
    def hit_share(self) -> float:
        return self.cached_tokens / self.prompt_tokens if self.prompt_tokens else 0.0

    def cost(self, policy: Policy) -> float:
        return (
            self.prefill_tokens * policy.pricing.prefill_per_token
            + self.decode_tokens * policy.pricing.decode_per_token
        )

    def prefill_by_tenant(self) -> dict[int, int]:
        totals: dict[int, int] = {}
        for item in self.served:
            totals[item.tenant] = totals.get(item.tenant, 0) + item.prefill_tokens
        return dict(sorted(totals.items()))

    def as_dict(self) -> dict[str, object]:
        return {
            "prefill_tokens": self.prefill_tokens,
            "cached_tokens": self.cached_tokens,
            "decode_tokens": self.decode_tokens,
            "prompt_tokens": self.prompt_tokens,
            "hit_share": self.hit_share,
            "evictions": self.evictions,
            "policy": self.policy_name,
            "capacity_tokens": self.capacity_tokens,
        }


def serve(
    workload: Workload,
    policy: Policy,
    order: tuple[int, ...] | None = None,
    *,
    capacity_tokens: int | None = None,
    cache_policy: str | None = None,
    trie: PrefixTrie | None = None,
) -> ServingResult:
    """Replay the workload in `order` and count the tokens actually processed."""
    name = cache_policy if cache_policy is not None else policy.cache.policy
    if name not in CACHE_POLICIES:
        raise UsageError(f"unknown cache policy {name!r}, expected one of {list(CACHE_POLICIES)}")
    capacity = capacity_tokens if capacity_tokens is not None else policy.cache.capacity_tokens
    if capacity < 0:
        raise UsageError(f"a cache capacity cannot be negative, got {capacity}")

    sequence = order if order is not None else tuple(range(len(workload.requests)))
    if name == "none" or capacity == 0:
        return _serve_uncached(workload, sequence, name)

    structure = trie if trie is not None else build_trie(workload.requests)
    return _serve_cached(workload, sequence, capacity, name, structure)


def _serve_uncached(workload: Workload, sequence: tuple[int, ...], name: str) -> ServingResult:
    """No cache, which is the exact anchor in the other direction.

    Every request pays for every one of its tokens, so a scheme that charges
    actual work and a scheme that charges the per request token count agree
    exactly rather than approximately.
    """
    served = tuple(
        ServedRequest(
            index=index,
            tenant=workload.requests[index].tenant,
            prompt_tokens=workload.requests[index].prompt_tokens,
            prefill_tokens=workload.requests[index].prompt_tokens,
            cached_tokens=0,
            output_tokens=workload.requests[index].output_tokens,
        )
        for index in sequence
    )
    return ServingResult(
        served=served,
        prefill_tokens=sum(item.prefill_tokens for item in served),
        cached_tokens=0,
        decode_tokens=sum(item.output_tokens for item in served),
        evictions=0,
        policy_name=name,
        capacity_tokens=0,
        order=sequence,
    )


def _serve_cached(
    workload: Workload,
    sequence: tuple[int, ...],
    capacity: int,
    name: str,
    trie: PrefixTrie,
) -> ServingResult:
    resident: dict[int, int] = {}
    resident_children: dict[int, int] = {}
    parent: dict[int, int] = {}
    heap: list[tuple[int, int]] = []
    served: list[ServedRequest] = []
    evictions = 0
    next_use = _next_use(workload, sequence, trie) if name == "oracle" else None

    for clock, (position, index) in enumerate(enumerate(sequence), start=1):
        request = workload.requests[index]
        path = list(trie.walk(request.tokens))

        cached = 0
        for depth, node in enumerate(path, start=1):
            if id(node) in resident:
                cached = depth
            else:
                break
        missing = len(path) - cached
        served.append(
            ServedRequest(
                index=index,
                tenant=request.tenant,
                prompt_tokens=len(path),
                prefill_tokens=missing,
                cached_tokens=cached,
                output_tokens=request.output_tokens,
            )
        )

        previous = 0
        for node in path:
            key = id(node)
            if key not in resident:
                resident[key] = clock
                resident_children.setdefault(key, 0)
                parent[key] = previous
                if previous:
                    resident_children[previous] = resident_children.get(previous, 0) + 1
            else:
                resident[key] = clock
            if next_use is None:
                heapq.heappush(heap, (clock, key))
            previous = key

        while len(resident) > capacity:
            key = (
                _lru_victim(heap, resident, resident_children)
                if next_use is None
                else _oracle_victim(resident, resident_children, next_use[position], len(sequence))
            )
            if key is None:  # pragma: no cover - unreachable while any leaf is resident
                # No evictable leaf. Reachable only if every resident node has a
                # resident child, which cannot happen because the deepest node on
                # any path is always a leaf. Left as a guard rather than an
                # assertion, because looping forever is the worse failure.
                break
            del resident[key]
            resident_children.pop(key, None)
            above = parent.pop(key, 0)
            if above:
                resident_children[above] = max(0, resident_children.get(above, 1) - 1)
            evictions += 1

    return ServingResult(
        served=tuple(served),
        prefill_tokens=sum(item.prefill_tokens for item in served),
        cached_tokens=sum(item.cached_tokens for item in served),
        decode_tokens=sum(item.output_tokens for item in served),
        evictions=evictions,
        policy_name=name,
        capacity_tokens=capacity,
        order=sequence,
    )


def _lru_victim(
    heap: list[tuple[int, int]], resident: dict[int, int], children: dict[int, int]
) -> int | None:
    """The least recently used resident leaf, by lazy invalidation.

    Entries are never removed from the heap when a node is touched again; a stale
    entry is recognised because its recorded clock is not the node's current one,
    and is discarded on the way past. That keeps eviction near constant time,
    which is the reason the capacity sweep finishes at all.

    An interior node is skipped rather than evicted. A prefix cache cannot hold a
    child without its parent, so a node with resident children is not a candidate,
    and it is re-pushed so it becomes one once its children have gone.
    """
    deferred: list[tuple[int, int]] = []
    victim: int | None = None
    while heap:
        clock, key = heapq.heappop(heap)
        if key not in resident or resident[key] != clock:
            continue
        if children.get(key, 0) > 0:
            deferred.append((clock, key))
            continue
        victim = key
        break
    for entry in deferred:
        heapq.heappush(heap, entry)
    return victim


def _oracle_victim(
    resident: dict[int, int],
    children: dict[int, int],
    horizon: dict[int, int],
    far: int,
) -> int | None:
    """The resident leaf needed furthest in the future, by direct scan.

    A scan rather than a heap, because the oracle's priority for a node changes at
    every position, so a heap would be almost entirely stale entries. This is the
    slow policy and it is only ever run to bound the fast one, which is why the
    cost is acceptable and is stated rather than hidden.
    """
    best: int | None = None
    best_distance = -1
    for key in resident:
        if children.get(key, 0) > 0:
            continue
        distance = horizon.get(key, far)
        if distance > best_distance:
            best, best_distance = key, distance
    return best


def _next_use(
    workload: Workload, sequence: tuple[int, ...], trie: PrefixTrie
) -> list[dict[int, int]]:
    """For each position, when each node id is next touched.

    Built once by walking the sequence backwards and sharing the tail dictionary,
    because rebuilding it per eviction would make the policy comparison a
    statement about the simulator's speed rather than about the policy.
    """
    future: list[dict[int, int]] = [{} for _ in sequence]
    seen: dict[int, int] = {}
    for position in range(len(sequence) - 1, -1, -1):
        seen = dict(seen)
        for node in trie.walk(workload.requests[sequence[position]].tokens):
            seen[id(node)] = position
        future[position] = seen
    return future
