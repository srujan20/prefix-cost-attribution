"""Re-measure every published figure and write docs/metrics.json.

Nothing in this file types a number. It runs the test suite, reads the machine
readable reports it produces, runs all five experiments, reads their JSON and the
benchmark's, and computes the derived values. `tools/check_numbers.py` then
verifies that the documents quote exactly these values.

Three details worth keeping.

The test count and the coverage percentage come from --junitxml and a JSON
coverage report rather than from parsing a progress line, because a parsed
progress line quietly becomes whatever the last run happened to print.

Every anchor phrase is checked for containing a placeholder, because an anchor
without one matches whatever the document says regardless of the value, which is
a guard that cannot fail.

The benchmark table is read from a committed file rather than re-measured. A
duration measured on a GitHub runner is a different measurement from one measured
on the machine the README describes, so re-timing in CI would fail the check for
the honest reason that the hardware changed.

Usage:
    python tools/collect_metrics.py [--skip-tests] [--skip-experiments]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs"
EXPERIMENTS = DOCS / "experiments"
REPORTS = REPO / "reports"

EXPERIMENT_SCRIPTS = (
    "exp01_the_two_exact_anchors.py",
    "exp02_the_same_usage_two_bills.py",
    "exp03_the_policy_not_the_capacity.py",
    "exp04_who_pays_the_surplus.py",
    "exp05_no_scheme_has_all_three.py",
)

# The exact wording a document must use with the value substituted in. Anchors are
# alternatives: one match is enough, because the same figure reads differently in
# a table and in a paragraph. Each anchor names the number plus two or three
# words, never a whole clause. The checker collapses whitespace before matching,
# so an anchor no longer has to be lucky about where a markdown line happened to
# break.
ANCHORS: dict[str, list[str]] = {
    "tests_total": ["{} tests"],
    "coverage_line_pct": ["{} percent line coverage", "{}% line coverage"],
    "requests": ["{} requests"],
    "tenants": ["{} tenants"],
    "prompt_families": ["{} prompt families"],
    "prompt_tokens": ["{} prompt tokens"],
    "distinct_prefix_tokens": ["{} distinct prefix tokens"],
    "shared_nodes": ["{} of those prefix tokens"],
    "shared_node_share": ["shared between tenants, which is {}"],
    "vocabulary_size": ["a vocabulary of {}"],
    "reuse_share": ["reuses {} of"],
    "anchor_permutation_trials": ["under {} permutations"],
    "shipped_capacity": ["capacity of {} tokens"],
    "capacity_share_of_working_set": ["{} of the working set"],
    "shipped_prefill": ["actually processes {}"],
    "shipped_hit_share": ["hit share of {}"],
    "shipped_evictions": ["{} evictions"],
    "per_request_collects": ["collects {} times"],
    "per_request_surplus": ["{} more than the server"],
    "per_request_vs_fair_prefill": ["{} times the fair share"],
    "worst_per_request_tenant_ratio": ["worst line is {} times"],
    "fair_collects": ["fair split collects {}"],
    "fair_shortfall": ["short of the bill by {}"],
    "marginal_median_spread": ["moves by {} of its own"],
    "marginal_worst_spread": ["worst tenant moves {}"],
    "marginal_low": ["charged {} in one arrival order"],
    "marginal_high": ["and {} in another"],
    "tenant_observations": ["{} tenant observations"],
    "stable_tenants": ["{} of them exactly"],
    "orderings_per_workload": ["{} arrival orders"],
    "first_arriver_marginal": ["first arriver top in {} of"],
    "family_replays": ["of {} family replays"],
    "first_arriver_chance": ["chance would be {}"],
    "first_arriver_shapley": ["fair split does it {}"],
    "arrival_correlation_marginal": ["rank correlation of {} with"],
    "arrival_correlation_shapley": ["against {} for the fair"],
    "oracle_prefill": ["an oracle needs only {}"],
    "lru_excess_at_shipped": ["spending {} more than"],
    "zero_eviction_capacity": ["eviction stops at {}"],
    "first_bill_capacity": ["a bill again from {}"],
    "evictions_at_first_bill": ["still evicting {} nodes"],
    "policy_cost_worst": ["as much as {} more"],
    "grid_cells": ["{} configurations"],
    "grid_recomputing": ["{} of them recompute"],
    "grid_all_three": ["all three in {} of"],
    "grid_all_three_when_recomputing": ["has all three in {} of the"],
    "bench_repeats": ["{} timed repeats"],
    "bench_python": ["Python {}"],
    "bench_smallest_requests": ["from {} requests"],
    "bench_largest_requests": ["out to {} requests"],
    "bench_exact_small_ms": ["exact pass takes {} ms"],
    "bench_exact_large_ms": ["{} ms at the largest"],
    "bench_sampled_large_ms": ["sampled pass takes {} ms"],
    "bench_sampled_ratio": ["{} times the exact pass"],
    "bench_linearity": ["a linearity ratio of {}"],
    "bench_samples": ["at {} permutations"],
    "bench_smallest_sample_ok": ["takes {} permutations"],
    "bench_error_at_that_sample": ["still wrong by {}"],
    "bench_error_at_ten": ["off by {} at ten"],
}

CELL_ANCHOR = ["| {} |"]


def run(command: list[str], *, cwd: Path = REPO) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def run_tests() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    completed = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--junitxml=reports/junit.xml",
            "--cov=prefixcost",
            "--cov-report=json:reports/coverage.json",
        ]
    )
    if completed.returncode != 0:
        print(completed.stdout[-4000:], file=sys.stderr)
        raise SystemExit("the test run failed, so there are no figures to publish")


def read_test_reports() -> tuple[int, float]:
    import xml.etree.ElementTree as ElementTree

    junit = REPORTS / "junit.xml"
    coverage = REPORTS / "coverage.json"
    for path in (junit, coverage):
        if not path.is_file():
            raise SystemExit(f"{path.relative_to(REPO)} is missing. Run: make test")
    root = ElementTree.parse(junit).getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    total = int(suite.get("tests", 0)) - int(suite.get("skipped", 0))
    percent = json.loads(coverage.read_text(encoding="utf-8"))["totals"]["percent_covered"]
    return total, round(percent, 1)


def run_experiments() -> None:
    for script in EXPERIMENT_SCRIPTS:
        completed = run([sys.executable, f"experiments/{script}"])
        if completed.returncode != 0:
            print(completed.stdout[-2000:], file=sys.stderr)
            print(completed.stderr[-2000:], file=sys.stderr)
            raise SystemExit(f"{script} failed")


def load(name: str) -> dict:
    path = EXPERIMENTS / f"{name}.json"
    if not path.is_file():
        raise SystemExit(f"{path.relative_to(REPO)} is missing. Run: make experiments")
    return json.loads(path.read_text(encoding="utf-8"))


def load_bench() -> dict:
    path = REPO / "benchmark/results/attribution_latency.json"
    if not path.is_file():
        raise SystemExit(
            f"{path.relative_to(REPO)} is missing, so the cost table cannot be "
            "checked. Run: make bench"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def two(value: float) -> float:
    return round(float(value), 2)


def three(value: float) -> float:
    return round(float(value), 3)


def four(value: float) -> float:
    return round(float(value), 4)


def ms(value: float) -> float:
    """A duration rounded for prose: one decimal above a millisecond, three below."""
    return round(value, 1) if value >= 1.0 else round(value, 3)


def build_metrics(*, skip_tests: bool, skip_experiments: bool) -> dict[str, object]:
    if not skip_tests:
        run_tests()
    tests_total, coverage = read_test_reports()
    if not skip_experiments:
        run_experiments()

    exp01 = load("exp01-the-two-exact-anchors")
    exp02 = load("exp02-the-same-usage-two-bills")
    exp03 = load("exp03-the-policy-not-the-capacity")
    exp04 = load("exp04-who-pays-the-surplus")
    exp05 = load("exp05-no-scheme-has-all-three")
    bench = load_bench()

    shipped = exp03["at_shipped_capacity"]
    widest = exp02["widest_single_tenant"]
    worst_line = exp04["worst_single_tenant"]

    metrics: dict[str, object] = {
        "tests_total": tests_total,
        "coverage_line_pct": coverage,
        "requests": exp01["requests"],
        "tenants": exp01["tenants"],
        "prompt_families": exp05["shapes"][2]["prompt_families"],
        "prompt_tokens": exp01["prompt_tokens"],
        "distinct_prefix_tokens": exp01["distinct_prefix_tokens"],
        "shared_nodes": exp01["shared_nodes"],
        "shared_node_share": three(exp01["shared_node_share"]),
        "vocabulary_size": exp01["vocabulary_size"],
        "reuse_share": four(exp01["reuse_share"]["median"]),
        "anchor_permutation_trials": exp01["anchor_holds_under_permutation"]["trials"],
        "shipped_capacity": exp03["shipped_capacity"],
        "capacity_share_of_working_set": two(exp03["shipped_capacity_share_of_working_set"]),
        "shipped_prefill": shipped["lru"]["prefill_tokens"],
        "shipped_hit_share": four(shipped["lru"]["hit_share"]),
        "shipped_evictions": shipped["lru"]["evictions"],
        "per_request_collects": four(exp02["collects"]["per_request"]["median"]),
        "per_request_surplus": four(exp04["surplus_share"]["median"]),
        "per_request_vs_fair_prefill": four(exp04["ratio_to_fair"]["per_request"]["median"]),
        "worst_per_request_tenant_ratio": four(worst_line["ratio"]),
        "fair_collects": four(exp02["collects"]["shapley"]["median"]),
        "fair_shortfall": four(1.0 - exp02["collects"]["shapley"]["median"]),
        "marginal_median_spread": four(exp02["spread"]["marginal"]["median"]),
        "marginal_worst_spread": four(exp02["worst_tenant_spread"]["marginal"]["max"]),
        "marginal_low": round(widest["low"]),
        "marginal_high": round(widest["high"]),
        "tenant_observations": exp02["tenant_observations"],
        "stable_tenants": exp02["exactly_zero_spread"]["shapley"]["successes"],
        "orderings_per_workload": exp02["orderings"],
        "first_arriver_marginal": exp04["first_arriver_pays_most"]["marginal"]["successes"],
        "family_replays": exp04["family_trials"],
        "first_arriver_chance": four(exp04["chance_first_pays_most"]),
        "first_arriver_shapley": four(exp04["first_arriver_pays_most"]["shapley"]["value"]),
        "arrival_correlation_marginal": four(
            exp04["correlation_with_arrival_position"]["marginal"]["median"]
        ),
        "arrival_correlation_shapley": four(
            exp04["correlation_with_arrival_position"]["shapley"]["median"]
        ),
        "oracle_prefill": shipped["oracle"]["prefill_tokens"],
        "lru_excess_at_shipped": four(exp03["lru_excess_share_at_shipped"]["median"]),
        "zero_eviction_capacity": exp03["first_zero_eviction_capacity"],
        "first_bill_capacity": exp03["first_capacity_where_shapley_is_a_bill"],
        "evictions_at_first_bill": exp03["evictions_at_that_capacity"],
        "policy_cost_worst": four(max(row["policy_excess_share"] for row in exp03["rows"])),
        "grid_cells": exp05["cells_total"],
        "grid_recomputing": exp05["cells_recomputing"],
        "grid_all_three": exp05["all_three"]["shapley"]["successes"],
        "grid_all_three_when_recomputing": exp05["all_three_when_recomputing"]["shapley"][
            "successes"
        ],
    }

    small, large = bench["rows"][0], bench["rows"][-1]
    smallest_ok = bench["smallest_sample_under_materiality"]
    at_that_sample = next(item for item in bench["accuracy"] if item["permutations"] == smallest_ok)
    metrics["bench_repeats"] = bench["repeats"]
    metrics["bench_python"] = bench["hardware"]["python"]
    metrics["bench_smallest_requests"] = small["requests"]
    metrics["bench_largest_requests"] = large["requests"]
    metrics["bench_exact_small_ms"] = ms(small["exact_p50_ms"])
    metrics["bench_exact_large_ms"] = ms(large["exact_p50_ms"])
    metrics["bench_sampled_large_ms"] = ms(large["sampled_p50_ms"])
    metrics["bench_sampled_ratio"] = large["sampled_over_exact"]
    metrics["bench_linearity"] = bench["linearity"]
    metrics["bench_samples"] = bench["bench_samples"]
    metrics["bench_smallest_sample_ok"] = smallest_ok
    metrics["bench_error_at_that_sample"] = four(at_that_sample["max_relative_error"])
    metrics["bench_error_at_ten"] = four(bench["accuracy"][0]["max_relative_error"])

    # Table cells. Each is guarded by the weaker claim that the value appears as a
    # table cell, because an anchor that also pinned the row label would need the
    # README to be generated rather than written.
    for name, entry in exp02["spread"].items():
        metrics[f"cell_spread_{name}"] = four(entry["median"])
    for name, entry in exp02["collects"].items():
        metrics[f"cell_collects_{name}"] = four(entry["median"])
    for name, entry in exp04["ratio_to_fair"].items():
        metrics[f"cell_fair_{name}"] = four(entry["median"])
    for name, entry in exp04["first_arriver_pays_most"].items():
        metrics[f"cell_first_{name}"] = four(entry["value"])
    for row in exp03["rows"]:
        key = row["capacity_tokens"]
        metrics[f"cell_cap_{key}_lru"] = row["lru"]["prefill_tokens"]
        metrics[f"cell_cap_{key}_oracle"] = row["oracle"]["prefill_tokens"]
        metrics[f"cell_cap_{key}_policy"] = four(row["policy_excess_share"])
        metrics[f"cell_cap_{key}_evictions"] = row["lru"]["evictions"]
    for row in bench["rows"]:
        key = row["requests"]
        metrics[f"cell_bench_{key}_nodes"] = row["distinct_prefix_tokens"]
        metrics[f"cell_bench_{key}_exact"] = ms(row["exact_p50_ms"])
        metrics[f"cell_bench_{key}_exact_p95"] = ms(row["exact_p95_ms"])
        metrics[f"cell_bench_{key}_sampled"] = ms(row["sampled_p50_ms"])
        metrics[f"cell_bench_{key}_ratio"] = row["sampled_over_exact"]
    for item in bench["accuracy"]:
        metrics[f"cell_acc_{item['permutations']}_max"] = four(item["max_relative_error"])
        metrics[f"cell_acc_{item['permutations']}_median"] = four(item["median_relative_error"])

    for name in metrics:
        if name.startswith("cell_"):
            ANCHORS[name] = CELL_ANCHOR

    vacuous = sorted(
        name for name in metrics if any("{}" not in phrase for phrase in ANCHORS.get(name, ["{}"]))
    )
    if vacuous:
        raise SystemExit(
            "every anchor phrase must contain a placeholder, otherwise it matches any "
            "value. Offending metrics: " + ", ".join(vacuous)
        )

    missing_anchors = sorted(set(metrics) - set(ANCHORS))
    if missing_anchors:
        raise SystemExit(
            "every metric needs at least one anchor phrase, missing for: "
            + ", ".join(missing_anchors)
        )
    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-experiments", action="store_true")
    args = parser.parse_args(argv)

    metrics = build_metrics(skip_tests=args.skip_tests, skip_experiments=args.skip_experiments)
    payload = {
        "metrics": metrics,
        "anchors": {name: ANCHORS[name] for name in metrics},
        "checked_documents": [
            "README.md",
            "docs/defense-guide.md",
            "docs/adr/ADR-001-the-cache-is-the-trie-not-a-map-of-prompts.md",
            "docs/adr/ADR-002-compute-the-shapley-value-rather-than-sample-it.md",
            "docs/adr/ADR-003-train-the-vocabulary-here-rather-than-download-one.md",
            "docs/adr/ADR-004-not-a-bill-outranks-order-dependent.md",
            "docs/adr/ADR-005-evict-from-the-leaves-inwards.md",
        ],
        "note": "every value here is produced by running the suite and the five experiments",
    }
    destination = DOCS / "metrics.json"
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {destination.relative_to(REPO)} with {len(metrics)} metrics")
    for name, value in metrics.items():
        if not name.startswith("cell_"):
            print(f"  {name:<34} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
