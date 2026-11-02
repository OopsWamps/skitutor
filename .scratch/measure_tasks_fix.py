from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(r"D:/vibecoding/skibot/.scratch")

WRAP_JS = """() => {
  const t = document.querySelector('table');
  const d = document.createElement('div');
  d.style.overflowX = 'auto';
  t.parentNode.insertBefore(d, t);
  d.appendChild(t);
  return {
    docScrollWidth: document.documentElement.scrollWidth,
    tableW: Math.round(t.getBoundingClientRect().width),
    wrapperW: Math.round(d.getBoundingClientRect().width),
    wrapperScrollW: d.scrollWidth,
    wrapperCanScroll: d.scrollWidth > d.clientWidth,
  };
}"""

WRAP_CSS_JS = """() => {
  const s = document.createElement('style');
  s.textContent = 'td,th{overflow-wrap:anywhere}';
  document.head.appendChild(s);
  return {
    docScrollWidth: document.documentElement.scrollWidth,
    tableW: Math.round(document.querySelector('table').getBoundingClientRect().width),
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    for key in ("gopr", "pxl", "long"):
        for label, js in (("wrap-div", WRAP_JS), ("overflow-wrap", WRAP_CSS_JS)):
            page = b.new_page(viewport={"width": 390, "height": 844})
            page.goto((ROOT / f"_tasks_render_{key}.html").resolve().as_uri())
            print(key, label, page.evaluate(js))
            page.close()
    b.close()
