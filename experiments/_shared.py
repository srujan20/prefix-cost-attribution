"""Shared plumbing for the five experiments.

Every experiment writes one JSON file into docs/experiments/ and prints a short
summary. Nothing here computes a published figure: `tools/collect_metrics.py`
reads these files and derives the numbers the documents quote, so every figure
has exactly one source and the documents cannot drift from it.

The workload cache is here rather than in the package because it is a property of
running experiments repeatedly, not of auditing a bill. Building the corpus and
training the vocabulary takes about a second, and the sweeps ask for the same
seed dozens of times.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "experiments"
sys.path.insert(0, str(REPO / "src"))

from prefixcost.config import load_policy  # noqa: E402
from prefixcost.trie import build_trie  # noqa: E402
from prefixcost.workload import build_workload  # noqa: E402

POLICY_PATH = REPO / "configs" / "policy.yaml"


def policy():
    return load_policy(POLICY_PATH)


@lru_cache(maxsize=8)
def workload_and_trie(seed: int):
    """The workload for a seed, and its trie, built once per process.

    Cached because the sweeps replay the same workload at a dozen capacities and
    a dozen orderings, and rebuilding it each time would make the benchmark a
    measurement of the corpus generator.
    """
    settings = policy()
    workload = build_workload(settings, seed=seed)
    return workload, build_trie(workload.requests)


def write(name: str, payload: dict) -> Path:
    DOCS.mkdir(parents=True, exist_ok=True)
    destination = DOCS / f"{name}.json"
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {destination.relative_to(REPO)}")
    return destination


def rate_dict(successes: int, trials: int) -> dict:
    from prefixcost.rates import Rate

    return Rate(successes=successes, trials=trials).as_dict()


def summary(values) -> dict[str, float] | None:
    """Five order statistics, or None when there is nothing to summarise.

    None entries are dropped rather than counted as zero: a correlation that is
    not defined because one side is constant is not a correlation of zero, and
    silently turning it into one would invent agreement.
    """
    import numpy as np

    kept = [value for value in values if value is not None]
    if not kept:
        return None
    array = np.asarray(kept, dtype=float)
    return {
        "n": len(kept),
        "median": float(np.median(array)),
        "mean": float(array.mean()),
        "min": float(array.min()),
        "max": float(array.max()),
        "p95": float(np.percentile(array, 95)),
    }


def banner(title: str) -> None:
    print(title)
    print("-" * len(title))
