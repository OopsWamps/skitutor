"""Adversarial re-check of the tasks.html table overflow claim.

Renders tasks.html fresh via Jinja2 for several filenames (including a short
control), loads each dump in headless Chromium at 390x844, and reports whether
the table exceeds its panel and whether the document scrolls horizontally.
"""
import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(r"D:/vibecoding/skibot")
TPL = ROOT / "skibot" / "web" / "templates"
OUT = ROOT / ".scratch"

env = Environment(
    loader=FileSystemLoader(str(TPL)),
    autoescape=select_autoescape(["html", "xml"]),
)

names = {
    "ctl_short": "a.mp4",
    "gopr": "GOPR0123.MP4",
    "pxl": "PXL_20250101_123456789.mp4",
    "long43": "VID_20250101_123456789_M_CAM1_ski_run.mp4x",
}

rendered = {}
for key, name in names.items():
    now = datetime.datetime(2025, 1, 1, 12, 34, 0)
    tasks = [
        {"id": "abc123", "video_name": name, "level": "novice",
         "created_at": now, "status": "done", "error": None},
    ]
    html = env.get_template("tasks.html").render(
        tasks=tasks, active="tasks", low_density=False, error=None
    )
    path = OUT / f"_adv_tasks_{key}.html"
    path.write_text(html, encoding="utf-8")
    rendered[key] = path

from playwright.sync_api import sync_playwright

JS = """() => {
  const t = document.querySelector('table');
  const panel = document.querySelector('.panel');
  const cs = getComputedStyle(panel);
  const panelContentW = panel.clientWidth
    - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  const rect = t.getBoundingClientRect();
  return {
    innerWidth: window.innerWidth,
    docScrollWidth: document.documentElement.scrollWidth,
    pageHScrolls: document.documentElement.scrollWidth > window.innerWidth,
    panelContentW: Math.round(panelContentW),
    tableW: Math.round(rect.width),
    tableRight: Math.round(rect.right),
    viewReportLinkRight: Math.round(
      document.querySelector('a.ghost-link').getBoundingClientRect().right),
    overflowBeyondPanel: Math.round(rect.width - panelContentW),
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 390, "height": 844})
    for key, path in rendered.items():
        page.goto(path.resolve().as_uri())
        r = page.evaluate(JS)
        print(f"{key:10s} {r}")
    b.close()
