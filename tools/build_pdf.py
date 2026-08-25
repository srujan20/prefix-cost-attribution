"""Lay out a markdown document as a PDF for offline reading.

Rendered with cmark-gfm, which is the library GitHub uses, so the tables and
fenced blocks come out the way they do on the repository page. Laid out by
Chromium through page.pdf(), because it is the only engine here that paginates
tables without breaking a row across a page.

House rules applied: plain black text, a ten point body, no decorative colour, no
cover page for the copy that lives in the repository, and a cover page carrying
this project's figures for the copy that is handed over.

Usage:
    python tools/build_pdf.py docs/defense-guide.md [--out PATH] [--cover]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cmarkgfm
from cmarkgfm.cmark import Options
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
SCRATCH = REPO / ".cache"

STYLE = """
@page { size: A4; margin: 18mm 16mm 16mm 16mm; }
body { font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       font-size: 10pt; line-height: 1.45; color: #000000; margin: 0; }
h1 { font-size: 17pt; margin: 0 0 8pt; }
h2 { font-size: 13pt; margin: 16pt 0 5pt; border-bottom: 0.6pt solid #000000;
     padding-bottom: 3pt; page-break-after: avoid; }
h3 { font-size: 11pt; margin: 12pt 0 4pt; page-break-after: avoid; }
p, li { font-size: 10pt; }
ul, ol { margin: 5pt 0 5pt 16pt; padding: 0; }
li { margin: 2pt 0; }
strong { font-weight: 650; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
       font-size: 8.8pt; }
pre { font-size: 8.6pt; line-height: 1.35; border: 0.5pt solid #000000; padding: 6pt;
      white-space: pre-wrap; page-break-inside: avoid; margin: 6pt 0; }
table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 6pt 0;
        page-break-inside: avoid; }
th, td { border: 0.5pt solid #000000; padding: 3pt 5pt; text-align: left;
         vertical-align: top; }
th { font-weight: 650; }
tr { page-break-inside: avoid; }
blockquote { margin: 6pt 0 6pt 10pt; padding-left: 8pt; border-left: 1pt solid #000000; }
.cover { page-break-after: always; }
.cover h1 { font-size: 22pt; margin: 0 0 2pt; }
.cover .subject { font-size: 11pt; margin: 0 0 18pt; }
.cover table { width: 100%; font-size: 9.5pt; }
.cover .note { font-size: 9pt; margin-top: 14pt; }
"""

FOOTER = (
    '<div style="width:100%;font-size:8pt;font-family:Helvetica,Arial,sans-serif;'
    'padding:0 16mm;display:flex;justify-content:space-between;color:#000;">'
    "<span>Srujan Sadineni  |  prefix-cost-attribution</span>"
    '<span class="pageNumber"></span></div>'
)
HEADER = '<div style="font-size:0"></div>'

COVER_ROWS = (
    ("What a per request token bill collects, against the spend", "per_request_collects", " times"),
    ("The same bill against a fair share of prefill", "per_request_vs_fair_prefill", " times"),
    ("Worst single tenant on that bill", "worst_per_request_tenant_ratio", " times"),
    ("What the cache aware bill moves a tenant by, median", "marginal_median_spread", ""),
    ("Worst tenant, across arrival orders of the same requests", "marginal_worst_spread", ""),
    ("Family replays where the first arriver paid the most", "first_arriver_marginal", " of 360"),
    ("What chance alone would give", "first_arriver_chance", ""),
    ("What the fair split collects, against the spend", "fair_collects", ""),
    ("Tenants whose fair share moved between orderings", "stable_tenants", " of 120 stable"),
    ("Grid cells where some scheme had all three properties", "grid_all_three", " of 24"),
    ("Of those, cells where the cache recomputed", "grid_all_three_when_recomputing", ""),
    ("What LRU spends over an optimal policy at the same capacity", "lru_excess_at_shipped", ""),
    ("Exact allocation against a sampled one", "bench_sampled_ratio", " times faster"),
    ("Error the smallest usable sample still carries", "bench_error_at_that_sample", ""),
    ("Tests, all of them measured", "tests_total", ""),
    ("Line coverage", "coverage_line_pct", " percent"),
)


def cover_html() -> str:
    metrics = json.loads((REPO / "docs" / "metrics.json").read_text(encoding="utf-8"))["metrics"]
    rows = "".join(
        f"<tr><td>{label}</td><td style='text-align:right'>{metrics[key]}{suffix}</td></tr>"
        for label, key, suffix in COVER_ROWS
    )
    return f"""<div class="cover">
<h1>prefix-cost-attribution</h1>
<p class="subject">Defense guide. With a prefix cache, what a request costs depends
on the sequence it arrived in. Three properties are worth wanting from a bill: it
sums to what the server spent, it does not change with arrival order, and shared
work is split fairly. No scheme has all three whenever the cache recomputes a
prefix it had already computed. This is what that costs, measured.</p>
<table><tbody>{rows}</tbody></table>
<p class="note">Every figure above is produced by code in the repository, re-measured
by CI on every push, and checked against the prose of this document by
tools/check_numbers.py. Reproduce all of it with one command: make verify.</p>
</div>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="markdown file to lay out")
    parser.add_argument("--out", help="output PDF path")
    parser.add_argument(
        "--cover", action="store_true", help="prepend a cover page carrying the figures"
    )
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_absolute():
        source = REPO / source
    if not source.is_file():
        raise SystemExit(f"{source} not found")
    destination = Path(args.out) if args.out else source.with_suffix(".pdf")

    body = cmarkgfm.markdown_to_html_with_extensions(
        source.read_text(encoding="utf-8"),
        options=Options.CMARK_OPT_UNSAFE,
        extensions=["table", "autolink", "strikethrough", "tagfilter"],
    )
    page_html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{STYLE}</style></head><body>"
        + (cover_html() if args.cover else "")
        + body
        + "</body></html>"
    )
    SCRATCH.mkdir(parents=True, exist_ok=True)
    scratch = SCRATCH / f"{source.stem}-print.html"
    scratch.write_text(page_html, encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(scratch.resolve().as_uri())
        page.wait_for_load_state("load")
        page.pdf(
            path=str(destination),
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=HEADER,
            footer_template=FOOTER,
            margin={"top": "18mm", "bottom": "16mm", "left": "0mm", "right": "0mm"},
        )
        browser.close()
    print(f"wrote {destination} ({destination.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
