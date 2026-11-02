from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(r"D:/vibecoding/skibot/.scratch")

JS = """() => {
  const t = document.querySelector('table');
  const panel = document.querySelector('.panel');
  const main = document.querySelector('main');
  const cells = [...document.querySelectorAll('table tr:first-child th')].map(th => ({
    text: th.textContent.trim() || '(action)',
    w: Math.round(th.getBoundingClientRect().width),
  }));
  return {
    docScrollWidth: document.documentElement.scrollWidth,
    bodyScrollWidth: document.body.scrollWidth,
    innerWidth: window.innerWidth,
    mainW: Math.round(main.getBoundingClientRect().width),
    panelW: Math.round(panel.getBoundingClientRect().width),
    panelContentW: Math.round(panel.clientWidth - parseFloat(getComputedStyle(panel).paddingLeft) - parseFloat(getComputedStyle(panel).paddingRight)),
    tableW: Math.round(t.getBoundingClientRect().width),
    tableMinContent: t.scrollWidth,
    tableRight: Math.round(t.getBoundingClientRect().right),
    panelRight: Math.round(panel.getBoundingClientRect().right),
    cells,
  };
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 390, "height": 844})
    for key in ("gopr", "pxl", "long"):
        url = (ROOT / f"_tasks_render_{key}.html").resolve().as_uri()
        page.goto(url)
        print(key, page.evaluate(JS))
    b.close()
