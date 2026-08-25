"""Render the README the way GitHub does, and assert every image actually loads.

python-markdown disagrees with GitHub about exactly this kind of file in three
ways: it does not parse markdown inside a centred div, it needs an extension for
fenced code, and it does not apply the blank line rule inside an HTML block. So
the rendering here uses cmark-gfm, which is the library GitHub itself uses.

Then the rendered page is loaded in Chromium and every image is checked for a
non zero naturalWidth. That is the only way to catch an SVG that is not well
formed: it fails as an img src with no console message, showing a broken image
icon and nothing else.

Usage:
    python tools/check_readme.py [README.md ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import cmarkgfm
from cmarkgfm.cmark import Options
from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
SCRATCH = REPO / ".cache"

PROBE = """() => Array.from(document.images).map(image => ({
  src: image.getAttribute('src'),
  width: image.naturalWidth,
  height: image.naturalHeight,
  complete: image.complete,
}))"""


def render(path: Path) -> str:
    body = cmarkgfm.markdown_to_html_with_extensions(
        path.read_text(encoding="utf-8"),
        options=Options.CMARK_OPT_UNSAFE,
        extensions=["table", "autolink", "strikethrough", "tagfilter"],
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<base href="file://' + str(REPO) + '/">'
        "</head><body>" + body + "</body></html>"
    )


def main(argv: list[str] | None = None) -> int:
    names = argv if argv else ["README.md"]
    SCRATCH.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        for name in names:
            source = REPO / name
            if not source.is_file():
                failures.append(f"{name}: not found")
                continue
            rendered = SCRATCH / f"{source.stem}-rendered.html"
            rendered.write_text(render(source), encoding="utf-8")
            page.goto(rendered.resolve().as_uri())
            page.wait_for_load_state("load")
            page.wait_for_timeout(400)
            images = page.evaluate(PROBE)
            print(f"{name}: {len(images)} images")
            for image in images:
                status = "ok" if image["width"] and image["height"] else "BROKEN"
                print(f"  {status:7} {image['width']}x{image['height']}  {image['src']}")
                if status == "BROKEN" and not str(image["src"]).startswith("http"):
                    failures.append(f"{name}: {image['src']} has a natural size of zero")
            if not images:
                failures.append(f"{name}: no images at all, which is not what this repo ships")
        browser.close()

    if failures:
        for failure in failures:
            print(f"FAIL  {failure}", file=sys.stderr)
        return 1
    print("every local image resolves with a non zero natural size")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
