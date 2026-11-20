#!/usr/bin/env python3
"""Draw the illustrated StreamFeed system overview.

Usage: scripts/render-overview.py [output-dir]     (default: docs/diagrams)

Writes streamfeed-overview.svg, and streamfeed-overview.png when a headless
Chrome is available (the one downloaded by scripts/render-diagrams.sh, or
google-chrome / chromium on PATH). Edit the "layout" section below to change
the drawing. Shape helpers: box, stacked_box, cylinder, cylinder_stack,
monitor, person, arrow, label.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ORANGE, ORANGE_D = "#F9B54C", "#E39B2B"
TEAL, TEAL_D = "#72C8CF", "#E39B2B"
INK, TEXT, LABEL = "#4A4A4A", "#4A4A4A", "#555555"
FONT = "'Source Sans Pro','Open Sans','Segoe UI',Helvetica,Arial,sans-serif"

W, H = 1140, 1240
out = []
add = out.append


def box(x, y, w, h, title, sub=None, fill=ORANGE):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{ORANGE_D}" stroke-width="1.5"/>')
    cy = y + h / 2
    if sub:
        add(f'<text x="{x+w/2}" y="{cy-4}" class="t">{title}</text>')
        add(f'<text x="{x+w/2}" y="{cy+16}" class="s">{sub}</text>')
    else:
        add(f'<text x="{x+w/2}" y="{cy+6}" class="t">{title}</text>')


def stacked_box(x, y, w, h, title, sub=None, n=3, d=10):
    for i in range(n - 1, 0, -1):
        fill = TEAL if i % 2 else ORANGE
        add(f'<rect x="{x-i*d}" y="{y-i*d}" width="{w}" height="{h}" fill="{fill}" stroke="{ORANGE_D}" stroke-width="1.5"/>')
    box(x, y, w, h, title, sub)


def cylinder(cx, top, rx, h, title=None, sub=None, ry=14, fill=TEAL):
    add(
        f'<path d="M{cx-rx},{top} v{h} a{rx},{ry} 0 0 0 {2*rx},0 v{-h}" '
        f'fill="{fill}" stroke="{ORANGE_D}" stroke-width="3"/>'
    )
    add(f'<ellipse cx="{cx}" cy="{top}" rx="{rx}" ry="{ry}" fill="{fill}" stroke="{ORANGE_D}" stroke-width="3"/>')
    if title:
        mid = top + h / 2 + 8
        if sub:
            add(f'<text x="{cx}" y="{mid-4}" class="t">{title}</text>')
            for k, line in enumerate(sub.split("|")):
                add(f'<text x="{cx}" y="{mid+16+k*17}" class="s">{line}</text>')
        else:
            add(f'<text x="{cx}" y="{mid+6}" class="t">{title}</text>')


def cylinder_stack(cx, top, rx, h, title, sub=None, n=3, dx=-26, dy=-26):
    for i in range(n - 1, 0, -1):
        cylinder(cx + i * dx, top + i * dy, rx, h)
    cylinder(cx, top, rx, h, title, sub)


def monitor(cx, top, w=150, h=100):
    x = cx - w / 2
    add(f'<rect x="{x}" y="{top}" width="{w}" height="{h}" rx="8" fill="{INK}"/>')
    add(f'<rect x="{x+9}" y="{top+9}" width="{w-18}" height="{h-32}" fill="{ORANGE}"/>')
    add(f'<rect x="{x+9}" y="{top+h-20}" width="{w-18}" height="9" fill="{ORANGE}"/>')
    add(f'<rect x="{cx-12}" y="{top+h}" width="24" height="16" fill="{INK}"/>')
    add(f'<rect x="{cx-45}" y="{top+h+14}" width="90" height="10" rx="5" fill="{INK}"/>')


def person(cx, top, color, label, sub=None):
    # head, hair, body, open book
    add(f'<circle cx="{cx}" cy="{top+22}" r="17" fill="{color}" stroke="{INK}" stroke-width="2"/>')
    add(f'<path d="M{cx-17},{top+20} a17,17 0 0 1 34,0 q-17,-6 -34,0z" fill="{INK}"/>')
    add(f'<path d="M{cx-32},{top+80} q0,-36 32,-36 q32,0 32,36z" fill="{color}" stroke="{INK}" stroke-width="2"/>')
    add(f'<rect x="{cx-26}" y="{top+58}" width="52" height="24" fill="#fff" stroke="{INK}" stroke-width="2"/>')
    add(f'<line x1="{cx}" y1="{top+58}" x2="{cx}" y2="{top+82}" stroke="{INK}" stroke-width="2"/>')
    add(f'<circle cx="{cx-30}" cy="{top+72}" r="7" fill="{color}" stroke="{INK}" stroke-width="2"/>')
    add(f'<circle cx="{cx+30}" cy="{top+72}" r="7" fill="{color}" stroke="{INK}" stroke-width="2"/>')
    add(f'<text x="{cx}" y="{top+108}" class="actor">{label}</text>')
    if sub:
        add(f'<text x="{cx}" y="{top+126}" class="s">{sub}</text>')


def arrow(points, both=False, dashed=False):
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    extra = ' stroke-dasharray="5,5"' if dashed else ""
    start = ' marker-start="url(#ah)"' if both else ""
    add(f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="1.6"{extra}{start} marker-end="url(#ah)"/>')


def label(x, y, text, anchor="middle"):
    for k, line in enumerate(text.split("|")):
        add(f'<text x="{x}" y="{y+k*22}" class="lbl" text-anchor="{anchor}">{line}</text>')


# ---------------------------------------------------------------- layout
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

# Top: user + web UI
person(80, 30, ORANGE, "You", "(browser)")
label(255, 52, "Search · Filter|Save · Run now")
arrow([(150, 82), (360, 82)], both=True)
monitor(450, 25)
box(330, 165, 240, 52, "Web UI", "Next.js · Basic Auth")

# API
arrow([(450, 217), (450, 300)], both=True)
label(462, 266, "/api proxy · JSON", anchor="start")
box(330, 300, 240, 52, "API", "FastAPI · never crawls")

# Postgres (stack)
arrow([(450, 352), (450, 402)], both=True)
label(462, 385, "SQL", anchor="start")
cylinder_stack(470, 470, 80, 105, "PostgreSQL", "posts · sources|crawl_runs · queue")

# Source sites (right, stacked cylinders like a cluster)
stacked_box(860, 300, 210, 64, "Source Sites", "ITviec · Facebook · LinkedIn")

# Worker frame
add(f'<rect x="40" y="660" width="830" height="300" rx="12" fill="none" stroke="{ORANGE_D}" stroke-width="1.5" stroke-dasharray="8,6"/>')
add('<text x="790" y="690" class="frame">Worker</text>')

box(80, 730, 200, 54, "Pipeline", "filter · normalize · dedup")
box(370, 730, 200, 54, "Crawl Runner", "lock · run · record")
stacked_box(650, 740, 200, 54, "Crawler Plugins", "httpx · Playwright")

arrow([(370, 757), (280, 757)])
label(325, 745, "RawPost[]")
arrow([(570, 757), (630, 757)])

# Local LLM (Ollama) used by the pipeline's hiring filter
box(50, 400, 200, 54, "Local LLM", "Ollama · VyLinh 3B")
arrow([(120, 730), (120, 454)], both=True)
label(110, 560, "hiring|post?", anchor="end")

# Pipeline -> Postgres
arrow([(180, 730), (180, 600), (380, 600)])
label(192, 622, "Upsert +|dedup", anchor="start")
# Runner <-> Postgres (claim queued runs, locks)
arrow([(470, 730), (470, 600)], both=True)
label(482, 622, "Claim runs ·|advisory lock", anchor="start")

# Crawlers -> source sites
arrow([(760, 720), (930, 364)])
label(860, 520, "HTTP ·|headless|browser", anchor="start")

# Scheduler & queue poller (dashed into runner)
box(110, 870, 200, 50, "Scheduler", "interval ± jitter")
box(560, 870, 200, 50, "Queue Poller", "every 15 s")
arrow([(260, 870), (420, 784)], dashed=True)
arrow([(610, 870), (520, 784)], dashed=True)

# Raw store + sessions (right, outside frame)
cylinder(1000, 620, 75, 80, "Raw Pages", "data/raw")
arrow([(850, 760), (925, 700)])
label(880, 752, "save page", anchor="start")
cylinder(1000, 1000, 75, 80, "Sessions", "data/sessions")
arrow([(940, 1000), (800, 800)])
label(905, 880, "load|storage_state", anchor="start")

# Bottom: owner + login CLI
person(80, 1010, TEAL, "You", "(manual login)")
arrow([(150, 1060), (375, 1060)], both=True)
label(260, 1045, "Log in · 2FA")
monitor(450, 1000, w=130, h=88)
box(350, 1130, 200, 50, "Login CLI", "headed browser")
arrow([(550, 1155), (700, 1155), (925, 1060)])
label(625, 1145, "save session")

# Footer
add(f'<text x="40" y="{H-22}" class="brand">StreamFeed</text>')
add(f'<text x="{W-40}" y="{H-22}" class="s" style="text-anchor:end">System overview · see docs/ARCHITECTURE.md</text>')

style = f"""
<style>
  text {{ font-family: {FONT}; fill: {TEXT}; }}
  .t {{ font-size: 18px; text-anchor: middle; font-weight: 600; }}
  .s {{ font-size: 13.5px; text-anchor: middle; fill: #5b4a2a; }}
  .lbl {{ font-size: 16px; font-weight: 700; fill: {LABEL}; }}
  .actor {{ font-size: 17px; text-anchor: middle; fill: #666; }}
  .frame {{ font-size: 16px; font-weight: 700; fill: {ORANGE_D}; letter-spacing: 1px; }}
  .brand {{ font-size: 22px; font-weight: 700; fill: {INK}; }}
</style>"""
defs = f"""<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker></defs>"""
svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">{style}{defs}\n' + "\n".join(out) + "\n</svg>\n"


def find_chrome() -> str | None:
    # Prefer the headless shell downloaded by mermaid-cli, then any Chrome/Chromium on PATH.
    cache = Path.home() / ".cache/puppeteer/chrome-headless-shell"
    found = sorted(cache.glob("*/*/chrome-headless-shell"))
    if found:
        return str(found[-1])
    for name in ("chrome-headless-shell", "google-chrome", "chromium", "chromium-browser"):
        if path := shutil.which(name):
            return path
    return None


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else repo_root / "docs/diagrams"
    out_dir.mkdir(parents=True, exist_ok=True)
    svg_path = out_dir / "streamfeed-overview.svg"
    svg_path.write_text(svg, encoding="utf-8")
    print(f"ok    {svg_path}")

    chrome = find_chrome()
    if not chrome:
        print("skip  PNG: no headless Chrome found (run scripts/render-diagrams.sh once)")
        return
    png_path = out_dir / "streamfeed-overview.png"
    subprocess.run(
        [chrome, "--headless", "--no-sandbox", "--hide-scrollbars", "--disable-gpu", f"--window-size={W},{H}",
         f"--screenshot={png_path}", svg_path.resolve().as_uri()],
        check=True, capture_output=True, timeout=60,
    )
    print(f"ok    {png_path}")


if __name__ == "__main__":
    main()
