"""Fail the build when a document quotes a figure the code no longer produces.

Two checks, in both directions.

The forward check: every metric in docs/metrics.json must appear in at least one
checked document, using one of its anchor phrases with the value substituted in.
Matching happens on phrases rather than on bare digits because a digit search
passes while the sentence around it has gone stale: "960 requests" is found in a
document that now says "960 tokens". Anchors are alternatives, so any one matching
is enough, since the same figure reads differently in a table and in a
paragraph.

The reverse check: any number in the prose of a checked document that matches no
metric is reported, because that is a number nothing re-measures. Fenced code
blocks, inline code spans, HTML attributes and link targets are excluded, since
an example invocation is allowed to contain a made up row count and flagging it
would train the reader to ignore the section. Numbers that come from the policy
file, and a short list of structural numbers such as the exit codes and the swept
capacities, are allowed. The reverse check reports by default and fails only with --strict,
because a check that legitimately fails gets deleted or weakened within a week.

Usage:
    python tools/check_numbers.py [--strict] [--summary PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
METRICS = REPO / "docs" / "metrics.json"
POLICY = REPO / "configs" / "policy.yaml"

FENCE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]*`")
HTML_ATTRIBUTE = re.compile(r"""\w+="[^"]*\"""")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
# An optional leading minus is part of the number. Without it a rank correlation
# published as -0.2338 was scanned as the token 0.2338, which matched no metric,
# and the reverse check reported the repository's own measurement as unexplained.
NUMBER = re.compile(r"(?<![\w.])-?\d+(?:[.,]\d+)*(?![\w])")
WHITESPACE = re.compile(r"\s+")

# Structural numbers that are part of the design rather than measurements, each
# with the reason it is not something the code re-measures.
STRUCTURAL_NUMBERS = {
    "0": "a count of zero in prose",
    "1": "a single item, an exit code, or one pass over the trie",
    "2": "an exit code, or a pair",
    "3": "an exit code",
    "4": "an exit code, and the tenants sharing one prompt family",
    "5": "the number of schemes, and the number of experiments",
    "6": "an ADR count",
    "10": "the smallest sample count in the accuracy sweep",
    "28,504": "the prefill an earlier whole prompt cache reported, quoted in ADR-001",
    "60000": "the cache capacity this repository shipped first, quoted as a rejected default",
    "0.0013": "the correlation the first version of exp04 reported, quoted as a mistake",
    "50": "a sample count in the accuracy sweep",
    "1000": "the largest sample count in the accuracy sweep",
    "06": "the exponent of a scientific notation tolerance, 1e-06",
    "09": "the exponent of a scientific notation tolerance, 1e-09",
    "1e-06": "the tolerance at which a total counts as summing to the bill",
    "1e-09": "the tolerance at which two shares count as equal",
    "0.08": "a capacity fraction in the grid",
    "0.25": "a capacity fraction in the grid, and the chance an arrival is first of four",
    "0.6": "a capacity fraction in the grid",
    "0.9": "a capacity fraction in the grid",
    "2.0": "a capacity fraction in the grid",
    "8": "a tenant count in the grid",
    "12": "a tenant count in the grid, and the arrival orders per workload",
    "30": "a tenant count in the grid",
    "1,000": "a capacity in the sweep",
    "2,000": "a capacity in the sweep",
    "4,000": "a capacity in the sweep",
    "12,000": "a capacity in the sweep",
    "16,000": "a capacity in the sweep",
    "32,000": "a capacity in the sweep",
    "4.5": "the contrast ratio the screenshot capture asserts",
    "001": "an ADR number",
    "002": "an ADR number",
    "003": "an ADR number",
    "004": "an ADR number",
    "005": "an ADR number",
    "01": "an experiment number",
    "02": "an experiment number",
    "03": "an experiment number",
    "04": "an experiment number",
    "05": "an experiment number",
    "3.10": "the minimum supported Python version",
    "3.11": "a supported Python version",
    "3.12": "a supported Python version",
    "3.13": "a supported Python version",
}


def load_metrics() -> tuple[dict[str, object], dict[str, list[str]], list[str]]:
    if not METRICS.is_file():
        raise SystemExit(
            f"{METRICS.relative_to(REPO)} is missing. Run tools/collect_metrics.py first."
        )
    payload = json.loads(METRICS.read_text(encoding="utf-8"))
    return payload["metrics"], payload["anchors"], payload["checked_documents"]


def policy_numbers() -> set[str]:
    """Every literal in the policy file, so a threshold in prose is not flagged."""
    if not POLICY.is_file():
        return set()
    raw = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, bool):
            return
        elif isinstance(node, (int, float)):
            found.add(str(node))
            found.add(f"{float(node):g}")
            found.add(f"{100 * float(node):g}")

    walk(raw)
    return found


def rendered(value: object) -> list[str]:
    """Every string form a value may legitimately take in prose."""
    if isinstance(value, float):
        text = f"{value:g}"
        forms = {text, f"{value}"}
        if value == int(value):
            forms.add(str(int(value)))
        return sorted(forms)
    text = str(value)
    if isinstance(value, int) and value >= 1000:
        return sorted({text, f"{value:,}"})
    return [text]


def prose_of(path: Path) -> str:
    """The checkable prose of a document, with whitespace collapsed to one space.

    Collapsing whitespace is the structural fix for a trap that cost real time on
    an earlier build: an anchor phrase longer than a few words straddles a newline
    in wrapped markdown and then matches nothing, while the prose it guards is
    perfectly correct. Anchors are still kept short, but they no longer have to be
    lucky about where the line broke.
    """
    content = path.read_text(encoding="utf-8")
    for pattern in (FENCE, INLINE_CODE, HTML_ATTRIBUTE, LINK_TARGET):
        content = pattern.sub(" ", content)
    return WHITESPACE.sub(" ", content)


def forward_check(
    metrics: dict[str, object], anchors: dict[str, list[str]], documents: dict[str, str]
) -> list[str]:
    failures: list[str] = []
    for name, value in metrics.items():
        phrases = [template.format(form) for template in anchors[name] for form in rendered(value)]
        hits = [
            document
            for document, content in documents.items()
            if any(phrase in content for phrase in phrases)
        ]
        if not hits:
            failures.append(
                f"{name} = {value} appears in no checked document. Expected one of: "
                + "; ".join(f'"{phrase}"' for phrase in phrases[:4])
            )
    return failures


def reverse_check(metrics: dict[str, object], documents: dict[str, str]) -> dict[str, list[str]]:
    allowed = set(STRUCTURAL_NUMBERS) | policy_numbers()
    for value in metrics.values():
        allowed.update(rendered(value))
        if isinstance(value, float):
            allowed.add(str(int(value)) if value == int(value) else f"{value:g}")
    unexplained: dict[str, list[str]] = {}
    for document, content in documents.items():
        found = sorted({match.group(0) for match in NUMBER.finditer(content)})
        leftovers = [
            token
            for token in found
            if token not in allowed and token.replace(",", "") not in allowed
        ]
        if leftovers:
            unexplained[document] = leftovers
    return unexplained


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true", help="also fail on an unexplained number in prose"
    )
    parser.add_argument("--summary", help="append a markdown summary to this file")
    args = parser.parse_args(argv)

    metrics, anchors, checked = load_metrics()
    documents: dict[str, str] = {}
    for name in checked:
        path = REPO / name
        if not path.is_file():
            print(f"missing checked document: {name}", file=sys.stderr)
            return 2
        documents[name] = prose_of(path)

    failures = forward_check(metrics, anchors, documents)
    unexplained = reverse_check(metrics, documents)

    print(f"checked {len(metrics)} metrics against {len(documents)} documents")
    for failure in failures:
        print(f"STALE  {failure}")
    for document, tokens in unexplained.items():
        print(f"UNANCHORED  {document}: {', '.join(tokens)}")

    if args.summary:
        lines = [
            "### Receipts",
            "",
            f"- metrics re-measured: {len(metrics)}",
            f"- documents checked: {len(documents)}",
            f"- stale figures: {len(failures)}",
            f"- unanchored numbers in prose: {sum(len(v) for v in unexplained.values())}",
            "",
        ]
        if failures:
            lines += ["Stale figures:", ""] + [f"- {item}" for item in failures] + [""]
        if unexplained:
            lines += (
                ["Unanchored numbers:", ""]
                + [
                    f"- `{document}`: {', '.join(tokens)}"
                    for document, tokens in unexplained.items()
                ]
                + [""]
            )
        with Path(args.summary).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))

    if failures:
        print(f"{len(failures)} figures no longer match the code", file=sys.stderr)
        return 1
    if args.strict and unexplained:
        print("unexplained numbers in prose, and --strict was given", file=sys.stderr)
        return 1
    print("every published figure matches the code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
