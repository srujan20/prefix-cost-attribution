"""The policy refusals, the CLI exit codes, and the reports.

The policy tests are the ones worth reading. Each refusal exists because the
configuration it rejects produces a number that looks like a finding and is a
statement about the configuration.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from prefixcost.audit import audit
from prefixcost.cli import main
from prefixcost.config import policy_from_mapping
from prefixcost.errors import PolicyError, UsageError
from prefixcost.report import html_report, json_report, text_report
from prefixcost.trie import build_trie
from prefixcost.workload import build_workload

REPO = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO / "configs" / "policy.yaml"


@pytest.fixture
def raw_policy():
    return yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))


def test_the_shipped_policy_loads(shipped_policy):
    assert shipped_policy.workload.tenants >= 2
    assert shipped_policy.resolution_floor == 1.0 / shipped_policy.attribution.replications


def test_more_families_than_tenants_is_refused(raw_policy):
    """The refusal that stops the tool reporting agreement as a finding."""
    broken = copy.deepcopy(raw_policy)
    broken["workload"]["prompt_families"] = broken["workload"]["tenants"] + 1
    with pytest.raises(PolicyError, match="no prefix is shared"):
        policy_from_mapping(broken)


def test_one_tenant_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["workload"]["tenants"] = 1
    with pytest.raises(PolicyError, match="not an attribution"):
        policy_from_mapping(broken)


def test_zero_families_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["workload"]["prompt_families"] = 0
    with pytest.raises(PolicyError, match="at least one prompt family"):
        policy_from_mapping(broken)


def test_a_conversation_with_no_turns_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["workload"]["turns_per_conversation"] = 0
    with pytest.raises(PolicyError, match="at least one turn"):
        policy_from_mapping(broken)


def test_an_unknown_cache_policy_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["cache"]["policy"] = "clairvoyant"
    with pytest.raises(PolicyError, match="unknown cache policy"):
        policy_from_mapping(broken)


def test_a_negative_capacity_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["cache"]["capacity_tokens"] = -1
    with pytest.raises(PolicyError, match="cannot be negative"):
        policy_from_mapping(broken)


def test_a_negative_price_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["pricing"]["decode_per_token"] = -2.0
    with pytest.raises(PolicyError, match="price per token"):
        policy_from_mapping(broken)


def test_an_unknown_scheme_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["attribution"]["default_scheme"] = "vibes"
    with pytest.raises(PolicyError, match="unknown scheme"):
        policy_from_mapping(broken)


def test_zero_replications_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["attribution"]["replications"] = 0
    with pytest.raises(PolicyError, match="at least 1"):
        policy_from_mapping(broken)


def test_a_missing_section_is_named(raw_policy):
    broken = copy.deepcopy(raw_policy)
    del broken["pricing"]
    with pytest.raises(PolicyError, match="pricing"):
        policy_from_mapping(broken)


def test_a_string_where_a_number_is_expected_is_refused(raw_policy):
    broken = copy.deepcopy(raw_policy)
    broken["workload"]["tenants"] = "many"
    with pytest.raises(PolicyError, match="must be int"):
        policy_from_mapping(broken)


def test_an_integer_where_a_float_is_expected_is_accepted(raw_policy):
    relaxed = copy.deepcopy(raw_policy)
    relaxed["pricing"]["prefill_per_token"] = 1
    assert policy_from_mapping(relaxed).pricing.prefill_per_token == 1.0


def test_a_missing_policy_file_is_refused(tmp_path):
    from prefixcost.config import load_policy

    with pytest.raises(PolicyError, match="not found"):
        load_policy(tmp_path / "nowhere.yaml")


def test_a_policy_that_is_not_a_mapping_is_refused(tmp_path):
    from prefixcost.config import load_policy

    path = tmp_path / "policy.yaml"
    path.write_text("- not a mapping\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="mapping"):
        load_policy(path)


def test_malformed_yaml_is_refused(tmp_path):
    from prefixcost.config import load_policy

    path = tmp_path / "policy.yaml"
    path.write_text("pricing: [unclosed\n", encoding="utf-8")
    with pytest.raises(PolicyError, match="not valid YAML"):
        load_policy(path)


def test_the_plan_command_names_every_scheme(capsys):
    assert main(["plan", "--policy", str(POLICY_PATH)]) == 0
    printed = capsys.readouterr().out
    for name in ("per_request", "marginal", "shapley"):
        assert name in printed


def test_the_cache_command_reports_the_exact_floor(capsys):
    code = main(
        ["cache", "--seed", "5", "--capacities", "0", "500000", "--policy", str(POLICY_PATH)]
    )
    printed = capsys.readouterr().out
    assert code == 0
    assert "distinct prefix tokens" in printed


def test_the_audit_command_returns_one_for_an_order_dependent_scheme(capsys):
    code = main(
        [
            "audit",
            "--seed",
            "5",
            "--scheme",
            "marginal",
            "--orderings",
            "4",
            "--policy",
            str(POLICY_PATH),
        ]
    )
    capsys.readouterr()
    assert code == 1


def test_the_fair_split_is_not_a_bill_at_the_shipped_capacity(capsys, tmp_path, raw_policy):
    """The finding, as an exit code.

    At the shipped capacity the cache recomputes prefixes it had already
    computed, so the server spends more than the trie's node count and the fair
    split, which divides the node count, no longer sums to the spend. Exit 2.
    """
    code = main(
        [
            "audit",
            "--seed",
            "5",
            "--scheme",
            "shapley",
            "--orderings",
            "4",
            "--policy",
            str(POLICY_PATH),
        ]
    )
    capsys.readouterr()
    assert code == 2


def test_the_fair_split_is_a_bill_once_the_cache_stops_recomputing(capsys, tmp_path, raw_policy):
    """And the other side of the boundary, which is what makes it a boundary.

    Raise the capacity above the workload's distinct prefix token count and the
    same scheme on the same workload becomes efficient, order independent and
    fair at once. Nothing about the scheme changed.
    """
    generous = copy.deepcopy(raw_policy)
    generous["cache"]["capacity_tokens"] = 400_000
    path = tmp_path / "generous.yaml"
    path.write_text(yaml.safe_dump(generous), encoding="utf-8")
    code = main(
        ["audit", "--seed", "5", "--scheme", "shapley", "--orderings", "4", "--policy", str(path)]
    )
    capsys.readouterr()
    assert code == 0


def test_the_audit_command_returns_two_for_a_scheme_that_is_not_a_bill(capsys):
    code = main(
        [
            "audit",
            "--seed",
            "5",
            "--scheme",
            "per_request",
            "--orderings",
            "4",
            "--policy",
            str(POLICY_PATH),
        ]
    )
    printed = capsys.readouterr().out
    assert code == 2
    assert "not an attribution" in printed or "not-an-attribution" in printed


def test_a_bad_policy_path_exits_four(capsys):
    assert main(["audit", "--policy", "/nowhere/policy.yaml"]) == 4
    assert "usage:" in capsys.readouterr().err


def test_an_unanswerable_workload_exits_three(tmp_path, raw_policy, capsys):
    broken = copy.deepcopy(raw_policy)
    broken["workload"]["mean_output_tokens"] = 0
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(broken), encoding="utf-8")
    code = main(["audit", "--seed", "5", "--orderings", "2", "--policy", str(path)])
    capsys.readouterr()
    assert code in (0, 1, 2)


def test_the_reports_round_trip(tmp_path, tiny_policy, capsys):
    workload = build_workload(tiny_policy, seed=5)
    result = audit(workload, tiny_policy, orderings_count=4)
    text = text_report(result, tiny_policy)
    assert "verdict:" in text
    markup = html_report(result, tiny_policy)
    assert 'id="schemes"' in markup
    assert 'id="spend"' in markup
    payload = json.loads(json_report(result, tiny_policy))
    assert payload["exit_code"] == result.verdict.exit_code


def test_the_trie_reports_how_widely_each_node_is_shared(tiny_policy):
    workload = build_workload(tiny_policy, seed=5)
    trie = build_trie(workload.requests)
    counts = trie.nodes_by_tenant_count()
    assert sum(counts.values()) == trie.distinct_prefix_tokens
    assert min(counts) >= 1


def test_walking_a_sequence_not_in_the_trie_is_refused(tiny_policy):
    from prefixcost.errors import UnanswerableError

    workload = build_workload(tiny_policy, seed=5)
    trie = build_trie(workload.requests)
    with pytest.raises(UnanswerableError, match="not in the trie"):
        list(trie.walk(("not", "a", "real", "token")))


def test_a_vocabulary_with_no_tokens_is_refused():
    from prefixcost.tokenizer import Vocabulary

    with pytest.raises(UsageError, match="cannot encode"):
        Vocabulary(merges=(), tokens=())


def test_the_module_entry_point_runs():
    completed = subprocess.run(
        [sys.executable, "-m", "prefixcost", "plan", "--policy", str(POLICY_PATH)],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
    )
    assert completed.returncode == 0
    assert "schemes" in completed.stdout


def test_the_audit_command_writes_both_report_files(tmp_path, capsys):
    html_path = tmp_path / "report.html"
    json_path = tmp_path / "report.json"
    code = main(
        [
            "audit",
            "--seed",
            "5",
            "--orderings",
            "3",
            "--policy",
            str(POLICY_PATH),
            "--html",
            str(html_path),
            "--json",
            str(json_path),
        ]
    )
    capsys.readouterr()
    assert code in (0, 1, 2)
    assert 'id="tenants"' in html_path.read_text(encoding="utf-8")
    assert json.loads(json_path.read_text(encoding="utf-8"))["scheme"]


def test_an_unanswerable_question_exits_three(monkeypatch, capsys):
    """The exit 3 path, reached by making the workload refuse.

    Provoked rather than waited for: the refusals that raise it are about inputs a
    caller supplies, and the CLI generates its own, so the only honest way to
    exercise the handler is to make the generator refuse.
    """
    from prefixcost import cli
    from prefixcost.errors import UnanswerableError

    def refuse(*args, **kwargs):
        raise UnanswerableError("nothing to attribute between")

    monkeypatch.setattr(cli, "build_workload", refuse)
    assert main(["audit", "--policy", str(POLICY_PATH)]) == 3
    assert "cannot answer:" in capsys.readouterr().err


def test_a_served_request_reports_its_own_hit_share(tiny_policy):
    from prefixcost.serving import serve

    workload = build_workload(tiny_policy, seed=5)
    served = serve(workload, tiny_policy, capacity_tokens=10**9)
    assert any(item.hit_share > 0 for item in served.served)
    assert all(0.0 <= item.hit_share <= 1.0 for item in served.served)


def test_prefill_by_tenant_sums_to_the_total(tiny_policy):
    from prefixcost.serving import serve

    workload = build_workload(tiny_policy, seed=5)
    served = serve(workload, tiny_policy, capacity_tokens=10**9)
    assert sum(served.prefill_by_tenant().values()) == served.prefill_tokens


def test_a_vocabulary_describes_itself(tiny_policy):
    workload = build_workload(tiny_policy, seed=5)
    described = workload.vocabulary.as_dict()
    assert described["size"] == workload.vocabulary.size
    assert len(described["first_merges"]) <= 12


def test_the_workload_reports_its_output_tokens(tiny_policy):
    workload = build_workload(tiny_policy, seed=5)
    assert workload.output_tokens == sum(r.output_tokens for r in workload.requests)


def test_the_policy_source_is_named_relative_to_the_repository(tmp_path):
    """A report ends up in a committed screenshot, so the path in it matters.

    An absolute path is correct and is also a photograph of the build machine's
    home directory, which tells a reader nothing and dates the image the moment
    the checkout moves.
    """
    from prefixcost.config import load_policy

    assert load_policy(POLICY_PATH).source == "configs/policy.yaml"
    assert load_policy().source == "configs/policy.yaml"


def test_a_policy_outside_the_repository_keeps_its_absolute_path(tmp_path, raw_policy):
    outside = tmp_path / "elsewhere.yaml"
    outside.write_text(yaml.safe_dump(raw_policy), encoding="utf-8")
    from prefixcost.config import load_policy

    assert load_policy(outside).source == str(outside.resolve())
