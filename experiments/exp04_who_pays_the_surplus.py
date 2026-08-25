"""The per request bill over-collects. This is about who it over-collects from.

A scheme that charges 1.84 times what the server spent is not merely wrong by a
factor. The factor is an average, and averages hide the only thing a customer
cares about, which is their own line. This experiment takes the fair split as the
reference and asks, tenant by tenant, how far each scheme puts them from it.

The reference is Shapley rather than "what they used", because "what they used"
is the question. A tenant whose requests pass through a prefix shared with five
others used that prefix, and so did the five, and the whole difficulty is that
the server paid for it once.

Two correlations are computed, and they are the point of the experiment. If the
over-charge tracks a tenant's own volume, the scheme is crude but defensible: a
heavy user pays more. If it tracks where the tenant's requests happened to land
in the arrival order, the scheme is charging for scheduling, and no explanation
of that survives contact with a customer.

A sharper version of the same question is asked alongside it. Tenants sharing a
prompt family share its opening tokens, so exactly one of them pays to compute
them in any given replay: whichever arrives first. So for each family in each
replay, the experiment checks whether the tenant that arrived first is the one
charged the most. Under a scheme that is a bill it happens far more often than
chance, and chance here is one in four, because that is how many tenants share a
family in this workload.

The arrival correlation is computed inside each ordering rather than across them,
and the first version of this experiment got that wrong. Averaging a tenant's
position over twelve orderings gives every tenant almost the same average, so the
correlation came out at 0.0013 and appeared to exonerate the order dependent
scheme that exp02 had just caught swinging a bill by half. The question is
whether a tenant who is early *in a given replay* is charged more *in that
replay*, and it has to be asked one replay at a time.

Spearman rather than Pearson because neither relationship has any reason to be
linear, and rank correlation is what "moves together" means when the shape is
unknown. Written out here rather than imported, because scipy is not a dependency
of this repository and a rank correlation is nine lines.

Usage:
    python experiments/exp04_who_pays_the_surplus.py [--seeds N] [--orderings N]
"""

from __future__ import annotations

import argparse

import numpy as np
from _shared import banner, policy, rate_dict, summary, workload_and_trie, write

from prefixcost.attribution import allocate_all
from prefixcost.config import SCHEMES
from prefixcost.serving import serve
from prefixcost.workload import orderings

NAME = "exp04-who-pays-the-surplus"


def spearman(left, right) -> float | None:
    """Rank correlation, with ties given their average rank.

    Returns None rather than a nan when either side is constant, because a
    constant has no ranks to correlate and because a nan in a JSON file is a
    parser dependent token that a reader would have to guess the meaning of.
    """
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if a.size < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(_ranks(a), _ranks(b))[0, 1])


def _ranks(values: np.ndarray) -> np.ndarray:
    order = values.argsort()
    ranks = np.empty(values.size, dtype=float)
    ranks[order] = np.arange(1, values.size + 1, dtype=float)
    # Average the ranks inside each group of equal values, so a tie does not
    # become an arbitrary ordering that the correlation then reads as signal.
    for value in np.unique(values):
        mask = values == value
        ranks[mask] = ranks[mask].mean()
    return ranks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--orderings", type=int, default=12)
    args = parser.parse_args(argv)

    settings = policy()
    banner(f"{args.seeds} workloads at capacity {settings.cache.capacity_tokens:,}")

    ratios: dict[str, list[float]] = {name: [] for name in SCHEMES}
    volume_correlations: dict[str, list[float]] = {name: [] for name in SCHEMES}
    position_correlations: dict[str, list[float]] = {name: [] for name in SCHEMES}
    worst = {"scheme": "", "seed": 0, "tenant": 0, "ratio": 1.0, "fair": 0.0, "charged": 0.0}
    surplus_shares = []
    first_pays_most: dict[str, int] = dict.fromkeys(SCHEMES, 0)
    family_trials = 0
    family_size = 0

    for index in range(args.seeds):
        seed = 11 + index
        workload, trie = workload_and_trie(seed)
        tenants = workload.tenants

        volume = dict.fromkeys(tenants, 0)
        for request in workload.requests:
            volume[request.tenant] += request.prompt_tokens

        result = serve(workload, settings, trie=trie)
        actual = result.cost(settings)
        allocations = allocate_all(workload, settings, trie, result)
        fair = allocations["shapley"].prefill_shares
        surplus_shares.append(allocations["per_request"].total / actual - 1.0)

        # One replay at a time: how early each tenant was in *this* order, against
        # what *this* order charged it.
        for sequence in orderings(workload, args.orderings, seed):
            positions: dict[int, list[int]] = {tenant: [] for tenant in tenants}
            for position, request_index in enumerate(sequence):
                positions[workload.requests[request_index].tenant].append(position)
            mean_position = [float(np.mean(positions[t])) for t in tenants]
            replayed = serve(workload, settings, sequence, trie=trie)
            replayed_allocations = allocate_all(workload, settings, trie, replayed)
            for name, allocation in replayed_allocations.items():
                shares = allocation.prefill_shares
                position_correlations[name].append(
                    spearman(mean_position, [shares[t] for t in tenants])
                )

            # Who arrived first in each prompt family, and who paid the most.
            first_arrival: dict[int, int] = {}
            for position, request_index in enumerate(sequence):
                request = workload.requests[request_index]
                first_arrival.setdefault(request.tenant, position)
            for family in sorted({r.prompt_family for r in workload.requests}):
                members = sorted({r.tenant for r in workload.requests if r.prompt_family == family})
                if len(members) < 2:
                    continue
                family_trials += 1
                family_size = len(members)
                earliest = min(members, key=lambda t: first_arrival[t])
                for name, allocation in replayed_allocations.items():
                    shares = allocation.prefill_shares
                    if max(members, key=lambda t, s=shares: s[t]) == earliest:
                        first_pays_most[name] += 1

        for name in SCHEMES:
            shares = allocations[name].prefill_shares
            per_tenant = [shares[t] / fair[t] for t in tenants]
            ratios[name].extend(per_tenant)
            volume_correlations[name].append(spearman([volume[t] for t in tenants], per_tenant))
            for tenant, ratio in zip(tenants, per_tenant, strict=True):
                if abs(ratio - 1.0) > abs(worst["ratio"] - 1.0):
                    worst = {
                        "scheme": name,
                        "seed": seed,
                        "tenant": tenant,
                        "ratio": ratio,
                        "fair": fair[tenant],
                        "charged": shares[tenant],
                    }

    payload = {
        "seeds": args.seeds,
        "orderings": args.orderings,
        "capacity_tokens": settings.cache.capacity_tokens,
        "reference": "shapley",
        "ratio_to_fair": {name: summary(ratios[name]) for name in SCHEMES},
        "spread_of_ratio": {name: max(ratios[name]) - min(ratios[name]) for name in SCHEMES},
        "correlation_with_own_volume": {
            name: summary(volume_correlations[name]) for name in SCHEMES
        },
        "correlation_with_arrival_position": {
            name: summary(position_correlations[name]) for name in SCHEMES
        },
        "arrival_correlation_replays": args.seeds * args.orderings,
        "family_trials": family_trials,
        "tenants_per_family": family_size,
        "chance_first_pays_most": 1.0 / family_size if family_size else None,
        "first_arriver_pays_most": {
            name: rate_dict(first_pays_most[name], family_trials) for name in SCHEMES
        },
        "surplus_share": summary(surplus_shares),
        "worst_single_tenant": worst,
    }
    write(NAME, payload)

    print()
    print(
        f"  {'scheme':<14}{'median x fair':>15}{'worst x fair':>14}"
        f"{'r with volume':>15}{'r with arrival':>16}"
    )
    for name in SCHEMES:
        stats = payload["ratio_to_fair"][name]
        extreme = max((stats["min"], stats["max"]), key=lambda value: abs(value - 1.0))
        volume_r = payload["correlation_with_own_volume"][name]
        arrival_r = payload["correlation_with_arrival_position"][name]
        print(
            f"  {name:<14}{stats['median']:>15.4f}{extreme:>14.4f}"
            f"{_render(volume_r):>15}{_render(arrival_r):>16}"
        )
    print()
    print(
        f"  the per request bill collects {payload['surplus_share']['median']:.4f} more than "
        f"the server spent, and nobody is credited with it"
    )
    print(
        f"  in a prompt family of {family_size}, chance would put the first arriver top "
        f"{1 / family_size:.4f} of the time; marginal does it "
        f"{payload['first_arriver_pays_most']['marginal']['value']:.4f} "
        f"over {family_trials} family replays, and shapley "
        f"{payload['first_arriver_pays_most']['shapley']['value']:.4f}"
    )
    print(
        f"  worst line: {worst['scheme']} charged tenant {worst['tenant']} "
        f"{worst['charged']:,.0f} against a fair {worst['fair']:,.0f}, "
        f"which is {worst['ratio']:.4f} times"
    )
    return 0


def _render(stats) -> str:
    return "not defined" if stats is None else f"{stats['median']:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
