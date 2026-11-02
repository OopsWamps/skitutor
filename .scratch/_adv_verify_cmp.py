"""Adversarial re-verification of the /compare overflow claim, at 390px."""
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "skibot" / "web" / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                  autoescape=select_autoescape(["html"]))

# Realistic camera/phone dump filenames, same shape as the route builds:
# label = f"{video_name}（{level}，{created_at:%Y-%m-%d}）"  (app.py:214)
LONG = "GX020423_26112024_141612_C0016_slowmo.mp4"
tasks = [
    {"id": "t1", "label": f"{LONG}（novice，2025-02-14）"},
    {"id": "t2", "label": f"{LONG}（intermediate，2025-02-14）"},
]

a_task = {"video_name": LONG, "level": "intermediate"}
b_task = {"video_name": "IMG_2611_1416.MP4", "level": "novice"}
row = {"metric_label": "测试", "phase_label": "入弯", "status": "empty", "reason": "x"}
result = {"a_turns": 3, "b_turns": 4, "phase_rows": [row], "overall_rows": [row]}

html = env.get_template("compare.html").render(
    active="compare", tasks=tasks, a="t1", b="t2",
    result=result, error="", a_task=a_task, b_task=b_task,
)
out = ROOT / ".scratch" / "_adv_cmp.html"
out.write_text(html, encoding="utf-8")

FIX_FORM = ("form .field{min-width:0;flex:1 1 220px}"
            "form select{width:100%;max-width:100%}")
FIX_H2 = ".panel h2{overflow-wrap:anywhere}"

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(out.as_uri())
    page.wait_for_timeout(150)

    js = """() => ({
      scrollW: document.documentElement.scrollWidth,
      innerW: window.innerWidth,
      selW: [...document.querySelectorAll('form select')].map(s=>Math.round(s.getBoundingClientRect().width)),
      h2A: Math.round(document.querySelectorAll('.panel')[1].querySelectorAll('h2')[0].scrollWidth),
    })"""
    print("baseline      ", page.evaluate(js))
    page.add_style_tag(content=FIX_FORM)
    page.wait_for_timeout(50)
    print("form-fixed    ", page.evaluate(js))
    page.add_style_tag(content=FIX_H2)
    page.wait_for_timeout(50)
    print("form+h2-fixed ", page.evaluate(js))
    browser.close()
