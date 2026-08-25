"""Photograph the real HTML report with Chromium, and check it is legible.

Three rules, each from a failure on an earlier project.

Frame by heading, not by pixel offset. The tool scrolls to a named h2, reads its
document offset, and clips from there to the next named heading. A hard coded
crop drifts silently the first time a paragraph gets longer, and then the README
shows half a table. A missing heading fails the run, because it means the report
changed shape and the shot is now meaningless.

Pass full_page even when a clip is given. Without it Chromium clamps the clip to
the viewport and every shot comes out exactly one viewport tall with the bottom
of the section missing.

Read back the computed style of the verdict badge and fail if it is invisible. A
badge class and a table cell class once collided at equal specificity on a
sibling project, the later rule won, and the verdict rendered green on green. The
Python was correct, the HTML was correct, the tests passed, and the headline
number was invisible. A screenshot tool is the only thing in the pipeline that
can see pixels.

Usage:
    python tools/capture_screenshots.py
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
SHOTS = REPO / "docs" / "screenshots"
BUILD = REPO / ".cache" / "reports"

VIEWPORT = {"width": 1120, "height": 900}
SCALE = 2

SHOT_PLAN = (
    {
        "name": "audit-order-dependent",
        "report": "marginal",
        "from_heading": None,
        "to_heading": "What each scheme says, and whether it divides that total",
        "caption": (
            "The cache aware bill, audited: exit 1, because a tenant's share moves "
            "with arrival order"
        ),
    },
    {
        "name": "audit-five-schemes",
        "report": "marginal",
        "from_heading": "What each scheme says, and whether it divides that total",
        "to_heading": "The shipped scheme against the fair split, per tenant",
        "caption": (
            "Five divisions of one bill. The only column that sums to it is the one "
            "that moves with arrival order"
        ),
    },
    {
        "name": "audit-per-tenant",
        "report": "marginal",
        "from_heading": "The shipped scheme against the fair split, per tenant",
        "to_heading": None,
        "caption": "The rows a customer would see, against what they would owe on a fair split",
    },
    {
        "name": "audit-not-an-attribution",
        "report": "per_request",
        "from_heading": None,
        "to_heading": "What each scheme says, and whether it divides that total",
        "caption": (
            "The bill almost everybody sends, audited: exit 2, because it is not a "
            "division of anything the server spent"
        ),
    },
)


def build_reports() -> dict[str, Path]:
    """Produce the real reports the shots are taken of.

    Two of them, from one workload at one capacity, differing only in which
    scheme the tool was asked to audit. The second is the one the README is
    really about: a per request token count, which is what almost every provider
    invoices, audited and returning exit 2 because it is not a division of what
    the server spent.
    """
    import sys

    sys.path.insert(0, str(REPO / "src"))

    from prefixcost.audit import audit
    from prefixcost.config import load_policy
    from prefixcost.report import html_report
    from prefixcost.workload import build_workload

    BUILD.mkdir(parents=True, exist_ok=True)
    policy = load_policy()
    workload = build_workload(policy, seed=11)

    paths = {}
    for scheme in ("marginal", "per_request"):
        destination = BUILD / f"{scheme}.html"
        destination.write_text(
            html_report(audit(workload, policy, scheme=scheme), policy), encoding="utf-8"
        )
        paths[scheme] = destination
    return paths


BADGE_PROBE = """() => {
  const badge = document.querySelector('.badge');
  if (!badge) return null;
  const style = getComputedStyle(badge);
  return {
    colour: style.color,
    background: style.backgroundColor,
    border: style.borderColor,
    visible: badge.offsetWidth > 0 && badge.offsetHeight > 0,
    text: badge.textContent.trim(),
  };
}"""

HEADING_PROBE = """(title) => {
  const found = Array.from(document.querySelectorAll('h2'))
    .find(node => node.textContent.trim() === title);
  if (!found) return null;
  const box = found.getBoundingClientRect();
  return {top: box.top + window.scrollY, height: document.body.scrollHeight};
}"""

# The widest thing actually drawn between the two headings. A fixed clip width
# frames every section at the width of the widest one, so a four column table
# came out with a third of the image blank beside it.
#
# Measured with a Range rather than getBoundingClientRect on the element. A
# paragraph is a block box and its rectangle is the full column width whatever it
# contains, so measuring elements returned the same number for every section and
# the clip never narrowed. A Range over the contents measures the text.
WIDTH_PROBE = """([from, to]) => {
  const nodes = Array.from(document.body.children);
  const start = from === null
    ? 0
    : nodes.findIndex(n => n.tagName === 'H2' && n.textContent.trim() === from);
  if (start < 0) return null;
  let end = nodes.length;
  if (to !== null) {
    const found = nodes.findIndex(n => n.tagName === 'H2' && n.textContent.trim() === to);
    if (found >= 0) end = found;
  }
  const edge = (node) => {
    if (node.tagName === 'TABLE') return node.getBoundingClientRect().right;
    const range = document.createRange();
    range.selectNodeContents(node);
    return range.getBoundingClientRect().right;
  };
  let right = 0;
  for (const node of nodes.slice(start, end)) {
    right = Math.max(right, edge(node));
  }
  return Math.ceil(right);
}"""


def luminance(colour: str) -> float:
    numbers = [float(part) for part in colour.replace("rgba", "rgb").strip("rgb()").split(",")[:3]]
    channels = []
    for value in numbers:
        scaled = value / 255.0
        channels.append(scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(foreground: str, background: str) -> float:
    first, second = luminance(foreground), luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def main() -> int:
    reports = build_reports()
    SHOTS.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, object]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE)
        badges: dict[str, dict[str, object]] = {}

        for name, path in reports.items():
            page.goto(path.resolve().as_uri())
            page.wait_for_load_state("load")
            badge = page.evaluate(BADGE_PROBE)
            if badge is None or not badge["visible"]:
                raise SystemExit(f"the verdict badge is missing or invisible in the {name} report")
            ratio = contrast(str(badge["colour"]), str(badge["background"]))
            if ratio < 4.5:
                raise SystemExit(
                    f"the verdict badge in the {name} report has a contrast ratio of "
                    f"{ratio:.2f}, below the 4.5 needed to read it"
                )
            badges[name] = {**badge, "contrast_ratio": round(ratio, 2)}

        for plan in SHOT_PLAN:
            path = reports[plan["report"]]
            page.goto(path.resolve().as_uri())
            page.wait_for_load_state("load")
            # A shot with no from_heading starts at the top of the report, which
            # is the only way to include the verdict badge: it sits above the
            # first h2, and the badge is the thing a reader looks at first.
            if plan["from_heading"] is None:
                start = {"top": 18, "height": page.evaluate("() => document.body.scrollHeight")}
            else:
                start = page.evaluate(HEADING_PROBE, plan["from_heading"])
            if start is None:
                raise SystemExit(
                    f"heading {plan['from_heading']!r} is gone, so the shot would be meaningless"
                )
            if plan["to_heading"] is None:
                end_top = start["height"]
            else:
                end = page.evaluate(HEADING_PROBE, plan["to_heading"])
                if end is None:
                    raise SystemExit(f"heading {plan['to_heading']!r} is gone")
                end_top = end["top"]
            top = max(start["top"] - 18, 0)
            height = max(end_top - top - 12, 80)
            content_right = page.evaluate(WIDTH_PROBE, [plan["from_heading"], plan["to_heading"]])
            width = min(VIEWPORT["width"] - 80, max(int(content_right or 0) - 40 + 24, 420))
            destination = SHOTS / f"{plan['name']}.png"
            page.screenshot(
                path=str(destination),
                full_page=True,
                clip={"x": 40, "y": top, "width": width, "height": height},
            )
            manifest.append(
                {
                    "image": str(destination.relative_to(REPO)),
                    "report": str(path.relative_to(REPO)),
                    "framed_by": [plan["from_heading"], plan["to_heading"]],
                    "clip": {"width": width, "height": height},
                    "caption": plan["caption"],
                    "device_scale_factor": SCALE,
                    "badge": badges[plan["report"]],
                    "bytes": destination.stat().st_size,
                }
            )
            print(f"wrote {destination.relative_to(REPO)} ({destination.stat().st_size} bytes)")

        browser.close()

    (SHOTS / "manifest.json").write_text(
        json.dumps({"shots": manifest}, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {(SHOTS / 'manifest.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
