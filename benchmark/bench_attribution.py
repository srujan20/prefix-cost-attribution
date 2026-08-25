"""Time the exact Shapley pass against a sampled one, and price the difference.

The claim this repository makes about Shapley is that it is cheap here, because
the cost game is played on a tree and a tree turns the usual exponential sum into
a single pass over the nodes. That is a performance claim, so it gets a
measurement rather than an assurance.

The comparison is against what somebody would otherwise do: sample permutations,
average the marginal contributions, and call the result a Shapley value. That
implementation is written out below rather than referenced, so the two are timed
on the same workload in the same process, and so a reader can see that the
sampled one is implemented efficiently rather than strawmanned. Each permutation
costs one pass over the covered node set, which is the best the sampling approach
can do.

The accuracy sweep is the second half and matters more than the timing. A sampled
Shapley value carries a Monte Carlo error into a customer's invoice, and the
sweep reports how many permutations are needed before that error falls under the
materiality threshold the tool already uses for ordering. The sweep repeats each
sample count under five seeds, because a single seed produced a sequence in which
a thousand permutations were worse than fifty, and publishing that as a
convergence curve would have been an accident of one draw. Over five seeds the
worst case error does fall with the sample count, and the number that matters is
still uncomfortable: the smallest sample that keeps every tenant inside the
materiality threshold costs a couple of hundred times the exact pass and is still
wrong, whereas the exact pass is wrong by nothing and returns the same number
twice.

Sizes are in requests, because that is what a team knows about their own traffic.
The distinct prefix token count each size produced is recorded beside it, since
that is what both implementations actually walk.

Usage:
    python benchmark/bench_attribution.py [--repeats 7]
        [--out benchmark/results/attribution_latency.json]
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from prefixcost.attribution import shapley  # noqa: E402
from prefixcost.config import load_policy  # noqa: E402
from prefixcost.serving import serve  # noqa: E402
from prefixcost.trie import PrefixTrie, build_trie  # noqa: E402
from prefixcost.workload import Workload, build_workload  # noqa: E402

CONVERSATION_SIZES = (1, 2, 4, 8)
SAMPLE_COUNTS = (10, 50, 200, 1000)
ACCURACY_SEEDS = (5, 6, 7, 8, 9)
BENCH_SAMPLES = 200
DEFAULT_OUT = REPO / "benchmark/results/attribution_latency.json"


def sampled_shapley(
    workload: Workload, policy, trie: PrefixTrie, permutations: int, seed: int
) -> dict[int, float]:
    """Shapley by sampling permutations, implemented as well as it can be.

    For each permutation the tenants are added one at a time and each is credited
    with the nodes its requests cover that nobody before it had covered. That is
    the marginal contribution, and because every node is credited at most once per
    permutation the whole permutation costs one pass rather than one pass per
    tenant.

    This is the honest competitor. It is not slow because it was written badly, it
    is slow because it needs many permutations to be accurate, and that is the
    comparison the benchmark is for.
    """
    tenants = workload.tenants
    totals = dict.fromkeys(tenants, 0.0)
    nodes = list(trie.iterate())
    rng = np.random.default_rng(seed)
    price = policy.pricing.prefill_per_token

    for _ in range(permutations):
        order = rng.permutation(len(tenants))
        position = {tenants[index]: rank for rank, index in enumerate(order)}
        for node in nodes:
            # The first tenant in this permutation that touches the node pays for
            # it, which is exactly its marginal contribution here.
            first = min(node.tenants, key=lambda tenant: position[tenant])
            totals[first] += price
    return {tenant: value / permutations for tenant, value in totals.items()}


def percentile(values: list[float], fraction: float) -> float:
    """Nearest rank percentile, so every reported duration is one that happened."""
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def repeat(function, repeats: int) -> list[float]:
    """Times `repeats` calls after one untimed warm up.

    Stated rather than hidden. The first call touches pages the allocator has not
    faulted in yet, and including it made the first sample several times the
    median, which then became the p95 of a small sample set.
    """
    function()
    durations = []
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        durations.append((time.perf_counter() - start) * 1000.0)
    return durations


def measure(repeats: int) -> dict[str, object]:
    policy = load_policy(REPO / "configs/policy.yaml")
    rows = []
    for conversations in CONVERSATION_SIZES:
        sized = replace(
            policy, workload=replace(policy.workload, conversations_per_tenant=conversations)
        )
        workload = build_workload(sized, seed=11)
        trie = build_trie(workload.requests)
        result = serve(workload, sized, trie=trie)

        exact_ms = repeat(
            lambda w=workload, p=sized, t=trie, r=result: shapley(w, p, t, r), repeats
        )
        sampled_ms = repeat(
            lambda w=workload, p=sized, t=trie: sampled_shapley(w, p, t, BENCH_SAMPLES, 5),
            max(1, repeats // 3),
        )
        exact_p50 = statistics.median(exact_ms)
        sampled_p50 = statistics.median(sampled_ms)
        rows.append(
            {
                "conversations_per_tenant": conversations,
                "requests": len(workload.requests),
                "prompt_tokens": workload.prompt_tokens,
                "distinct_prefix_tokens": trie.distinct_prefix_tokens,
                "exact_p50_ms": round(exact_p50, 3),
                "exact_p95_ms": round(percentile(exact_ms, 0.95), 3),
                "sampled_p50_ms": round(sampled_p50, 3),
                "sampled_over_exact": round(sampled_p50 / exact_p50, 1),
            }
        )
        print(
            f"  {rows[-1]['requests']:>6} requests"
            f"  {rows[-1]['distinct_prefix_tokens']:>8} prefix tokens"
            f"  exact {exact_p50:>8.3f} ms"
            f"  sampled {sampled_p50:>10.2f} ms"
            f"  ({rows[-1]['sampled_over_exact']:.1f}x)"
        )

    # Accuracy, on the shipped workload size, against the exact answer.
    workload = build_workload(policy, seed=11)
    trie = build_trie(workload.requests)
    result = serve(workload, policy, trie=trie)
    truth = shapley(workload, policy, trie, result).prefill_shares
    accuracy = []
    for permutations in SAMPLE_COUNTS:
        worst_by_seed = []
        median_by_seed = []
        for seed in ACCURACY_SEEDS:
            estimate = sampled_shapley(workload, policy, trie, permutations, seed)
            errors = [abs(estimate[t] - truth[t]) / truth[t] for t in workload.tenants]
            worst_by_seed.append(max(errors))
            median_by_seed.append(float(np.median(errors)))
        accuracy.append(
            {
                "permutations": permutations,
                "seeds": len(ACCURACY_SEEDS),
                "max_relative_error": round(max(worst_by_seed), 5),
                "median_relative_error": round(float(np.median(median_by_seed)), 5),
                "under_materiality": max(worst_by_seed) <= policy.attribution.material_share_shift,
            }
        )
        print(
            f"  {permutations:>5} permutations  worst tenant over {len(ACCURACY_SEEDS)} seeds "
            f"off by {max(worst_by_seed):.5f}  "
            f"(materiality {policy.attribution.material_share_shift})"
        )

    first, last = rows[0], rows[-1]
    return {
        "hardware": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "platform": platform.system(),
        },
        "repeats": repeats,
        "bench_samples": BENCH_SAMPLES,
        "materiality": policy.attribution.material_share_shift,
        "rows": rows,
        "accuracy": accuracy,
        "accuracy_seeds": list(ACCURACY_SEEDS),
        "smallest_sample_under_materiality": next(
            (item["permutations"] for item in accuracy if item["under_materiality"]), None
        ),
        # Whether the worst case error falls every time the sample count rises.
        # It does not, and a reader deciding how many permutations to buy should
        # be told that rather than shown a curve that happens to look smooth.
        "error_falls_monotonically": all(
            accuracy[index]["max_relative_error"] >= accuracy[index + 1]["max_relative_error"]
            for index in range(len(accuracy) - 1)
        ),
        # A value near 1.0 says the exact pass is linear in the node count.
        # Published as a ratio rather than claimed in prose, so a change in the
        # computation shows up here instead of quietly contradicting the README.
        "linearity": round(
            (last["exact_p50_ms"] / first["exact_p50_ms"])
            / (last["distinct_prefix_tokens"] / first["distinct_prefix_tokens"]),
            3,
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    print(
        f"timing the exact pass against {BENCH_SAMPLES} sampled permutations at "
        f"{len(CONVERSATION_SIZES)} workload sizes, {args.repeats} repeats each"
    )
    payload = measure(args.repeats)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
