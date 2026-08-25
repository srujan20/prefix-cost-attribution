"""Draw the benchmark chart from the committed JSON, without re-timing anything.

Separate from the benchmark for one reason: a chart regenerated in CI would be a
picture of a GitHub runner, and the README describes a measurement made on a
stated machine. This script reads the file the benchmark wrote and does no
timing, so the chart and the prose come from the same run.

Usage:
    python benchmark/plot_results.py [--results PATH] [--out docs/charts/attribution.png]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = REPO / "benchmark/results/attribution_latency.json"
DEFAULT_OUT = REPO / "docs/charts/attribution-cost.png"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.results.is_file():
        raise SystemExit(f"{args.results} is missing. Run: make bench")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    payload = json.loads(args.results.read_text(encoding="utf-8"))
    rows = payload["rows"]
    accuracy = payload["accuracy"]

    figure, (left, right) = plt.subplots(1, 2, figsize=(11, 4.2))
    figure.patch.set_facecolor("white")

    nodes = [row["distinct_prefix_tokens"] for row in rows]
    left.plot(nodes, [row["exact_p50_ms"] for row in rows], marker="o", label="exact, one pass")
    left.plot(
        nodes,
        [row["sampled_p50_ms"] for row in rows],
        marker="s",
        label=f"sampled, {payload['bench_samples']} permutations",
    )
    left.set_yscale("log")
    left.set_xlabel("distinct prefix tokens")
    left.set_ylabel("milliseconds, median of repeats")
    left.set_title("What the allocation costs")
    left.grid(True, which="both", alpha=0.25)
    left.legend(frameon=False, fontsize=9)

    permutations = [item["permutations"] for item in accuracy]
    right.plot(
        permutations,
        [item["max_relative_error"] for item in accuracy],
        marker="o",
        color="#8c1d1d",
        label="worst tenant",
    )
    right.plot(
        permutations,
        [item["median_relative_error"] for item in accuracy],
        marker="s",
        color="#3d4348",
        label="median tenant",
    )
    right.axhline(
        payload["materiality"],
        color="#1c5c3a",
        linestyle="--",
        linewidth=1.2,
        label=f"materiality {payload['materiality']}",
    )
    right.set_xscale("log")
    right.set_yscale("log")
    right.set_xlabel("permutations sampled")
    right.set_ylabel("relative error against the exact value")
    right.set_title("What the sampled allocation gets wrong")
    right.grid(True, which="both", alpha=0.25)
    right.legend(frameon=False, fontsize=9)

    figure.tight_layout()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.out, dpi=150, facecolor="white")
    print(f"wrote {args.out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
