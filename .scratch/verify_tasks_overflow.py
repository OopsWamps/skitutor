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
    "gopr": "GOPR0123.MP4",
    "pxl": "PXL_20250101_123456789.mp4",
    "long": "VID_20250101123456_ABCDEFGHIJKLMNOPQRSTUVWXYZ.mp4",
}

for key, name in names.items():
    now = datetime.datetime(2025, 1, 1, 12, 34, 0)
    tasks = [
        {
            "id": "abc123",
            "video_name": name,
            "level": "novice",
            "created_at": now,
            "status": "done",
            "error": None,
        }
    ]
    html = env.get_template("tasks.html").render(
        tasks=tasks, active="tasks", low_density=False, error=None
    )
    path = OUT / f"_tasks_render_{key}.html"
    path.write_text(html, encoding="utf-8")
    print(path)
