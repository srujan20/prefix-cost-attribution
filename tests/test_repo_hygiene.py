"""Repository level invariants that are cheap to break and expensive to notice.

These are not tests of the library. They are tests of the deliverable, and each
one exists because the failure it catches is invisible in a diff: an em dash
that slipped into a docstring, a placeholder handle left in a badge URL, an
optional dependency imported at module scope so a lean install fails.
"""

from __future__ import annotations

import ast
import builtins
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".cfg",
    ".txt",
    ".json",
    ".svg",
}
NETWORK_MODULES = ("requests", "urllib", "httpx", "aiohttp", "socket", "boto3")
SKIP_DIRECTORIES = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "reports",
    "node_modules",
    ".cache",
    ".venv",
    "venv",
    "env",
    "site-packages",
    "build",
    "dist",
}
OPTIONAL_MODULES = ("playwright", "cmarkgfm", "matplotlib")
# Imported at call time only. A module scope import of tiktoken would make
# `pip install prefixcost` reach the network on first use, which is the one thing
# this repository promises it never does.
DEFERRED_MODULES = ("tiktoken",)

# Built from code points rather than written out, so this file does not itself
# contain the characters and strings it forbids. The first version did, and the
# suite failed with this file as the only offender, which is a confusing
# signature to read.
EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
PLACEHOLDER_HANDLES = ("YOUR_" + "USERNAME", "<user" + "name>", "USERNAME" + "/")


def text_files() -> list[Path]:
    """The text files that are part of the deliverable.

    Asks git first, and this is not a preference. The first version walked the
    tree with rglob and a skip list, which passed in the working copy and failed
    in a fresh clone: the clone-run-verify check builds a virtualenv inside the
    repository, and rglob happily read site-packages, where em dashes and
    placeholder handles are plentiful. What the hygiene tests are about is what
    was committed, so the tracked file list is the right question to ask.
    """
    if (REPO / ".git").exists():
        completed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, text=True, check=True
        )
        return [
            REPO / name
            for name in completed.stdout.split("\0")
            if name and Path(name).suffix in TEXT_SUFFIXES and (REPO / name).is_file()
        ]
    return [
        path
        for path in REPO.rglob("*")
        if path.is_file()
        and path.suffix in TEXT_SUFFIXES
        and not any(part in SKIP_DIRECTORIES for part in path.parts)
    ]


def test_there_are_text_files_to_check():
    assert len(text_files()) > 15


def test_no_file_contains_an_em_dash():
    offenders = [
        str(path.relative_to(REPO))
        for path in text_files()
        if EM_DASH in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_no_file_contains_an_en_dash_between_words():
    """An en dash in prose is the same drift as an em dash, and reads as generated."""
    pattern = re.compile(rf"[A-Za-z]{EN_DASH}[A-Za-z]")
    offenders = [
        str(path.relative_to(REPO))
        for path in text_files()
        if pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_no_file_carries_a_placeholder_handle():
    offenders: list[str] = []
    for path in text_files():
        content = path.read_text(encoding="utf-8")
        if any(handle in content for handle in PLACEHOLDER_HANDLES):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_the_package_imports_without_the_evidence_extras(monkeypatch):
    """Trap 1: a lean install must not fail on an import the extras provide.

    The evidence tooling needs Playwright, matplotlib and cmark-gfm, and the
    optional real vocabulary needs tiktoken. Nothing under src may import any of
    them at module scope, or `pip install .` followed by `prefixcost audit`
    breaks on a machine that never asked for them.
    """
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.split(".")[0] in OPTIONAL_MODULES:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    for module in (
        "prefixcost.cli",
        "prefixcost.audit",
        "prefixcost.attribution",
        "prefixcost.serving",
        "prefixcost.trie",
        "prefixcost.tokenizer",
        "prefixcost.workload",
        "prefixcost.report",
        "prefixcost.rates",
        # The vocabularies module itself must import without tiktoken present,
        # because `prefixcost plan` asks it whether a real vocabulary is
        # available.
        "prefixcost.vocabularies",
    ):
        __import__(module)


def test_a_deferred_dependency_is_never_imported_at_module_scope():
    """A module scope import of tiktoken would break the offline claim for everyone.

    Checked by walking the syntax tree rather than by scanning lines. The first
    version scanned text and failed on a docstring that contained both the word
    import and the word tiktoken, which is a test reporting on prose.
    """
    offenders: list[str] = []
    for path in (REPO / "src").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in DEFERRED_MODULES:
                    offenders.append(f"{path.relative_to(REPO)}:{name}")
    assert offenders == []


def test_the_extras_are_the_three_this_repository_declares():
    """Three extras, and the claim is that the runtime needs none of them.

    A fourth appearing without a decision record is the thing this test exists to
    notice. The runtime dependencies are numpy and a YAML parser, and ADR-003
    explains why a pretrained vocabulary is not among them.
    """
    import tomllib

    manifest = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    extras = manifest["project"]["optional-dependencies"]
    assert set(extras) == {"dev", "vocabularies", "evidence"}


def test_the_vocabularies_module_reports_availability_without_importing_it(monkeypatch):
    """`pretrained_available` has to answer on a machine with no tiktoken at all."""
    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.split(".")[0] == "tiktoken":
            raise ModuleNotFoundError("No module named 'tiktoken'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    from prefixcost.errors import VocabularyUnavailable
    from prefixcost.vocabularies import pretrained_available, require_pretrained

    assert pretrained_available() is False
    with pytest.raises(VocabularyUnavailable, match="vocabularies extra"):
        require_pretrained()


def test_no_source_file_imports_an_evidence_extra():
    offenders: list[str] = []
    for path in (REPO / "src").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for module in OPTIONAL_MODULES:
            if re.search(rf"^\s*(import|from)\s+{module}\b", content, re.MULTILINE):
                offenders.append(f"{path.relative_to(REPO)}:{module}")
    assert offenders == []


def test_no_source_file_imports_a_network_library():
    """The README promises the headline number is reproducible with no network."""
    offenders: list[str] = []
    for path in (REPO / "src").rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        for module in NETWORK_MODULES:
            if re.search(rf"^\s*(import|from)\s+{module}\b", content, re.MULTILINE):
                offenders.append(f"{path.relative_to(REPO)}:{module}")
    assert offenders == []


def test_no_experiment_or_benchmark_imports_a_network_library():
    offenders: list[str] = []
    for directory in ("experiments", "benchmark"):
        for path in (REPO / directory).rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            for module in NETWORK_MODULES:
                if re.search(rf"^\s*(import|from)\s+{module}\b", content, re.MULTILINE):
                    offenders.append(f"{path.relative_to(REPO)}:{module}")
    assert offenders == []


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout


def _is_git_checkout() -> bool:
    return (REPO / ".git").exists()


def _history_is_complete() -> bool:
    """Whether this checkout can see the whole history, or only the tip of it.

    This function exists because of a CI failure I could not reproduce on any
    machine I had. Four of the five jobs went red on GitHub while the same suite
    was green locally and green from a fresh full clone. The cause was
    actions/checkout, which clones with depth 1 unless told otherwise, so the
    runner saw one commit and the dump commit test saw a dump commit.

    The workflow now sets fetch-depth: 0, which is the real fix. This guard is
    the second half of it: a test that asserts a property of the history has no
    business failing on a checkout that was never given the history. It reports
    what it cannot see instead of reporting a defect that is not there.
    """
    if not _is_git_checkout():
        return False
    return _git("rev-parse", "--is-shallow-repository").strip() != "true"


needs_git = pytest.mark.skipif(not _is_git_checkout(), reason="not a git checkout")
needs_history = pytest.mark.skipif(
    not _history_is_complete(), reason="shallow checkout, the history is not present to check"
)

# A commit made through the GitHub web editor is authored by the same person and
# committed by GitHub, under the account's noreply address. That is not a second
# author, and the first version of the author test called it one.
GITHUB_COMMITTERS = ("noreply@github.com", "web-flow")
BOT_MARKERS = ("[bot]", "bot@", "dependabot", "renovate", "github-actions")


@needs_git
def test_no_commit_message_carries_an_ai_attribution():
    log = _git("log", "--format=%B%n%an%n%ae").lower()
    for marker in (
        "co-authored-by",
        "generated with",
        "claude",
        "copilot",
        "chatgpt",
        "gpt-4",
        "openai",
        "anthropic",
    ):
        assert marker not in log, marker


@needs_history
def test_no_commit_was_written_by_a_bot():
    """Replaces an assertion that every commit carried one identical author.

    That version was too strict to survive contact with GitHub. Editing a file
    in the web UI produces a commit whose committer is GitHub, and the test read
    the extra identity as a broken history rather than as what it is. What this
    file actually needs to defend is that a person wrote the history and no tool
    signed it, so that is what it now asserts.
    """
    identities = {
        line.lower() for line in _git("log", "--format=%an <%ae>%n%cn <%ce>").splitlines() if line
    }
    offenders = [
        identity
        for identity in identities
        if any(marker in identity for marker in BOT_MARKERS)
        and not any(allowed in identity for allowed in GITHUB_COMMITTERS)
    ]
    assert offenders == []


@needs_history
def test_the_history_has_one_human_author():
    """One person authored every commit, ignoring who GitHub recorded as committer."""
    authors = {line for line in _git("log", "--format=%an <%ae>").splitlines() if line}
    non_github = {
        author
        for author in authors
        if not any(allowed in author.lower() for allowed in GITHUB_COMMITTERS)
    }
    assert len(non_github) == 1, sorted(authors)


@needs_history
def test_the_history_is_not_a_single_dump_commit():
    count = len(_git("log", "--format=%h").splitlines())
    assert count >= 5


@needs_git
def test_no_commit_message_contains_an_em_dash():
    assert EM_DASH not in _git("log", "--format=%B")
