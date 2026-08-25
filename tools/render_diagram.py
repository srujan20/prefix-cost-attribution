"""Render the architecture diagram to a committed SVG.

Written by hand rather than through a diagramming library, for four reasons that
each cost time on other projects.

GitHub's lazily loaded Mermaid renderer sometimes reports "Unable to render rich
display" for a diagram that parses correctly everywhere else. There is nothing to
fix in the source and no way to fix it from the repository, so the diagram is
committed as an image instead.

A diagram with HTML labels is not well formed XML, because the labels end up
inside a foreignObject with unclosed br tags. It displays when injected into a
live page and fails silently as an img src, with naturalWidth 0 and nothing in
any console. This file emits plain text elements and nothing else.

An img src needs intrinsic dimensions. A percentage width with no height leaves a
browser without an aspect ratio and it picks a default.

A transparent background is not theme neutral. Node fills are light with dark
text either way, so an unfilled label comes out dark grey on near black in a dark
theme. One opaque rectangle covers the whole viewBox.

Usage:
    python tools/render_diagram.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parents[1]
OUTPUT = REPO / "docs" / "diagrams" / "architecture.svg"
MANIFEST = REPO / "docs" / "diagrams" / "manifest.json"

WIDTH = 1000
HEIGHT = 940
FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

INK = "#14171a"
MUTED = "#5b636b"
LINE = "#9aa2aa"
PAPER = "#ffffff"
PANEL = "#f2f4f6"
BAD_FILL = "#fdf0d8"
BAD_EDGE = "#e5c583"
GOOD_FILL = "#eef4ea"
GOOD_EDGE = "#b3cba4"
# Label inks, darker than the box edges so they clear a contrast ratio of 4.5
# against white. The edge colours do not, which is why they are not reused here.
BAD_LABEL = "#7a5f16"
GOOD_LABEL = "#355c24"
DATA_FILL = "#e8eef5"
DATA_EDGE = "#a9bed4"
NEUTRAL_FILL = "#ffffff"
NEUTRAL_EDGE = "#8f9aa4"


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    width: int
    height: int
    title: str
    lines: tuple[str, ...] = ()
    fill: str = NEUTRAL_FILL
    edge: str = NEUTRAL_EDGE
    mono: bool = False

    @property
    def centre_x(self) -> int:
        return self.x + self.width // 2

    @property
    def bottom(self) -> int:
        return self.y + self.height


WORKLOAD = Box(
    40,
    62,
    920,
    100,
    "One deployment, many tenants, and prompts that share their opening tokens",
    (
        "Tenants sharing a system prompt share its tokens. Turns of one conversation share the",
        "history. So the server sends far more prompt tokens than it ever processes, and the",
        "difference belongs to nobody in particular. That difference is what has to be divided.",
    ),
    fill=DATA_FILL,
    edge=DATA_EDGE,
)

SENT = Box(
    40,
    216,
    290,
    160,
    "tokens sent",
    (
        "every prompt token of",
        "every request, counted",
        "once per request",
        "",
        "what a per request bill",
        "charges for",
    ),
    fill=BAD_FILL,
    edge=BAD_EDGE,
)

PROCESSED = Box(
    355,
    216,
    290,
    160,
    "tokens processed",
    (
        "the misses: what the",
        "cache did not already",
        "hold when each request",
        "arrived",
        "",
        "what the server spent",
    ),
    fill=GOOD_FILL,
    edge=GOOD_EDGE,
)

DISTINCT = Box(
    670,
    216,
    290,
    160,
    "distinct prefix tokens",
    (
        "the nodes of the trie of",
        "every request's tokens",
        "",
        "what a cache that never",
        "evicts would process,",
        "in any arrival order",
    ),
    fill=DATA_FILL,
    edge=DATA_EDGE,
)

SCHEMES = (
    Box(
        40,
        470,
        172,
        150,
        "per_request",
        ("sums to bill  no", "same order    yes", "fair split    no", "", "the usual invoice"),
        fill=BAD_FILL,
        edge=BAD_EDGE,
        mono=True,
    ),
    Box(
        227,
        470,
        172,
        150,
        "marginal",
        (
            "sums to bill  YES",
            "same order    no",
            "fair split    no",
            "",
            "cache aware, and",
            "a record of order",
        ),
        fill=BAD_FILL,
        edge=BAD_EDGE,
        mono=True,
    ),
    Box(
        414,
        470,
        172,
        150,
        "equal_split",
        (
            "sums to bill  no",
            "same order    yes",
            "fair split    no",
            "",
            "one conversation",
            "pays like a thousand",
        ),
        fill=BAD_FILL,
        edge=BAD_EDGE,
        mono=True,
    ),
    Box(
        601,
        470,
        172,
        150,
        "proportional",
        (
            "sums to bill  no",
            "same order    yes",
            "fair split    no",
            "",
            "charges for prefixes",
            "never used",
        ),
        fill=BAD_FILL,
        edge=BAD_EDGE,
        mono=True,
    ),
    Box(
        788,
        470,
        172,
        150,
        "shapley",
        (
            "sums to bill  yes*",
            "same order    yes",
            "fair split    YES",
            "",
            "* only when the",
            "cache recomputes",
            "nothing",
        ),
        fill=GOOD_FILL,
        edge=GOOD_EDGE,
        mono=True,
    ),
)

VERDICTS = Box(
    40,
    688,
    920,
    116,
    "Three verdicts, and the order between them is the design decision",
    (
        "not-an-attribution, exit 2, when the scheme does not sum to what the server spent. It",
        "outranks the others: an unstable division of the wrong total is not a finding about",
        "ordering.   order-dependent, exit 1.   attribution-sound, exit 0, and only then.",
    ),
    fill=PANEL,
    edge=MUTED,
)


def element(parent: ElementTree.Element, tag: str, **attributes: object) -> ElementTree.Element:
    return ElementTree.SubElement(
        parent, tag, {key.replace("_", "-"): str(value) for key, value in attributes.items()}
    )


def text(
    parent: ElementTree.Element,
    x: int,
    y: int,
    content: str,
    *,
    size: float = 13,
    weight: str = "400",
    fill: str = INK,
    anchor: str = "start",
    family: str = FONT,
    preserve: bool = False,
) -> None:
    node = element(
        parent,
        "text",
        x=x,
        y=y,
        fill=fill,
        font_size=size,
        font_weight=weight,
        font_family=family,
        text_anchor=anchor,
    )
    if preserve:
        # Runs of spaces are the alignment in the monospaced boxes, and SVG
        # collapses them by default, which turned a two column layout into
        # ragged prose the first time this was rendered.
        node.set("xml:space", "preserve")
    # The content goes on the text element itself rather than into a child tspan.
    # With a child, ElementTree.indent adds a newline and indentation before it,
    # and under xml:space="preserve" that indentation renders as a leading gap
    # that shifted every monospaced line to the right.
    node.text = content


def draw_box(parent: ElementTree.Element, box: Box) -> None:
    element(
        parent,
        "rect",
        x=box.x,
        y=box.y,
        width=box.width,
        height=box.height,
        rx=6,
        fill=box.fill,
        stroke=box.edge,
        stroke_width=1.2,
    )
    text(parent, box.x + 14, box.y + 25, box.title, size=13.5, weight="650")
    for index, line in enumerate(box.lines):
        text(
            parent,
            box.x + 14,
            box.y + 46 + index * (16 if box.mono else 19),
            line,
            size=11 if box.mono else 11.5,
            fill=MUTED,
            family=MONO if box.mono else FONT,
            preserve=box.mono,
        )


def draw_arrow(parent: ElementTree.Element, x1: int, y1: int, x2: int, y2: int) -> None:
    element(
        parent,
        "line",
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        stroke=LINE,
        stroke_width=1.2,
        marker_end="url(#arrow)",
    )


def build() -> ElementTree.Element:
    root = ElementTree.Element(
        "svg",
        {
            "xmlns": "http://www.w3.org/2000/svg",
            "viewBox": f"0 0 {WIDTH} {HEIGHT}",
            "width": str(WIDTH),
            "height": str(HEIGHT),
            "role": "img",
        },
    )
    element(root, "rect", x=0, y=0, width=WIDTH, height=HEIGHT, fill=PAPER)

    defs = ElementTree.SubElement(root, "defs")
    marker = ElementTree.SubElement(
        defs,
        "marker",
        {
            "id": "arrow",
            "viewBox": "0 0 10 10",
            "refX": "9",
            "refY": "5",
            "markerWidth": "7",
            "markerHeight": "7",
            "orient": "auto-start-reverse",
        },
    )
    element(marker, "path", d="M 0 0 L 10 5 L 0 10 z", fill=LINE)

    text(
        root,
        40,
        38,
        "Three token counts, five ways to divide one of them, and three properties",
        size=16,
        weight="650",
    )

    draw_box(root, WORKLOAD)

    text(root, 40, 194, "counted from the same workload", size=11, weight="600", fill=MUTED)
    first_gap = (SENT.x + SENT.width + PROCESSED.x) // 2
    second_gap = (PROCESSED.x + PROCESSED.width + DISTINCT.x) // 2
    text(root, first_gap, 188, "larger", size=11.5, weight="700", fill=BAD_LABEL, anchor="middle")
    text(root, first_gap, 204, "by the hit share", size=10, fill=BAD_LABEL, anchor="middle")
    text(root, second_gap, 188, "larger", size=11.5, weight="700", fill=GOOD_LABEL, anchor="middle")
    text(
        root,
        second_gap,
        204,
        "by what was recomputed",
        size=10,
        fill=GOOD_LABEL,
        anchor="middle",
    )

    for box in (SENT, PROCESSED, DISTINCT):
        draw_box(root, box)

    joint = SENT.y + SENT.height // 2
    element(
        root,
        "line",
        x1=SENT.x + SENT.width + 2,
        y1=joint,
        x2=PROCESSED.x - 2,
        y2=joint,
        stroke=BAD_EDGE,
        stroke_width=3,
    )
    element(
        root,
        "line",
        x1=PROCESSED.x + PROCESSED.width + 2,
        y1=joint,
        x2=DISTINCT.x - 2,
        y2=joint,
        stroke=GOOD_EDGE,
        stroke_width=3,
        stroke_dasharray="5 4",
    )

    # One short label, then a spine and a bus. An arrow per box from the row
    # above would have to cross this text, and a diagram whose arrows run through
    # its own sentences is harder to read than one with a bus.
    text(
        root,
        40,
        SENT.bottom + 28,
        "each scheme divides the middle column",
        size=11,
        weight="600",
        fill=MUTED,
    )

    bus = SCHEMES[0].y - 30
    element(
        root,
        "line",
        x1=PROCESSED.centre_x,
        y1=PROCESSED.bottom + 4,
        x2=PROCESSED.centre_x,
        y2=bus,
        stroke=LINE,
        stroke_width=1.2,
    )
    element(
        root,
        "line",
        x1=SCHEMES[0].centre_x,
        y1=bus,
        x2=SCHEMES[-1].centre_x,
        y2=bus,
        stroke=LINE,
        stroke_width=1.2,
    )
    for box in SCHEMES:
        draw_box(root, box)
        draw_arrow(root, box.centre_x, bus, box.centre_x, box.y - 4)

    fan_in = SCHEMES[0].bottom + 28
    for box in SCHEMES:
        element(
            root,
            "line",
            x1=box.centre_x,
            y1=box.bottom + 2,
            x2=box.centre_x,
            y2=fan_in,
            stroke=LINE,
            stroke_width=1.2,
        )
    element(
        root,
        "line",
        x1=SCHEMES[0].centre_x,
        y1=fan_in,
        x2=SCHEMES[-1].centre_x,
        y2=fan_in,
        stroke=LINE,
        stroke_width=1.2,
    )
    draw_arrow(root, VERDICTS.centre_x, fan_in, VERDICTS.centre_x, VERDICTS.y - 4)
    draw_box(root, VERDICTS)

    footer = VERDICTS.bottom + 30
    text(
        root,
        40,
        footer,
        "amber: not a division of what the server spent, so whatever it divides, it is "
        "not the bill",
        size=10.5,
        fill=BAD_LABEL,
    )
    text(
        root,
        40,
        footer + 18,
        "green: a division of the bill, order independent and fair, whenever the cache "
        "recomputes nothing",
        size=10.5,
        fill=GOOD_LABEL,
    )
    text(
        root,
        40,
        footer + 42,
        "Exactly one column has an uppercase YES on the first row, and it is not the column "
        "that has one on the third. That is the finding.",
        size=11,
        fill=MUTED,
    )
    text(
        root,
        40,
        footer + 61,
        "The Shapley column is exact rather than sampled: on a tree, a node's cost divides "
        "equally among the tenants using it.",
        size=11,
        fill=MUTED,
    )
    text(
        root,
        40,
        footer + 84,
        "tools/render_diagram.py, plain text elements only, no foreignObject",
        size=10,
        fill=LINE,
        family=MONO,
    )
    return root


def main() -> int:
    svg = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.indent(svg, space="  ")
    payload = ElementTree.tostring(svg, encoding="unicode", xml_declaration=False)
    OUTPUT.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + payload + "\n", encoding="utf-8")

    # Parsing the file back is the check that matters: an img src silently fails
    # to load a document that is not well formed, with nothing in any console.
    ElementTree.parse(OUTPUT)
    MANIFEST.write_text(
        json.dumps(
            {
                "file": str(OUTPUT.relative_to(REPO)),
                "width": WIDTH,
                "height": HEIGHT,
                "generated_by": "tools/render_diagram.py",
                "labels": "plain text elements only, no foreignObject",
                "background": "one opaque rectangle covering the whole viewBox",
                "boxes": len(SCHEMES) + 5,
                "bytes": OUTPUT.stat().st_size,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUTPUT.relative_to(REPO)} ({OUTPUT.stat().st_size} bytes, {WIDTH}x{HEIGHT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
