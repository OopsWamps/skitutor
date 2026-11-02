"""Measure /compare form widths at 390px in real Chromium."""
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "skibot" / "web" / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                  autoescape=select_autoescape(["html"]))

tasks = [
    {"id": "t1", "label": "VID_20250214_143025.mp4（novice，2025-02-14）"},
    {"id": "t2", "label": "VID_20250214_143026.mp4（intermediate，2025-02-14）"},
    {"id": "t3", "label": "VID_20250214_143027.mp4（advanced，2025-02-14）"},
]
html = env.get_template("compare.html").render(
    active="compare", tasks=tasks, a="", b="",
    result=None, error="", a_task=None, b_task=None,
)
out = ROOT / ".scratch" / "_cmp_width.html"
out.write_text(html, encoding="utf-8")

FIX_FIELD = ".field{min-width:0}"
FIX_SELECT = "select{max-width:100%}"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(out.as_uri())
    page.wait_for_timeout(200)

    def measure(label):
        data = page.evaluate("""() => {
          const sels = [...document.querySelectorAll('form select')];
          const field = document.querySelector('form label.field');
          return {
            scrollWidth: document.documentElement.scrollWidth,
            bodyScrollWidth: document.body.scrollWidth,
            innerWidth: window.innerWidth,
            selectWidths: sels.map(s => s.getBoundingClientRect().width),
            fieldWidth: field.getBoundingClientRect().width,
            formWidth: document.querySelector('form').getBoundingClientRect().width,
          };
        }""")
        print(label, data)
        return data

    measure("baseline       ")
    page.add_style_tag(content=FIX_FIELD)
    measure("field min-width")
    page.reload()
    page.wait_for_timeout(100)
    page.add_style_tag(content=FIX_SELECT)
    measure("select max-w   ")
    page.reload()
    page.wait_for_timeout(100)
    page.add_style_tag(content=FIX_FIELD + FIX_SELECT)
    measure("both           ")
    browser.close()
