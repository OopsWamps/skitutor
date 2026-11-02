"""Verify claim: compare.html h2 filename overflows panel at 390px; fix = overflow-wrap:anywhere."""
import sys
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "skibot" / "web" / "templates"

env = Environment(loader=FileSystemLoader(str(TEMPLATES)),
                  autoescape=select_autoescape(["html"]))

NAME = "VID_20260214_143025_GOPR0068_front_run1.mp4"   # 47 chars, no break opportunity
print("filename len:", len(NAME))

result = {
    "phase_rows": [{"metric_label": "屈髋角（三维）", "phase_label": "入弯",
                    "status": "no_data", "reason": "无相位数据",
                    "a": None, "b": None, "delta": None, "direction": None}],
    "overall_rows": [],
    "notes": [], "n_comparable": 0, "a_turns": 5, "b_turns": 4,
}
a_task = SimpleNamespace(video_name=NAME, level="novice")
b_task = SimpleNamespace(video_name="short.mp4", level="advanced")
tasks = [{"id": "t1", "label": f"{NAME}（novice，2025-02-14）"},
         {"id": "t2", "label": "short.mp4（advanced，2025-02-14）"}]

html = env.get_template("compare.html").render(
    active="compare", tasks=tasks, a="t1", b="t2",
    result=result, error="", a_task=a_task, b_task=b_task,
)
out = ROOT / ".scratch" / "_h2_overflow.html"
out.write_text(html, encoding="utf-8")

# the claim's scenario assumes the selects are already constrained (separate fix)
FIX_SELECT = ".field{min-width:0} select{max-width:100%}"
FIX_H2 = "h2{overflow-wrap:anywhere}"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(out.as_uri())
    page.wait_for_timeout(200)

    def measure(label):
        data = page.evaluate("""() => {
          const h2s = [...document.querySelectorAll('.panel--solid h2')];
          const h2 = h2s.find(h => h.textContent.includes('A：'));
          const panel = h2.closest('.panel');
          return {
            docScrollWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth,
            h2ScrollWidth: h2.scrollWidth,
            h2ClientWidth: h2.clientWidth,
            panelInnerWidth: panel.clientWidth - 40,
          };
        }""")
        print(label, data)
        return data

    base = measure("baseline           ")
    page.add_style_tag(content=FIX_SELECT)
    selonly = measure("select-fix only    ")
    page.add_style_tag(content=FIX_H2)
    both = measure("select-fix + h2 fix")
    browser.close()

# claim scenario: even with selects constrained, h2 still bleeds out of its box
ok_repro = (selonly["h2ScrollWidth"] > selonly["h2ClientWidth"]
            and selonly["docScrollWidth"] > 390)
ok_fix = (both["h2ScrollWidth"] <= both["h2ClientWidth"]
          and both["docScrollWidth"] <= 390)
print("REPRODUCED (h2 bleeds with selects fixed):", ok_repro,
      "| h2 fix resolves page overflow:", ok_fix)
