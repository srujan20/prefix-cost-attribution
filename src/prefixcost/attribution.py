"""Five ways to divide one bill, and the three properties no two of them share.

An attribution scheme takes a workload and produces a number per tenant. Three
properties are worth wanting and this file is about the fact that you cannot have
all of them at once when the cache is finite.

**Efficient**: the shares sum to what the server actually spent. A scheme that is
not efficient is not a bill, it is an opinion, and somebody is absorbing the
difference.

**Order independent**: the same set of requests produces the same shares whatever
order they arrived in. A scheme without this charges a tenant differently for the
same usage depending on who else happened to be first, which is indefensible the
moment a customer asks.

**Shared cost fairly split**: a prefix used by six tenants is paid for by those
six, in proportion to nothing but the fact that they used it.

`marginal` is efficient and order dependent. `shapley` is fair and order
independent, and efficient only when the cache never evicts. `per_request` is
none of the three and is what almost everybody bills.

The Shapley value here is exact and cheap, which is the technical result in this
file. For a cost game played on a tree, where a coalition pays for the nodes its
paths cover, the Shapley value of a player is the sum over the nodes on its path
of that node's cost divided by the number of players using it. So the allocation
usually described as exponential is a single pass over the trie, and this file
does exactly that rather than sampling permutations and calling the average a
Shapley value.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import ORDER_INDEPENDENT, SCHEMES, Policy
from .errors import UsageError
from .serving import ServingResult
from .trie import PrefixTrie
from .workload import Workload


@dataclass(frozen=True)
class Allocation:
    """One scheme's answer, plus what it is an answer about."""

    scheme: str
    shares: dict[int, float]
    prefill_shares: dict[int, float]
    prefill_total: float
    decode_total: float
    order_independent: bool

    @property
    def total(self) -> float:
        return sum(self.shares.values())

    def sums_to(self, actual: float, tolerance: float = 1e-06) -> bool:
        """Whether this allocation is a bill or an opinion, at the given total."""
        return abs(self.total - actual) <= tolerance * max(1.0, abs(actual))

    def as_dict(self) -> dict[str, object]:
        return {
            "scheme": self.scheme,
            "shares": {str(tenant): value for tenant, value in sorted(self.shares.items())},
            "prefill_shares": {
                str(tenant): value for tenant, value in sorted(self.prefill_shares.items())
            },
            "total": self.total,
            "prefill_total": self.prefill_total,
            "decode_total": self.decode_total,
            "order_independent": self.order_independent,
        }


def decode_shares(workload: Workload, policy: Policy) -> dict[int, float]:
    """Output tokens, priced. The part of the bill nobody argues about.

    Decode is never shared: every output token is generated for exactly one
    request. So every scheme in this file agrees about it, and the disagreement
    that follows is entirely about prefill. Keeping the two apart is what makes
    the comparison legible.
    """
    shares = dict.fromkeys(workload.tenants, 0.0)
    for request in workload.requests:
        shares[request.tenant] += request.output_tokens * policy.pricing.decode_per_token
    return shares


def per_request(
    workload: Workload, policy: Policy, _trie: PrefixTrie, _result: ServingResult
) -> Allocation:
    """Charge every request for every one of its tokens. What almost everybody bills.

    Order independent, because it never looks at the cache. Not efficient, because
    it charges for tokens the server did not process: with a prefix cache it
    over-attributes by exactly the cached share, and the surplus goes to whoever
    is doing the billing.
    """
    prefill = dict.fromkeys(workload.tenants, 0.0)
    for request in workload.requests:
        prefill[request.tenant] += request.prompt_tokens * policy.pricing.prefill_per_token
    return _finish("per_request", prefill, workload, policy)


def marginal(
    workload: Workload, policy: Policy, _trie: PrefixTrie, result: ServingResult
) -> Allocation:
    """Charge each request for the tokens actually processed for it.

    Efficient by construction: the shares sum to the served total exactly, because
    they are the served total, regrouped. And order dependent, because which
    request finds the prefix already resident is a fact about arrival order rather
    than about the request. That is the whole tension, and exp02 measures it.
    """
    prefill = dict.fromkeys(workload.tenants, 0.0)
    for item in result.served:
        prefill[item.tenant] += item.prefill_tokens * policy.pricing.prefill_per_token
    return _finish("marginal", prefill, workload, policy)


def equal_split(
    workload: Workload, policy: Policy, trie: PrefixTrie, _result: ServingResult
) -> Allocation:
    """Split the shared prefill equally among tenants, and charge the rest as used.

    The scheme a finance team reaches for when told that some cost is shared.
    Order independent and simple to explain. Its problem is that it charges a
    tenant with one conversation the same share of the common prompt as a tenant
    with a thousand, which is why it is here rather than recommended.
    """
    tenants = workload.tenants
    private = dict.fromkeys(tenants, 0.0)
    shared_tokens = 0
    for node in trie.iterate():
        if len(node.tenants) == 1:
            private[next(iter(node.tenants))] += 1.0
        else:
            shared_tokens += 1
    per_tenant = shared_tokens / len(tenants)
    prefill = {
        tenant: (private[tenant] + per_tenant) * policy.pricing.prefill_per_token
        for tenant in tenants
    }
    return _finish("equal_split", prefill, workload, policy)


def proportional(
    workload: Workload, policy: Policy, trie: PrefixTrie, _result: ServingResult
) -> Allocation:
    """Split the shared prefill in proportion to each tenant's own token count.

    The other scheme a finance team reaches for, and the more defensible of the
    two, because a heavy user pays more. It is still not the fair split: it
    charges a tenant for shared prefixes it never used, in proportion to usage it
    incurred elsewhere.
    """
    tenants = workload.tenants
    private = dict.fromkeys(tenants, 0.0)
    shared_tokens = 0
    for node in trie.iterate():
        if len(node.tenants) == 1:
            private[next(iter(node.tenants))] += 1.0
        else:
            shared_tokens += 1

    weights = dict.fromkeys(tenants, 0.0)
    for request in workload.requests:
        weights[request.tenant] += request.prompt_tokens
    total_weight = sum(weights.values())
    prefill = {
        tenant: (private[tenant] + shared_tokens * (weights[tenant] / total_weight))
        * policy.pricing.prefill_per_token
        for tenant in tenants
    }
    return _finish("proportional", prefill, workload, policy)


def shapley(
    workload: Workload, policy: Policy, trie: PrefixTrie, _result: ServingResult
) -> Allocation:
    """The exact Shapley value, in one pass over the trie.

    Each node costs one prefill token and is paid for by the tenants whose
    requests pass through it, split equally among them. For a cost game on a tree
    that *is* the Shapley value: it is efficient on the tree's own total,
    symmetric, gives a dummy nothing, and is additive, and those four properties
    determine the allocation uniquely.

    So the scheme normally dismissed as exponential is linear in the number of
    nodes here, and it is computed rather than sampled. A sampled Shapley value
    would carry a Monte Carlo error into a bill, which is a strange thing to hand
    a customer.
    """
    prefill = dict.fromkeys(workload.tenants, 0.0)
    for node in trie.iterate():
        share = policy.pricing.prefill_per_token / len(node.tenants)
        for tenant in node.tenants:
            prefill[tenant] += share
    return _finish("shapley", prefill, workload, policy)


def _finish(
    scheme: str, prefill: dict[int, float], workload: Workload, policy: Policy
) -> Allocation:
    decode = decode_shares(workload, policy)
    shares = {tenant: prefill[tenant] + decode[tenant] for tenant in workload.tenants}
    return Allocation(
        scheme=scheme,
        shares=shares,
        prefill_shares=dict(prefill),
        prefill_total=sum(prefill.values()),
        decode_total=sum(decode.values()),
        order_independent=scheme in ORDER_INDEPENDENT,
    )


ALLOCATORS = {
    "per_request": per_request,
    "marginal": marginal,
    "equal_split": equal_split,
    "proportional": proportional,
    "shapley": shapley,
}


def allocate(
    scheme: str,
    workload: Workload,
    policy: Policy,
    trie: PrefixTrie,
    result: ServingResult,
) -> Allocation:
    if scheme not in ALLOCATORS:
        raise UsageError(f"unknown scheme {scheme!r}, expected one of {list(ALLOCATORS)}")
    return ALLOCATORS[scheme](workload, policy, trie, result)


def allocate_all(
    workload: Workload, policy: Policy, trie: PrefixTrie, result: ServingResult
) -> dict[str, Allocation]:
    return {name: allocate(name, workload, policy, trie, result) for name in SCHEMES}
