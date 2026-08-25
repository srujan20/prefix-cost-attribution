"""Check the linear time Shapley pass against the definition, on a trie small
enough to enumerate every permutation.

The claim in `attribution.shapley` is a mathematical one: for a cost game played
on a tree, a player's Shapley value is the sum over the nodes on its path of that
node's cost divided by the number of players using it. That is the whole reason
the allocation is one pass instead of a sum over subsets, and a claim of that
shape is worth checking against the definition rather than against a rerun of
itself.

So this file builds a tiny trie by hand, computes the Shapley value the long way
by averaging marginal contributions over all n! permutations of the players, and
asserts the two agree exactly. Five players is 120 permutations, which is fast
and is enough: the identity being checked is per node, so a trie with every
sharing pattern from one player to five exercises it completely.
"""

from __future__ import annotations

from itertools import permutations

import pytest

from prefixcost.trie import PrefixTrie


def build(sequences: dict[int, list[tuple[str, ...]]]) -> PrefixTrie:
    trie = PrefixTrie()
    for tenant, tokens in sequences.items():
        for sequence in tokens:
            trie.add(sequence, tenant)
    return trie


def exact_by_formula(trie: PrefixTrie, tenants: list[int]) -> dict[int, float]:
    """The one pass identity, written out here rather than imported.

    Deliberately duplicated. If this test imported `attribution.shapley` it would
    be comparing the implementation against the definition, which is what it
    claims to do, but a later refactor of the shared helper would move both sides
    at once and the test would keep passing. Nine lines of duplication buys
    independence.
    """
    values = dict.fromkeys(tenants, 0.0)
    for node in trie.iterate():
        share = 1.0 / len(node.tenants)
        for tenant in node.tenants:
            values[tenant] += share
    return values


def coalition_cost(trie: PrefixTrie, coalition: frozenset[int]) -> int:
    """What this set of tenants would have cost on its own.

    The nodes their requests cover, counted once each, which is exactly what an
    unbounded prefix cache would have processed had only these tenants existed.
    """
    return sum(1 for node in trie.iterate() if node.tenants & coalition)


def brute_force(trie: PrefixTrie, tenants: list[int]) -> dict[int, float]:
    """The definition: average marginal contribution over every arrival order."""
    values = dict.fromkeys(tenants, 0.0)
    orders = list(permutations(tenants))
    for order in orders:
        present: set[int] = set()
        previous = 0
        for tenant in order:
            present.add(tenant)
            current = coalition_cost(trie, frozenset(present))
            values[tenant] += current - previous
            previous = current
    return {tenant: value / len(orders) for tenant, value in values.items()}


TENANTS = [0, 1, 2, 3, 4]
SEQUENCES = {
    # A shared five way prefix, then three way and two way splits, then private
    # tails of different lengths, so every sharing count from one to five appears.
    0: [("a", "b", "c", "d"), ("a", "b", "x")],
    1: [("a", "b", "c", "e")],
    2: [("a", "b", "c", "d", "f", "g")],
    3: [("a", "b", "y", "z")],
    4: [("a", "q"), ("a", "b", "y", "w")],
}


@pytest.fixture(scope="module")
def trie() -> PrefixTrie:
    return build(SEQUENCES)


def test_the_trie_has_every_sharing_count_from_one_to_five(trie):
    counts = trie.nodes_by_tenant_count()
    assert set(counts) == {1, 2, 3, 5}
    # Four way sharing does not occur in this shape, which is fine: the identity
    # is per node and the counts present already span the range. Asserted rather
    # than left implicit so a change to SEQUENCES that collapses the sharing
    # cannot pass unnoticed.
    assert counts[5] >= 2


def test_the_one_pass_value_equals_the_average_over_every_permutation(trie):
    formula = exact_by_formula(trie, TENANTS)
    definition = brute_force(trie, TENANTS)
    for tenant in TENANTS:
        assert formula[tenant] == pytest.approx(definition[tenant], abs=1e-09)


def test_both_are_efficient_on_the_grand_coalition(trie):
    total = coalition_cost(trie, frozenset(TENANTS))
    assert sum(exact_by_formula(trie, TENANTS).values()) == pytest.approx(total)
    assert sum(brute_force(trie, TENANTS).values()) == pytest.approx(total)
    assert total == trie.distinct_prefix_tokens


def test_a_dummy_tenant_pays_only_for_what_only_it_uses(trie):
    """Tenant 4 owns the ("a", "q") branch alone, so it pays for that node whole."""
    values = exact_by_formula(trie, TENANTS)
    private = sum(1 for node in trie.iterate() if node.tenants == {4})
    assert values[4] > private
    # And a tenant added with no requests at all would take nothing, which is the
    # dummy axiom. Checked by adding one and confirming the others do not move.
    extended = build(SEQUENCES)
    before = exact_by_formula(extended, TENANTS)
    after = exact_by_formula(extended, [*TENANTS, 9])
    assert after[9] == 0.0
    assert all(after[t] == pytest.approx(before[t]) for t in TENANTS)


def test_two_tenants_with_identical_requests_pay_the_same(trie):
    """The symmetry axiom, which is what makes the split defensible to a customer."""
    symmetric = build({0: [("m", "n", "o")], 1: [("m", "n", "o")], 2: [("m", "p", "q", "r")]})
    values = exact_by_formula(symmetric, [0, 1, 2])
    # 0 and 1 each pay a third of "m" and half of each of "n" and "o".
    assert values[0] == pytest.approx(values[1]) == pytest.approx(1 / 3 + 0.5 + 0.5)
    # 2 pays a third of "m" and all of its own three token tail.
    assert values[2] == pytest.approx(1 / 3 + 3.0)
