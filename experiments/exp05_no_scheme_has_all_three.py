"""Check the three properties directly, across configurations, and find the boundary.

Everything before this measures one workload at one capacity. The obvious
objection is that the conflict is an artefact of that arrangement, so this
experiment states the three properties as tests and runs them over a grid.

Efficient: the shares sum to what the server spent, to one part in a million.
Order independent: the shares are identical, to the last bit, under every replay.
Fair: the shares equal the exact Shapley value of the cost game on the trie.

The grid varies the tenant count, how many prompt families they share, and the
cache capacity. The workloads are deliberately smaller than the shipped one, so
the grid finishes in a minute rather than an hour; the properties being checked
are exact rather than statistical, so nothing about them needs a large sample.

The result is a boundary rather than a slogan. There is a condition under which
one scheme has all three, and this experiment reports what that condition is,
which is more useful to somebody choosing a billing scheme than an impossibility
theorem would be.

Usage:
    python experiments/exp05_no_scheme_has_all_three.py [--orderings N]
"""

from __future__ import annotations

import argparse
import dataclasses

from _shared import banner, policy, rate_dict, write

from prefixcost.attribution import allocate_all
from prefixcost.config import SCHEMES
from prefixcost.serving import serve
from prefixcost.trie import build_trie
from prefixcost.workload import build_workload, orderings

NAME = "exp05-no-scheme-has-all-three"

SHAPES = (
    {"tenants": 8, "prompt_families": 2},
    {"tenants": 12, "prompt_families": 4},
    {"tenants": 24, "prompt_families": 6},
    {"tenants": 30, "prompt_families": 3},
)
CAPACITY_FRACTIONS = (0.08, 0.25, 0.6, 0.9, 1.0, 2.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orderings", type=int, default=6)
    args = parser.parse_args(argv)

    base = policy()
    banner(
        f"{len(SHAPES)} shapes by {len(CAPACITY_FRACTIONS)} capacities, {args.orderings} replays"
    )

    cells = []
    all_three = dict.fromkeys(SCHEMES, 0)
    all_three_when_recomputing = dict.fromkeys(SCHEMES, 0)
    recomputing_cells = 0

    for shape in SHAPES:
        settings = dataclasses.replace(
            base,
            workload=dataclasses.replace(
                base.workload,
                tenants=shape["tenants"],
                prompt_families=shape["prompt_families"],
                # Smaller than the shipped workload. The properties are exact, so
                # the grid does not need the shipped size to answer the question.
                conversations_per_tenant=3,
                turns_per_conversation=4,
            ),
        )
        workload = build_workload(settings, seed=11)
        trie = build_trie(workload.requests)
        anchor = trie.distinct_prefix_tokens
        sequences = orderings(workload, args.orderings, 11)

        for fraction in CAPACITY_FRACTIONS:
            capacity = max(1, round(anchor * fraction))
            runs = [
                serve(workload, settings, sequence, capacity_tokens=capacity, trie=trie)
                for sequence in sequences
            ]
            allocations = [allocate_all(workload, settings, trie, run) for run in runs]
            actual = runs[0].cost(settings)

            # Whether the cache ever had to recompute a prefix it had already
            # computed. That is the condition, and it is not the same as whether
            # it evicted: an eviction of something never needed again is free.
            recomputed = runs[0].prefill_tokens > anchor
            if recomputed:
                recomputing_cells += 1

            cell = {
                "tenants": shape["tenants"],
                "prompt_families": shape["prompt_families"],
                "capacity_fraction": fraction,
                "capacity_tokens": capacity,
                "distinct_prefix_tokens": anchor,
                "prefill_tokens": runs[0].prefill_tokens,
                "evictions": runs[0].evictions,
                "recomputed_a_cached_prefix": recomputed,
                "schemes": {},
            }
            for name in SCHEMES:
                shares = [item[name].prefill_shares for item in allocations]
                fair = allocations[0]["shapley"].prefill_shares
                efficient = all(item[name].sums_to(actual) for item in allocations)
                stable = all(row == shares[0] for row in shares)
                equals_fair = all(
                    abs(shares[0][t] - fair[t]) <= 1e-09 * max(1.0, abs(fair[t]))
                    for t in workload.tenants
                )
                cell["schemes"][name] = {
                    "efficient": efficient,
                    "order_independent": stable,
                    "fair": equals_fair,
                    "all_three": efficient and stable and equals_fair,
                    "collects": allocations[0][name].total / actual,
                }
                if cell["schemes"][name]["all_three"]:
                    all_three[name] += 1
                    if recomputed:
                        all_three_when_recomputing[name] += 1
            cells.append(cell)

    total = len(cells)
    payload = {
        "orderings": args.orderings,
        "shapes": list(SHAPES),
        "capacity_fractions": list(CAPACITY_FRACTIONS),
        "cells": cells,
        "cells_total": total,
        "cells_recomputing": recomputing_cells,
        "all_three": {name: rate_dict(all_three[name], total) for name in SCHEMES},
        "all_three_when_recomputing": {
            name: rate_dict(all_three_when_recomputing[name], recomputing_cells) for name in SCHEMES
        },
        "boundary": (
            "a scheme has all three properties exactly when the cache never recomputes a "
            "prefix it had already computed"
        ),
        "boundary_holds": all(all_three_when_recomputing[name] == 0 for name in SCHEMES)
        and all(
            cell["schemes"]["shapley"]["all_three"]
            for cell in cells
            if not cell["recomputed_a_cached_prefix"]
        ),
    }
    write(NAME, payload)

    print()
    print(f"  {'scheme':<14}{'all three':>12}{'when recomputing':>20}")
    for name in SCHEMES:
        print(
            f"  {name:<14}{all_three[name]:>6} /{total:>4}"
            f"{all_three_when_recomputing[name]:>13} /{recomputing_cells:>5}"
        )
    print()
    print(
        f"  {recomputing_cells} of {total} cells recompute a cached prefix, and in those "
        f"no scheme has all three"
    )
    print(
        f"  in the other {total - recomputing_cells}, shapley has all three in every one: "
        f"{payload['boundary_holds']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
