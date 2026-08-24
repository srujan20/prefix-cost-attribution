"""The two exact anchors, the five schemes, and the refusals.

The first two tests are the anchors everything else is measured against, and both
are exact rather than approximate. An unbounded cache processes each distinct
prefix token once, so the prefill equals the trie's node count as an integer. No
cache at all processes every token of every request, so the prefill equals the
prompt token count as an integer. A repository whose central numbers are exact
should assert them as equalities rather than as tolerances, and these do.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from prefixcost.attribution import allocate, allocate_all
from prefixcost.audit import Verdict, audit, decide_verdict, measure_stability
from prefixcost.config import ORDER_INDEPENDENT, SCHEMES
from prefixcost.errors import UnanswerableError, UsageError
from prefixcost.serving import serve
from prefixcost.trie import build_trie
from prefixcost.workload import build_workload, causal_order, orderings

UNBOUNDED = 10**9


def test_an_unbounded_cache_processes_each_distinct_prefix_token_once(tiny_policy, workload, trie):
    """The anchor. An integer equality, not a tolerance."""
    served = serve(workload, tiny_policy, capacity_tokens=UNBOUNDED, trie=trie)
    assert served.prefill_tokens == trie.distinct_prefix_tokens
    assert served.evictions == 0


def test_no_cache_processes_every_token_of_every_request(tiny_policy, workload):
    """The anchor in the other direction, and the one that makes two schemes agree."""
    served = serve(workload, tiny_policy, cache_policy="none")
    assert served.prefill_tokens == workload.prompt_tokens
    assert served.cached_tokens == 0


def test_with_no_cache_the_marginal_and_per_request_schemes_agree_exactly(
    tiny_policy, workload, trie
):
    served = serve(workload, tiny_policy, cache_policy="none", trie=trie)
    allocations = allocate_all(workload, tiny_policy, trie, served)
    for tenant in workload.tenants:
        assert allocations["marginal"].shares[tenant] == pytest.approx(
            allocations["per_request"].shares[tenant]
        )


def test_a_capacity_of_zero_is_the_same_as_no_cache(tiny_policy, workload, trie):
    zero = serve(workload, tiny_policy, capacity_tokens=0, trie=trie)
    none = serve(workload, tiny_policy, cache_policy="none", trie=trie)
    assert zero.prefill_tokens == none.prefill_tokens


def test_more_capacity_never_costs_more_prefill(tiny_policy, workload, trie):
    """Monotonicity, which would catch an eviction bug that keeps the wrong node."""
    previous = None
    for capacity in (500, 2000, 8000, UNBOUNDED):
        served = serve(workload, tiny_policy, capacity_tokens=capacity, trie=trie)
        if previous is not None:
            assert served.prefill_tokens <= previous
        previous = served.prefill_tokens


def test_the_oracle_is_never_worse_than_lru_at_the_same_capacity(tiny_policy, workload, trie):
    """What the oracle is for: separating the cost of a policy from the cost of a size."""
    capacity = 1500
    lru = serve(workload, tiny_policy, capacity_tokens=capacity, trie=trie)
    oracle = serve(
        workload, tiny_policy, capacity_tokens=capacity, cache_policy="oracle", trie=trie
    )
    assert oracle.prefill_tokens <= lru.prefill_tokens


def test_marginal_always_sums_to_what_the_server_spent(tiny_policy, workload, trie):
    """Efficiency, which is the one property marginal has and is why it survives."""
    for capacity in (0, 1500, UNBOUNDED):
        served = serve(workload, tiny_policy, capacity_tokens=capacity, trie=trie)
        allocation = allocate("marginal", workload, tiny_policy, trie, served)
        assert allocation.sums_to(served.cost(tiny_policy))


def test_shapley_sums_to_the_bill_when_the_cache_never_evicts(tiny_policy, workload, trie):
    """And the qualification in that name is the whole tension.

    With an unbounded cache the server's prefill is the trie's node count, which
    is exactly what the Shapley allocation divides. Add eviction and the server
    spends more than the trie total on work no tenant's request caused alone, so
    the fair split stops being a division of the bill.
    """
    served = serve(workload, tiny_policy, capacity_tokens=UNBOUNDED, trie=trie)
    allocation = allocate("shapley", workload, tiny_policy, trie, served)
    assert allocation.sums_to(served.cost(tiny_policy))

    evicting = serve(workload, tiny_policy, capacity_tokens=800, trie=trie)
    assert evicting.evictions > 0
    assert not allocate("shapley", workload, tiny_policy, trie, evicting).sums_to(
        evicting.cost(tiny_policy)
    )


def test_per_request_over_attributes_whenever_the_cache_hits(tiny_policy, workload, trie):
    served = serve(workload, tiny_policy, capacity_tokens=UNBOUNDED, trie=trie)
    assert served.hit_share > 0
    allocation = allocate("per_request", workload, tiny_policy, trie, served)
    assert allocation.total > served.cost(tiny_policy)


def test_the_order_independent_schemes_are_exactly_stable(tiny_policy, workload, trie):
    """Exactly, not nearly. A construction meant to give zero is worth checking."""
    stability = measure_stability(workload, tiny_policy, trie, orderings_count=6)
    for name in ORDER_INDEPENDENT:
        assert stability[name].max_relative_spread == 0.0, name


def test_the_marginal_scheme_is_not_stable(tiny_policy, workload, trie):
    """Asserted in the direction that can fail, so a broken permuter is noticed."""
    stability = measure_stability(workload, tiny_policy, trie, orderings_count=6)
    assert stability["marginal"].max_relative_spread > 0.0


def test_every_scheme_agrees_about_decode(tiny_policy, workload, trie):
    """Decode is never shared, so a disagreement about it would be a bug."""
    served = serve(workload, tiny_policy, trie=trie)
    allocations = allocate_all(workload, tiny_policy, trie, served)
    totals = {allocation.decode_total for allocation in allocations.values()}
    assert len(totals) == 1


def test_shapley_gives_a_shared_prefix_to_its_users_and_nobody_else(tiny_policy):
    """The fairness property, on a workload built so the answer is checkable by hand."""
    policy = replace(
        tiny_policy,
        workload=replace(
            tiny_policy.workload,
            tenants=2,
            prompt_families=2,
            conversations_per_tenant=1,
            turns_per_conversation=1,
        ),
    )
    workload = build_workload(policy, seed=1)
    trie = build_trie(workload.requests)
    served = serve(workload, policy, capacity_tokens=UNBOUNDED, trie=trie)
    allocation = allocate("shapley", workload, policy, trie, served)
    # Two tenants on different families share only the opening tokens, so neither
    # can be paying for the whole of the other's prompt.
    for tenant in workload.tenants:
        assert 0 < allocation.prefill_shares[tenant] < trie.distinct_prefix_tokens


def test_the_three_verdicts_are_all_reachable(tiny_policy, workload):
    verdicts = {
        audit(workload, tiny_policy, scheme=scheme, orderings_count=6).verdict
        for scheme in ("shapley", "marginal", "per_request")
    }
    assert verdicts == {Verdict.SOUND, Verdict.ORDER_DEPENDENT, Verdict.NOT_AN_ATTRIBUTION}


def test_not_being_a_bill_outranks_being_unstable(tiny_policy, workload, trie):
    """Ordering: an unstable division of the wrong total is not a finding about order."""
    served = serve(workload, tiny_policy, trie=trie)
    allocations = allocate_all(workload, tiny_policy, trie, served)
    stability = measure_stability(workload, tiny_policy, trie, orderings_count=4)
    assert (
        decide_verdict("per_request", allocations, stability, served.cost(tiny_policy), tiny_policy)
        is Verdict.NOT_AN_ATTRIBUTION
    )


def test_every_ordering_contains_every_request(tiny_policy, workload):
    """A permuter that dropped or duplicated a request would make the spread a fiction."""
    reference = sorted(causal_order(workload))
    for sequence in orderings(workload, 5, seed=2):
        assert sorted(sequence) == reference


def test_turns_keep_their_order_inside_a_conversation(tiny_policy, workload):
    """A conversation received backwards would flatter the cache and never happens."""
    for sequence in orderings(workload, 5, seed=2):
        seen: dict[tuple[int, int], int] = {}
        for index in sequence:
            request = workload.requests[index]
            key = (request.tenant, request.conversation)
            assert request.turn > seen.get(key, -1)
            seen[key] = request.turn


def test_an_unknown_scheme_is_refused(tiny_policy, workload, trie):
    served = serve(workload, tiny_policy, trie=trie)
    with pytest.raises(UsageError, match="unknown scheme"):
        allocate("vibes", workload, tiny_policy, trie, served)


def test_an_unknown_cache_policy_is_refused(tiny_policy, workload):
    with pytest.raises(UsageError, match="unknown cache policy"):
        serve(workload, tiny_policy, cache_policy="wishful")


def test_a_negative_capacity_is_refused(tiny_policy, workload):
    with pytest.raises(UsageError, match="cannot be negative"):
        serve(workload, tiny_policy, capacity_tokens=-5)


def test_a_single_tenant_workload_is_refused(tiny_policy):
    """Refused by the workload type rather than by the policy loader.

    The policy loader also refuses fewer than two tenants, so this path is only
    reachable by constructing the policy directly, which a caller embedding this
    package can do. Both guards exist and this asserts the one that is closest to
    the data.
    """
    single = replace(tiny_policy, workload=replace(tiny_policy.workload, tenants=1))
    with pytest.raises(UnanswerableError, match="nothing to attribute"):
        build_workload(single, seed=1)


def test_an_empty_workload_is_refused(workload):
    with pytest.raises(UnanswerableError, match="no requests"):
        replace(workload, requests=())


def test_the_scheme_registry_matches_the_declared_names():
    from prefixcost.attribution import ALLOCATORS

    assert set(ALLOCATORS) == set(SCHEMES)


def test_two_audits_of_the_same_seed_agree(tiny_policy):
    first = audit(build_workload(tiny_policy, seed=4), tiny_policy, orderings_count=4)
    second = audit(build_workload(tiny_policy, seed=4), tiny_policy, orderings_count=4)
    assert first.as_dict() == second.as_dict()
