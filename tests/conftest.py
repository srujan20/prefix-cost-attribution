"""Fixtures shared across the suite.

`tiny_policy` is small on purpose. Almost everything asserted here is an invariant
rather than a value, and an invariant that holds on twenty four tenants holds on
six, while the suite runs in seconds rather than minutes. The shipped policy is
used only where the assertion is about the shipped configuration.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from prefixcost.config import load_policy
from prefixcost.trie import build_trie
from prefixcost.workload import build_workload

REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "configs" / "policy.yaml"


@pytest.fixture(scope="session")
def shipped_policy():
    return load_policy(POLICY_PATH)


@pytest.fixture
def tiny_policy(shipped_policy):
    return replace(
        shipped_policy,
        vocabulary=replace(shipped_policy.vocabulary, target_size=300),
        workload=replace(
            shipped_policy.workload,
            tenants=6,
            prompt_families=2,
            conversations_per_tenant=3,
            turns_per_conversation=3,
        ),
        attribution=replace(shipped_policy.attribution, replications=20),
    )


@pytest.fixture
def workload(tiny_policy):
    return build_workload(tiny_policy, seed=7)


@pytest.fixture
def trie(workload):
    return build_trie(workload.requests)
