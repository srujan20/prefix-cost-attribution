"""Replay one workload in many arrival orders and watch a tenant's bill move.

The requests are identical in every replay. Same tenants, same prompts, same
outputs, same total. Only the order in which they reached the server changes, and
conversations keep their internal turn order because a server does not receive a
conversation backwards.

For four of the five schemes the shares do not move at all. Not nearly, exactly:
they are functions of the request set rather than of the replay, so the spread is
0.0000 and the zero is a count rather than a rounded estimate.

For `marginal`, the scheme a cache aware biller arrives at by accident, a tenant's
prefill share moves by a large fraction of its own value. Whoever arrives first
with a shared system prompt pays to compute it and everyone behind them gets it
free, so the bill is partly a record of scheduling.

The unit of observation is one tenant in one workload, because that is the unit
a customer complains about. The rate published is the share of those tenants
whose bill moves past the configured materiality threshold across the orderings,
with a Wilson interval, since the zeros are the claims most easily published
carelessly.

Usage:
    python experiments/exp02_the_same_usage_two_bills.py [--orderings N] [--seeds N]
"""

from __future__ import annotations

import argparse

from _shared import banner, policy, rate_dict, summary, workload_and_trie, write

from prefixcost.attribution import allocate_all
from prefixcost.config import ORDER_INDEPENDENT, SCHEMES
from prefixcost.serving import serve
from prefixcost.workload import orderings

NAME = "exp02-the-same-usage-two-bills"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orderings", type=int, default=12)
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)

    settings = policy()
    threshold = settings.attribution.material_share_shift
    banner(
        f"{args.seeds} workloads, {args.orderings} arrival orders each, materiality {threshold:.4f}"
    )

    # Per scheme: every tenant's spread, in every workload. One row per tenant
    # rather than per workload, because a tenant is the unit that receives an
    # invoice and therefore the unit that can be charged unfairly.
    tenant_spreads: dict[str, list[float]] = {name: [] for name in SCHEMES}
    worst_per_seed: dict[str, list[float]] = {name: [] for name in SCHEMES}
    totals_hold: dict[str, int] = dict.fromkeys(SCHEMES, 0)
    # What each scheme collects as a fraction of what the server spent. The
    # boolean above answers "is this a bill" at a tolerance of one part in a
    # million; this answers "and if not, by how much", because a scheme that is
    # short by a third of a percent and a scheme that is short by half are both
    # false under the boolean and are not the same problem.
    collects: dict[str, list[float]] = {name: [] for name in SCHEMES}
    total_replays = 0
    widest = {"scheme": "", "seed": 0, "tenant": 0, "spread": 0.0, "low": 0.0, "high": 0.0}

    for index in range(args.seeds):
        seed = 11 + index
        workload, trie = workload_and_trie(seed)
        tenants = workload.tenants

        # One row per ordering, per scheme: that ordering's prefill share for
        # every tenant.
        collected: dict[str, list[list[float]]] = {name: [] for name in SCHEMES}
        for sequence in orderings(workload, args.orderings, seed):
            result = serve(workload, settings, sequence, trie=trie)
            actual = result.cost(settings)
            for name, allocation in allocate_all(workload, settings, trie, result).items():
                collected[name].append([allocation.prefill_shares[t] for t in tenants])
                collects[name].append(allocation.total / actual)
                if allocation.sums_to(actual):
                    totals_hold[name] += 1
            total_replays += 1

        for name in SCHEMES:
            columns = list(zip(*collected[name], strict=True))
            per_tenant = []
            for values in columns:
                mean = sum(values) / len(values)
                per_tenant.append((max(values) - min(values)) / mean if mean else 0.0)
            tenant_spreads[name].extend(per_tenant)
            worst = max(range(len(per_tenant)), key=lambda i, p=per_tenant: p[i])
            worst_per_seed[name].append(per_tenant[worst])
            if per_tenant[worst] > widest["spread"]:
                values = columns[worst]
                widest = {
                    "scheme": name,
                    "seed": seed,
                    "tenant": tenants[worst],
                    "spread": per_tenant[worst],
                    "low": min(values),
                    "high": max(values),
                }

    observations = args.seeds * len(workload_and_trie(11)[0].tenants)
    payload = {
        "seeds": args.seeds,
        "orderings": args.orderings,
        "tenant_observations": observations,
        "materiality": threshold,
        "order_independent_by_construction": list(ORDER_INDEPENDENT),
        "spread": {name: summary(tenant_spreads[name]) for name in SCHEMES},
        "worst_tenant_spread": {name: summary(worst_per_seed[name]) for name in SCHEMES},
        "exactly_zero_spread": {
            name: rate_dict(sum(1 for v in tenant_spreads[name] if v == 0.0), observations)
            for name in SCHEMES
        },
        "moved_past_materiality": {
            name: rate_dict(sum(1 for v in tenant_spreads[name] if v > threshold), observations)
            for name in SCHEMES
        },
        "sums_to_the_bill": {name: rate_dict(totals_hold[name], total_replays) for name in SCHEMES},
        "collects": {name: summary(collects[name]) for name in SCHEMES},
        "widest_single_tenant": widest,
        "headline_spread": max(worst_per_seed["marginal"]),
    }
    write(NAME, payload)

    print()
    print(
        f"  {'scheme':<14}{'median spread':>15}{'worst':>10}"
        f"{'exactly 0':>12}{'is a bill':>12}{'collects':>11}"
    )
    for name in SCHEMES:
        zero = payload["exactly_zero_spread"][name]
        print(
            f"  {name:<14}{payload['spread'][name]['median']:>15.4f}"
            f"{payload['worst_tenant_spread'][name]['max']:>10.4f}"
            f"{zero['successes']:>8} /{zero['trials']:>3}"
            f"{payload['sums_to_the_bill'][name]['value']:>12.4f}"
            f"{payload['collects'][name]['median']:>11.4f}"
        )
    print()
    print(
        f"  widest single tenant: {widest['scheme']} charged tenant {widest['tenant']} "
        f"{widest['low']:,.0f} in one order and {widest['high']:,.0f} in another, "
        f"for identical usage"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
