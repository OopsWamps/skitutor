"""区分各场景的具体异常类型。"""
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, r"D:\vibecoding\skibot")

from fastapi.testclient import TestClient  # noqa: E402

from skibot.web.app import create_app  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="skibot_corrupt2_"))

# 场景 A：report.json 解析失败（JSONDecodeError 路径）
work = tmp / "leg1"
work.mkdir()
(work / "report.json").write_text("{corrupt", encoding="utf-8")

# 场景 B：合法 {} 报告的对照（预计挂在别处？查明具体异常）
work2 = tmp / "good"
work2.mkdir()
(work2 / "report.json").write_text("{}", encoding="utf-8")

app = create_app(tmp, enable_precheck=False, analyze_fn=lambda *a, **k: None)
client = TestClient(app, raise_server_exceptions=True)

for tid in ("leg1", "good"):
    try:
        r = client.get(f"/report/{tid}")
        print(f"GET /report/{tid} -> {r.status_code} (no exception)")
    except Exception as e:
        tb = traceback.extract_tb(e.__traceback__)
        site = [f for f in tb if "app.py" in f.filename][-1]
        print(f"GET /report/{tid} -> {type(e).__name__}: {e} "
              f"(app.py:{site.lineno})")

try:
    client.get("/compare", params={"a": "leg1", "b": "leg1"})
except Exception as e:
    tb = traceback.extract_tb(e.__traceback__)
    site = [f for f in tb if "app.py" in f.filename][-1]
    print(f"GET /compare -> {type(e).__name__}: {e} (app.py:{site.lineno})")

# 场景 C：report.json 是合法 JSON 但顶层不是对象（比如被写成列表/字符串）
work3 = tmp / "weird"
work3.mkdir()
(work3 / "report.json").write_text('"just a string"', encoding="utf-8")
try:
    client.get("/report/weird")
except Exception as e:
    print(f"GET /report/weird (JSON scalar) -> {type(e).__name__}: {e}")

# 场景 D：report.json 非法 + compare 只有单侧坏（另一任务正常可解析）
(work / "report.json").write_text("{corrupt", encoding="utf-8")
try:
    client.get("/compare", params={"a": "good", "b": "leg1"})
except Exception as e:
    print(f"GET /compare good+leg1 -> {type(e).__name__}: {e}")
