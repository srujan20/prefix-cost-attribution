"""Sweep the cache capacity under two policies, and separate two questions.

"The cache is too small" and "the eviction policy is wrong" are different
complaints with different fixes, and a single prefill number cannot tell them
apart. The oracle policy separates them. It evicts whatever is needed furthest in
the future, which is not implementable because it reads the future, and that is
exactly why it is useful: at a given capacity it is the best any policy could do,
so the gap between it and LRU is the cost of the policy and the gap between it and
the unbounded anchor is the cost of the capacity.

The sweep also locates where the attribution conflict dissolves. Above the
workload's distinct prefix token count nothing is ever evicted, the fair schemes
sum to the bill exactly, and a reader can have all three properties at once. Below
it they cannot. That boundary is a number this experiment reports rather than an
argument, and it is the honest answer to "so which scheme should I use".

Usage:
    python experiments/exp03_the_policy_not_the_capacity.py [--seeds N]
"""

from __future__ import annotations

import argparse

from _shared import banner, policy, summary, workload_and_trie, write

from prefixcost.attribution import allocate_all
from prefixcost.config import SCHEMES
from prefixcost.serving import serve

NAME = "exp03-the-policy-not-the-capacity"
CAPACITIES = (0, 1000, 2000, 4000, 8000, 12000, 16000, 20000, 32000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=3)
    args = parser.parse_args(argv)

    settings = policy()
    shipped = settings.cache.capacity_tokens
    workload, trie = workload_and_trie(11)
    anchor = trie.distinct_prefix_tokens
    banner(f"sweeping {len(CAPACITIES) + 1} capacities under lru and oracle, anchor {anchor}")

    rows = []
    # The anchor capacity itself is in the sweep, because the claim that eviction
    # stops there is the point and a sweep that steps over it would be asserting
    # the claim rather than showing it.
    for capacity in sorted({*CAPACITIES, anchor}):
        row = {"capacity_tokens": capacity}
        for name in ("lru", "oracle"):
            served = serve(
                workload, settings, capacity_tokens=capacity, cache_policy=name, trie=trie
            )
            actual = served.cost(settings)
            allocations = allocate_all(workload, settings, trie, served)
            row[name] = {
                "prefill_tokens": served.prefill_tokens,
                "hit_share": served.hit_share,
                "evictions": served.evictions,
                "collects": {scheme: allocations[scheme].total / actual for scheme in SCHEMES},
                "is_a_bill": {scheme: allocations[scheme].sums_to(actual) for scheme in SCHEMES},
            }
        row["policy_excess_tokens"] = row["lru"]["prefill_tokens"] - row["oracle"]["prefill_tokens"]
        row["policy_excess_share"] = (
            row["policy_excess_tokens"] / row["oracle"]["prefill_tokens"]
            if row["oracle"]["prefill_tokens"]
            else 0.0
        )
        rows.append(row)

    at_shipped = next(row for row in rows if row["capacity_tokens"] == shipped)
    # The first capacity at which nothing is evicted at all. Not asserted to be
    # the anchor: measured, and then compared to it.
    zero_eviction = next(
        (
            row["capacity_tokens"]
            for row in rows
            if row["lru"]["evictions"] == 0 and row["capacity_tokens"] > 0
        ),
        None,
    )

    # Whether the oracle reaches the unbounded optimum, per seed, at the shipped
    # capacity. This is the sentence the experiment exists for.
    oracle_optimal = []
    for index in range(args.seeds):
        seed = 11 + index
        other, other_trie = workload_and_trie(seed)
        served = serve(
            other, settings, capacity_tokens=shipped, cache_policy="oracle", trie=other_trie
        )
        lru = serve(other, settings, capacity_tokens=shipped, cache_policy="lru", trie=other_trie)
        oracle_optimal.append(
            {
                "seed": seed,
                "distinct_prefix_tokens": other_trie.distinct_prefix_tokens,
                "oracle_prefill": served.prefill_tokens,
                "lru_prefill": lru.prefill_tokens,
                "oracle_is_optimal": served.prefill_tokens == other_trie.distinct_prefix_tokens,
                "lru_excess_share": lru.prefill_tokens / served.prefill_tokens - 1.0,
            }
        )

    # The capacity at which the fair scheme starts summing to the bill again.
    # Not the same as the capacity at which eviction stops, and the gap between
    # them is a real finding: evicting a node that is never needed again costs
    # nothing, so what breaks efficiency is recomputation, not eviction.
    first_bill = next(
        (row["capacity_tokens"] for row in rows if row["lru"]["is_a_bill"]["shapley"]), None
    )

    payload = {
        "seeds": args.seeds,
        "shipped_capacity": shipped,
        "distinct_prefix_tokens": anchor,
        "shipped_capacity_share_of_working_set": shipped / anchor,
        "rows": rows,
        "at_shipped_capacity": at_shipped,
        "first_zero_eviction_capacity": zero_eviction,
        "first_capacity_where_shapley_is_a_bill": first_bill,
        "evictions_at_that_capacity": next(
            row["lru"]["evictions"] for row in rows if row["capacity_tokens"] == first_bill
        ),
        "zero_eviction_capacity_is_the_anchor": zero_eviction == anchor,
        "oracle_at_shipped": oracle_optimal,
        "oracle_reaches_the_unbounded_optimum": all(
            item["oracle_is_optimal"] for item in oracle_optimal
        ),
        "lru_excess_share_at_shipped": summary(item["lru_excess_share"] for item in oracle_optimal),
    }
    write(NAME, payload)

    print()
    print(
        f"  {'capacity':>9}{'lru prefill':>13}{'oracle':>10}{'policy cost':>13}"
        f"{'evictions':>11}{'shapley is a bill':>20}"
    )
    for row in rows:
        print(
            f"  {row['capacity_tokens']:>9,}{row['lru']['prefill_tokens']:>13,}"
            f"{row['oracle']['prefill_tokens']:>10,}{row['policy_excess_share']:>13.4f}"
            f"{row['lru']['evictions']:>11,}"
            f"{row['lru']['is_a_bill']['shapley']!s:>20}"
        )
    print()
    print(f"  eviction stops at capacity {zero_eviction:,}, and the trie has {anchor:,} nodes")
    print(
        f"  the fair scheme is a bill again from capacity {first_bill:,}, where lru still "
        f"evicts {payload['evictions_at_that_capacity']:,} nodes: what breaks efficiency is "
        f"recomputing an evicted prefix, not evicting one"
    )
    print(
        f"  at the shipped {shipped:,}, the oracle reaches the unbounded optimum on "
        f"{sum(item['oracle_is_optimal'] for item in oracle_optimal)} of {args.seeds} workloads, "
        f"so the capacity is sufficient and lru is spending "
        f"{payload['lru_excess_share_at_shipped']['median']:.4f} more than it needs to"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
