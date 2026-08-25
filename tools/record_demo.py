"""Record a replay of real terminal output, not a screen recording.

Every line of text in the video is captured stdout from a command that actually
ran, with its real exit code, and each segment is paced by that command's
measured wall time, so a slow command looks slow. The README says so, because a
demo that looks hand written is worth less than no demo.

Pipeline: run each command and capture stdout, stderr, exit code and duration;
generate a self contained HTML player that types the command and reveals the
captured output at the measured pace; record the page with Playwright to webm;
transcode to an MP4 and a two pass palette GIF that autoplays inline on GitHub.

The import is warmed by a setup command that is not recorded. Without it the
first segment pays for loading pandas and the pacing of the whole video is
wrong.

Usage:
    python tools/record_demo.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[1]
VIDEO = REPO / "docs" / "video"
SCRATCH = REPO / ".cache" / "video"

WIDTH = 960
HEIGHT = 600
FPS = 8
GIF_WIDTH = 620

WARMUP = [sys.executable, "-m", "prefixcost", "--version"]

# Ordered as the argument, not as the feature list: what the configuration is,
# then the bill almost everybody sends, then the bill a cache aware team arrives
# at, then what the cache is actually doing, then the receipts.
SEGMENTS = (
    ("What this configuration will build, and what each scheme is", ["plan"]),
    (
        "A per request token count, audited. Exit 2: it is not a division of the spend",
        ["audit", "--seed", "11", "--scheme", "per_request", "--orderings", "6"],
    ),
    (
        "Charge for the tokens actually processed. Exit 1: the shares move with arrival order",
        ["audit", "--seed", "11", "--scheme", "marginal", "--orderings", "6"],
    ),
    (
        "What the prefix cache buys, by capacity. Above the working set it evicts nothing",
        ["cache", "--seed", "11", "--capacities", "0", "2000", "8000", "24613", "32000"],
    ),
)

RECEIPTS = ("Every figure in the documents, re-measured", ["tools/check_numbers.py"])


@dataclass
class Captured:
    label: str
    command: str
    output: str
    exit_code: int
    seconds: float


def capture(label: str, argv: list[str], *, module: bool) -> Captured:
    command = f"python -m prefixcost {' '.join(argv)}" if module else f"python {' '.join(argv)}"
    full = [sys.executable, "-m", "prefixcost", *argv] if module else [sys.executable, *argv]
    started = time.perf_counter()
    completed = subprocess.run(full, cwd=REPO, capture_output=True, text=True, check=False)
    elapsed = time.perf_counter() - started
    output = completed.stdout
    if completed.stderr.strip():
        output = f"{output}{completed.stderr}"
    print(f"  {command}  exit {completed.returncode}  {elapsed:.2f}s")
    return Captured(
        label=label,
        command=command,
        output=output.rstrip("\n"),
        exit_code=completed.returncode,
        seconds=round(elapsed, 3),
    )


PLAYER = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;background:#12161b;}
#screen{width:%(width)dpx;height:%(height)dpx;padding:18px 20px;box-sizing:border-box;
  font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#d7dde4;
  overflow:hidden;}
.label{color:#8fb4d9;margin:10px 0 6px;font-weight:600;}
.cmd{color:#f0f4f8;}
.cmd .prompt{color:#6fbf73;}
.out{color:#b9c2cb;white-space:pre-wrap;}
.code{color:#e5a35c;}
</style></head><body><div id="screen"></div><script>
const segments = %(segments)s;
const screen = document.getElementById('screen');
const sleep = ms => new Promise(r => setTimeout(r, ms));
function trim() {
  while (screen.scrollHeight > screen.clientHeight && screen.firstChild) {
    screen.removeChild(screen.firstChild);
  }
}
async function type(node, value, per) {
  for (const character of value) {
    node.textContent += character;
    trim();
    await sleep(per);
  }
}
async function run() {
  for (const segment of segments) {
    const label = document.createElement('div');
    label.className = 'label';
    label.textContent = '# ' + segment.label;
    screen.appendChild(label); trim(); await sleep(320);

    const line = document.createElement('div');
    line.className = 'cmd';
    const prompt = document.createElement('span');
    prompt.className = 'prompt';
    prompt.textContent = '$ ';
    line.appendChild(prompt);
    const typed = document.createElement('span');
    line.appendChild(typed);
    screen.appendChild(line); trim();
    await type(typed, segment.command, 16);
    await sleep(180);

    const lines = segment.output.split('\\n');
    const per = Math.max(12, Math.min(70, (segment.seconds * 1000) / Math.max(lines.length, 1)));
    const out = document.createElement('div');
    out.className = 'out';
    screen.appendChild(out);
    for (const text of lines) {
      out.textContent += text + '\\n';
      trim();
      await sleep(per);
    }
    const code = document.createElement('div');
    code.className = 'code';
    code.textContent = 'exit ' + segment.exit_code;
    screen.appendChild(code); trim();
    await sleep(700);
  }
  await sleep(1200);
  window.__done = true;
}
run();
</script></body></html>"""


def main() -> int:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg is not on PATH, so the MP4 and GIF cannot be produced. The webm from "
            "Playwright would still be written, but the README embeds the GIF, so this "
            "boundary is recorded rather than worked around."
        )
    SCRATCH.mkdir(parents=True, exist_ok=True)
    VIDEO.mkdir(parents=True, exist_ok=True)

    print("warming the import so the first segment is not paying for pandas")
    subprocess.run(WARMUP, cwd=REPO, capture_output=True, check=False)

    print("capturing real output")
    captured = [capture(label, argv, module=True) for label, argv in SEGMENTS]
    captured.append(capture(RECEIPTS[0], RECEIPTS[1], module=False))

    payload = json.dumps(
        [
            {
                "label": item.label,
                "command": item.command,
                "output": item.output,
                "exit_code": item.exit_code,
                "seconds": item.seconds,
            }
            for item in captured
        ]
    )
    page_path = SCRATCH / "player.html"
    page_path.write_text(
        PLAYER % {"width": WIDTH, "height": HEIGHT, "segments": payload}, encoding="utf-8"
    )

    print("recording the replay")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=str(SCRATCH),
            record_video_size={"width": WIDTH, "height": HEIGHT},
        )
        page = context.new_page()
        page.goto(page_path.resolve().as_uri())
        page.wait_for_function("() => window.__done === true", timeout=180_000)
        video = page.video
        context.close()
        source = Path(video.path()) if video is not None else None
        browser.close()
    if source is None or not source.is_file():
        raise SystemExit("Playwright did not produce a video file")

    mp4 = VIDEO / "demo.mp4"
    gif = VIDEO / "demo.gif"
    palette = SCRATCH / "palette.png"
    run_ffmpeg = lambda args: subprocess.run(  # noqa: E731
        ["ffmpeg", "-y", "-loglevel", "error", *args], check=True
    )
    run_ffmpeg(["-i", str(source), "-movflags", "+faststart", "-pix_fmt", "yuv420p", str(mp4)])
    scale = f"fps={FPS},scale={GIF_WIDTH}:-1:flags=lanczos"
    run_ffmpeg(["-i", str(source), "-vf", f"{scale},palettegen=stats_mode=diff", str(palette)])
    run_ffmpeg(
        [
            "-i",
            str(source),
            "-i",
            str(palette),
            "-lavfi",
            f"{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=3",
            str(gif),
        ]
    )

    manifest = {
        "note": (
            "every line of terminal text is captured stdout from a command that ran, and "
            "each segment is paced by that command's measured wall time"
        ),
        "player": "generated by tools/record_demo.py, recorded with Chromium",
        "fps": FPS,
        "gif_width": GIF_WIDTH,
        "mp4_bytes": mp4.stat().st_size,
        "gif_bytes": gif.stat().st_size,
        "commands": [
            {
                "label": item.label,
                "command": item.command,
                "exit_code": item.exit_code,
                "seconds": item.seconds,
                "output_lines": len(item.output.splitlines()),
            }
            for item in captured
        ],
    }
    (VIDEO / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {mp4.relative_to(REPO)} ({mp4.stat().st_size} bytes)")
    print(f"wrote {gif.relative_to(REPO)} ({gif.stat().st_size} bytes)")
    print(f"wrote {(VIDEO / 'manifest.json').relative_to(REPO)}")
    if gif.stat().st_size > 5_000_000:
        raise SystemExit(
            f"the GIF is {gif.stat().st_size} bytes, over the 5 MB that autoplays inline on "
            "GitHub. Lower FPS or GIF_WIDTH rather than shipping a still frame with a "
            "play button on it."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
