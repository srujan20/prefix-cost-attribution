"""The prefix trie, which is where the exact quantities in this repository live.

A prefix cache with unbounded capacity processes each distinct prefix token
exactly once, whatever order the requests arrive in. So the total prefill work
under a perfect cache is the number of nodes in the trie of all request token
sequences, which is an integer, computable without simulating anything, and equal
to itself under every permutation of the workload.

That is the anchor. Every rate in this repository that is described as exact is
exact because it is a count of trie nodes rather than an average of simulated
runs.

The trie also carries, per node, the set of tenants whose requests pass through
it. That set is what makes a fair allocation computable: for a cost game played
on a tree, where a coalition pays for the nodes its paths cover, the Shapley value
of a player is the sum over the nodes on its path of that node's cost divided by
the number of players using the node. So the allocation that is usually described
as exponential to compute is a single pass over this structure, and
`attribution.py` does exactly that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import UnanswerableError


@dataclass
class Node:
    """One distinct prefix. `tenants` is who passes through it."""

    token: str
    depth: int
    children: dict[str, Node] = field(default_factory=dict)
    tenants: set[int] = field(default_factory=set)
    requests: int = 0
    terminal: int = 0


@dataclass
class PrefixTrie:
    """Every request's token sequence, sharing its prefixes.

    The root is a sentinel at depth zero and is not counted as a token, because
    it corresponds to no work: a prefill of nothing costs nothing.
    """

    root: Node = field(default_factory=lambda: Node(token="", depth=0))
    nodes: int = 0

    def add(self, tokens: tuple[str, ...], tenant: int) -> None:
        current = self.root
        current.tenants.add(tenant)
        current.requests += 1
        for depth, token in enumerate(tokens, start=1):
            child = current.children.get(token)
            if child is None:
                child = Node(token=token, depth=depth)
                current.children[token] = child
                self.nodes += 1
            child.tenants.add(tenant)
            child.requests += 1
            current = child
        current.terminal += 1

    def walk(self, tokens: tuple[str, ...]):
        """Yield each node along a token sequence, root excluded."""
        current = self.root
        for token in tokens:
            current = current.children.get(token)
            if current is None:
                raise UnanswerableError(
                    "a token sequence was walked that is not in the trie, so the two "
                    "are describing different workloads"
                )
            yield current

    @property
    def distinct_prefix_tokens(self) -> int:
        """Total prefill under a cache that never evicts. Exact, and an integer."""
        return self.nodes

    def shared_nodes(self) -> int:
        """Nodes used by more than one tenant, which is where attribution is a question."""
        return sum(1 for node in self.iterate() if len(node.tenants) > 1)

    def nodes_by_tenant_count(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for node in self.iterate():
            counts[len(node.tenants)] = counts.get(len(node.tenants), 0) + 1
        return dict(sorted(counts.items()))

    def iterate(self):
        """Every node except the root, in no particular order.

        Public because the attribution schemes walk it. A leading underscore
        would have said this was internal while three other modules used it,
        which is a worse lie than a slightly wider surface.
        """
        stack = [self.root]
        while stack:
            node = stack.pop()
            if node.depth > 0:
                yield node
            stack.extend(node.children.values())


def build_trie(requests) -> PrefixTrie:
    trie = PrefixTrie()
    for request in requests:
        trie.add(request.tokens, request.tenant)
    return trie
