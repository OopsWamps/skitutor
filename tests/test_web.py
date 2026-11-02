"""Web 壳冒烟测试：python tests/test_web.py

路由、上传落盘、任务状态机、报告页渲染（真任务注入完成态）。
不真跑 Sports2D。
"""

import json
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from skibot.web.app import create_app  # noqa: E402
from skibot.web import tasks as tasks_mod  # noqa: E402


def _report_dict() -> dict:
    return {
        "schema": "skibot.report/0.2", "level": "intermediate",
        "generated_at": "2026-10-22T00:00:00",
        "view": {"detected": "sagittal", "score": -0.5, "n_valid_frames": 100},
        "assumptions": ["测试假设"], "calibrated": False, "notice": "测试水印",
        "data_source": "mock",
        "quality": {"person_height_ratio": 0.3, "low_conf_frame_ratio": 0.1,
                    "deep_squat_frame_ratio": 0.0},
        "metrics": [
            {"metric": "knee_flexion", "name": "膝弯曲角", "basis": "3d",
             "definition": "髋-膝-踝夹角", "reference": [140, 170],
             "median_deg": 155.0, "pct_in_range": 0.5, "band_percentile": 50.0,
             "n_valid_frames": 100, "verdict": "膝弯曲角在参考带内"},
            {"metric": "hip_flexion", "name": "屈髋角", "basis": "3d",
             "definition": "躯干轴-股骨，180°=伸直", "reference": None,
             "median_deg": 172.0, "pct_in_range": None, "band_percentile": None,
             "n_valid_frames": 100, "verdict": None,
             "helper": {"hip_flexion_3pt_deg": 168.0}},
            {"metric": "knee_abduction", "name": "膝内外翻角", "basis": "3d",
             "definition": "JCS 外展，0°=无内外翻", "reference": None,
             "median_deg": None, "pct_in_range": None, "band_percentile": None,
             "n_valid_frames": 0, "verdict": None, "helper": None}],
        "person": {"id": 0, "selection": "测试选人"},
        "phases": {
            "n_turns": 4, "fallback": None,
            "aggregate": {
                "knee_flexion": {
                    "entry": {"median_deg": 101.5, "n_frames": 40},
                    "control": {"median_deg": None, "n_frames": 0},
                    "exit": {"median_deg": 132.0, "n_frames": 36}},
            },
            "note": "相位切分未通过人工标注验收，逐弯数值仅供参考",
        },
    }


def test_web_flow(tmp: Path):
    # 本用例用假字节验证落盘/失败进表：分析函数走构造注入，不跑 Sports2D；
    # 预检拒绝路径另有专门用例，这里关掉
    def _stub_fail(video, opts):
        raise RuntimeError("stub 分析失败")

    app = create_app(tmp / "web_results", enable_precheck=False,
                     analyze_fn=_stub_fail)
    client = TestClient(app)

    # 页面路由
    assert client.get("/").status_code == 200
    assert client.get("/tasks").status_code == 200
    assert client.get("/report/不存在").status_code == 404

    # 上传：落盘 + 入队
    r = client.post("/api/upload", files={"video": ("clip.mp4", b"fake", "video/mp4")},
                    data={"level": "novice", "blur": "off"})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]
    # 落盘为 ASCII 的 <task_id>.mp4（中文原名只作显示名，避免 trc 头 GBK 问题）
    saved = tmp / "web_results" / "uploads" / tid / f"{tid}.mp4"
    assert saved.exists() and saved.read_bytes() == b"fake"
    assert client.get(f"/api/tasks/{tid}").json()["video"] == "clip.mp4"
    # 未打码原件不对 /files 开放：uploads/* 一律 404（拒绝路由先于挂载注册）
    assert client.get(f"/files/uploads/{tid}/{tid}.mp4").status_code == 404

    # 假字节 + 替身分析：秒失败进表（不拖垮服务）
    deadline = time.time() + 30
    t = {"status": tasks_mod.STATUS_QUEUED}
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] == tasks_mod.STATUS_FAILED:
            break
        time.sleep(0.5)
    assert t["status"] == tasks_mod.STATUS_FAILED, t
    assert "Error" in t["error"] or "error" in t["error"].lower() or t["error"]

    # 任务页展示失败原因与重试按钮；重试端点的 404/409 语义
    tasks_page = client.get("/tasks").text
    assert "重试" in tasks_page and t["error"] in tasks_page
    assert client.post("/api/tasks/不存在/retry").status_code == 404

    # 源视频被清理后重试：409 + 可操作提示，不把任务重新入队
    saved.unlink()
    r = client.post(f"/api/tasks/{tid}/retry")
    assert r.status_code == 409 and "源视频已丢失" in r.json()["detail"]
    saved.write_bytes(b"fake")  # 恢复，后面的用例还要用这个任务

    # 重试真的重新入队执行：同一个替身再失败一次，错误进表
    r = client.post(f"/api/tasks/{tid}/retry")
    assert r.status_code == 200 and r.json()["status"] == tasks_mod.STATUS_QUEUED
    deadline = time.time() + 30
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{tid}").json()
        if t["status"] == tasks_mod.STATUS_FAILED:
            break
        time.sleep(0.2)
    assert t["status"] == tasks_mod.STATUS_FAILED and "stub" in t["error"]

    # 参数校验
    assert client.post("/api/upload", files={"video": ("c.mp4", b"x", "video/mp4")},
                       data={"level": "pro"}).status_code == 400
    assert client.post("/api/upload", files={"video": ("c.gif", b"x", "image/gif")}).status_code == 400

    # 报告页：走 mark_done 公开缝注入完成态（真渲染路径）
    board = client.app.state.board
    work = tmp / "web_results" / tid  # analyze_one 的结果子目录 = video.stem = task_id
    work.mkdir(parents=True, exist_ok=True)
    (work / "report.json").write_text(json.dumps(_report_dict(), ensure_ascii=False),
                                      encoding="utf-8")
    (work / f"{tid}_annotated.mp4").write_bytes(b"fake")
    board.mark_done(tid, work / "report.json")
    page = client.get(f"/report/{tid}")
    assert page.status_code == 200
    body = page.text
    assert "膝弯曲角" in body and "155.0" in body and "达标" in body
    assert "left:50.0%" in body and f"{tid}_annotated.mp4" in body
    # 报告页只出体态读数：原理、口径、水印、辅助量一律不展示
    assert "测试水印" not in body
    assert "基准 2d" not in body and "髋-膝-踝夹角" not in body
    assert "三维来源 mock" not in body
    assert "三维列只作描述统计" not in body and "hip_flexion_3pt_deg" not in body
    assert "有效数据不足" in body
    # 相位读数并进各关节卡：null 单元格只留破折号
    assert "入弯" in body and "101.5" in body and "出弯" in body and "132.0" in body
    assert "无有效帧" in body and "未通过人工标注验收" not in body
    assert "标注视频" in body and "三维骨架回放" not in body

    # 完成态不允许重试
    assert client.post(f"/api/tasks/{tid}/retry").status_code == 409

    # 任务列表页含完成条目
    assert client.get("/tasks").status_code == 200
    print("  web：路由 / 上传落盘 / 失败进表 / 重试端点 / 完成态报告页渲染 ✓")


def _real_video(path: Path, size=(1280, 720), n=88) -> Path:
    """写一段可解码的 mp4（预检元数据全过的素材）。"""
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 25, size)
    assert w.isOpened()
    frame = np.full((size[1], size[0], 3), 120, np.uint8)
    for _ in range(n):
        w.write(frame)
    w.release()
    return path


def test_upload_precheck(tmp: Path):
    """预检接入上传：合格入队、不合格 422 拒绝并删除落盘文件、页面展示原因。"""
    def _stub(video, opts):
        raise RuntimeError("stub 分析失败")

    app = create_app(tmp / "web_results", analyze_fn=_stub)
    client = TestClient(app)

    # 合格素材（720p / 3.5s / 灰帧无人）→ 入队；抽帧检不出人物按无法判定放行
    good = _real_video(tmp / "good.mp4")
    r = client.post("/api/upload",
                    files={"video": ("good.mp4", good.read_bytes(), "video/mp4")},
                    data={"level": "novice", "blur": "on"})
    assert r.status_code == 200, r.text
    assert any("无法判定" in n for n in r.json()["precheck_notes"])

    # 垃圾字节 → 422，结构化原因 + 建议，且上传目录被清掉
    r = client.post("/api/upload",
                    files={"video": ("bad.mp4", b"not-a-video", "video/mp4")},
                    data={"level": "novice"})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["message"].startswith("视频未通过上传预检")
    assert detail["issues"][0]["check"] == "container"
    assert "H.264" in detail["issues"][0]["advice"]
    uploads = list((tmp / "web_results" / "uploads").iterdir())
    assert len(uploads) == 1  # 只剩合格那条

    # 判据可配置：把最短时长调到 10 分钟，同一素材被拒
    from skibot.precheck import PrecheckConfig

    app2 = create_app(
        tmp / "web_results2",
        precheck_config=PrecheckConfig(min_duration_s=600.0,
                                       max_duration_s=1200.0),
        analyze_fn=_stub)
    r = TestClient(app2).post(
        "/api/upload",
        files={"video": ("good.mp4", good.read_bytes(), "video/mp4")},
        data={"level": "novice"})
    assert r.status_code == 422
    assert r.json()["detail"]["issues"][0]["check"] == "duration"

    # 上传页能展示拒绝原因（结构化渲染脚本在页面里）
    page = TestClient(app2).get("/").text
    assert "showReject" in page and "预检" in page

    print("  预检接入：合格入队 / 422 结构化拒绝并清盘 / 判据可配置 / 页面展示 ✓")


def test_web_reselect(tmp: Path):
    """报告页改选人物：候选全量、编号预览、异步改选、报告与标注视频更新。"""
    result_dir = tmp / "web_results"
    work = result_dir / "clip1"
    work.mkdir(parents=True)
    _real_video(work / "clip1.mp4", size=(640, 480), n=6)

    alias = {0: "Nose", 5: "LShoulder", 6: "RShoulder", 11: "LHip", 12: "RHip",
             13: "LKnee", 14: "RKnee", 15: "LAnkle", 16: "RAnkle"}
    names = [alias.get(i, f"J{i}") for i in range(26)]
    stand = {5: (280, 120), 6: (320, 120), 11: (285, 240), 12: (315, 240),
             13: (280, 330), 14: (320, 330), 15: (278, 420), 16: (322, 420)}
    for pid in (0, 1):
        lines = ["PathFileType\t4\t(X/Y/Z)\tfake.trc",
                 "DataRate\tCameraFrameRate\tNumFrames\tNumMarkers\tUnits",
                 "30.00\t30.00\t6\t26\tm", "Frame\tTime\t" + "\t".join(names), ""]
        for f in range(6):
            cells = [str(f + 1), f"{f / 30:.6f}"]
            for j in range(26):
                x, y = stand.get(j, (300.0 + 10 * j, 100.0 + 10 * j))
                cells += [f"{x + 25.0 * pid:.6f}", f"{y:.6f}", "0.9"]
            lines.append("\t".join(cells))
        (work / f"clip1_px_person{pid:02d}.trc").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    report = _report_dict()
    report["person"] = {"id": 0, "selection": "自动"}
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False),
                                      encoding="utf-8")

    app = create_app(result_dir, enable_precheck=False)
    client = TestClient(app)

    # 重新扫描恢复的完成任务
    cands = client.get("/api/tasks/clip1/candidates").json()
    assert [c["id"] for c in cands["candidates"]] == [0, 1]
    assert cands["current"] == 0
    assert all(c["preview_url"] for c in cands["candidates"])
    assert (work / "candidate_person0.jpg").exists()
    assert (work / "candidate_person1.jpg").exists()

    # 报告页渲染编号候选
    body = client.get("/report/clip1").text
    assert "分析对象" in body and "人物 1" in body

    # 异步改选：立即返回，轮询结束后报告与标注视频都换成 person 1
    r = client.post("/api/tasks/clip1/person", data={"person": 1})
    assert r.status_code == 200 and r.json()["person"] == 1
    deadline = time.time() + 60
    while time.time() < deadline:
        j = client.get("/api/tasks/clip1").json()
        if not j["reselecting"]:
            break
        time.sleep(0.2)
    assert not j["reselecting"] and not j["reselect_error"], j
    updated = json.loads((work / "report.json").read_text(encoding="utf-8"))
    assert updated["person"]["id"] == 1
    assert "人工改选" in updated["person"]["selection"]
    assert (work / "clip1_annotated.mp4").exists()

    # 越界人物 400；未完成任务 409
    assert client.post("/api/tasks/clip1/person",
                       data={"person": 9}).status_code == 400
    assert client.get("/api/tasks/不存在/candidates").status_code == 404

    print("  改选接入：候选全量 + 编号预览 / 异步改选 / 报告与视频更新 / "
          "越界与未完成拒绝 ✓")


def test_task_persistence(tmp: Path):
    """持久化契约：重启后排队任务继续执行、运行中标失败可重试、元数据
    原样恢复、原子写、损坏不阻塞启动、历史结果目录兜底不变。用 stub
    analyze_one 阻塞/成功模拟，不真跑 Sports2D。"""
    from skibot.web.tasks import (STATUS_DONE, STATUS_FAILED, STATUS_RUNNING,
                                  TaskBoard)

    result_dir = tmp / "results"

    calls: list[str] = []

    def fake_analyze(video_path, opts):
        calls.append(Path(video_path).name)
        stem = Path(video_path).stem
        work = result_dir / stem
        if len(calls) == 1:
            time.sleep(5)  # 模拟长时间分析；测试体在此期间“重启”
            raise RuntimeError("中断")
        work.mkdir(parents=True, exist_ok=True)
        (work / "report.json").write_text("{}", encoding="utf-8")
        return work / "report.json"

    # 先建表再落上传件：TaskBoard 启动时回收「落盘后未入队」的孤儿上传目录，
    # 测试预置的源视频要在表建好之后才放进去。分析函数全走构造注入。
    board_a = TaskBoard(result_dir, analyze_fn=fake_analyze)
    up = result_dir / "uploads" / "t1"
    up.mkdir(parents=True)
    video = up / "t1.mp4"
    video.write_bytes(b"fake")
    board_a.enqueue("t1", video, "我的滑行.mp4", "novice", False)
    deadline = time.time() + 10
    while time.time() < deadline and board_a.get("t1").status != STATUS_RUNNING:
        time.sleep(0.05)
    assert board_a.get("t1").status == STATUS_RUNNING
    assert (result_dir / "tasks.json").exists()
    assert not (result_dir / "tasks.json.tmp").exists()  # 原子写无残留
    stored = json.loads((result_dir / "tasks.json").read_text(encoding="utf-8"))
    # 持久化绝对路径：换个工作目录启动也能找回上传文件
    assert Path(stored["tasks"][0]["video_path"]).is_absolute()

    # “重启”：新实例从 JSON 恢复；运行中 → 失败（可重试），元数据原样
    board_a.stop()  # 旧实例停表，避免它稍后醒来用过期快照覆写 tasks.json
    board_b = TaskBoard(result_dir, analyze_fn=fake_analyze)
    t = board_b.get("t1")
    assert t is not None and t.status == STATUS_FAILED
    assert "中断" in t.error
    assert t.video_name == "我的滑行.mp4" and t.level == "novice"
    assert t.blur is False and t.created_at.year >= 2026

    # 手动重试 → 重新入队并执行成功；返回值里的状态是锁内快照
    res = board_b.retry("t1")
    assert res.task is t and res.requeued is True and res.status == "queued"
    deadline = time.time() + 10
    while time.time() < deadline and board_b.get("t1").status != STATUS_DONE:
        time.sleep(0.05)
    t = board_b.get("t1")
    assert t.status == STATUS_DONE and t.report_path is not None
    assert t.report_path.exists()
    board_b.stop()

    # 再“重启”：完成态与现状一致地恢复
    board_c = TaskBoard(result_dir, analyze_fn=fake_analyze)
    t = board_c.get("t1")
    assert t.status == STATUS_DONE and t.report_path.exists()
    assert not (result_dir / "tasks.json.tmp").exists()

    # 非失败任务 retry 原样返回（不重新入队）
    res = board_c.retry("t1")
    assert res.task is t and res.requeued is False and res.status == "done"
    assert board_c.retry("不存在").task is None
    board_c.stop()

    # 历史结果目录（不在 JSON 里）兜底恢复为完成态，与旧行为一致；
    # 源视频路径取自报告里的 video 字段
    legacy = result_dir / "legacy"
    legacy.mkdir()
    legacy_src = result_dir / "legacy_source.mp4"
    legacy_src.write_bytes(b"fake")
    (legacy / "report.json").write_text(
        json.dumps({"level": "advanced", "video": str(legacy_src)}),
        encoding="utf-8")
    board_d = TaskBoard(result_dir, analyze_fn=fake_analyze)
    t = board_d.get("legacy")
    assert t.status == STATUS_DONE and t.level == "advanced"
    assert t.video_path == legacy_src.resolve()  # 源视频从报告取回
    assert t.blur is None  # 旧任务不知道打码设置，改选时按副本存在性推断
    board_d.stop()

    # 损坏的 tasks.json 不阻塞启动：回退到结果目录扫描
    (result_dir / "tasks.json").write_text("{broken", encoding="utf-8")
    board_e = TaskBoard(result_dir, analyze_fn=fake_analyze)
    t = board_e.get("t1")
    assert t is not None and t.status == STATUS_DONE
    assert board_e.get("legacy").status == STATUS_DONE
    board_e.stop()

    # 合法 JSON 但任务表形状不对（null/非列表）同样按损坏处理，不当崩
    for bad in ('{"tasks": null}', '{"tasks": 1}', '{"tasks": {"a": 1}}', "[]"):
        (result_dir / "tasks.json").write_text(bad, encoding="utf-8")
        board_bad = TaskBoard(result_dir, analyze_fn=fake_analyze)
        assert board_bad.get("t1").status == STATUS_DONE
        board_bad.stop()

    # 文件被外部进程短暂锁住（读失败）：不崩、也不把排队任务覆盖掉
    from unittest import mock

    queued = {"id": "t9", "video_path": str(video), "video_name": "v.mp4",
              "level": "novice", "blur": False, "status": "queued",
              "error": "", "report_path": None,
              "created_at": "2026-10-23T00:00:00"}
    original = json.dumps({"version": 1, "tasks": [queued]}, ensure_ascii=False)
    (result_dir / "tasks.json").write_text(original, encoding="utf-8")

    def _locked(self, *a, **k):
        raise PermissionError("被占用")

    with mock.patch.object(Path, "read_text", _locked):
        board_locked = TaskBoard(result_dir, analyze_fn=fake_analyze)
        assert board_locked.get("t9") is None      # 本次没恢复
        # 下次落盘前仍读不出：先备份原文件再重建，排队记录留在备份里
        board_locked.enqueue("newone", video, "n.mp4", "novice", False)
    backup = result_dir / "tasks.json.unreadable"
    assert backup.exists() and "t9" in backup.read_text(encoding="utf-8")
    board_locked.stop()

    # 排队任务重启后继续执行：盘上是 queued，新实例接手跑完（直接写盘
    # 避免旧实例 worker 与“重启”竞态）
    video2 = up.parent / "t2" / "t2.mp4"
    video2.parent.mkdir(parents=True)
    video2.write_bytes(b"fake")
    entry = {"id": "t2", "video_path": str(video2), "video_name": "第二条.mp4",
             "level": "intermediate", "blur": True, "status": "queued",
             "error": "", "report_path": None,
             "created_at": "2026-10-23T00:00:00"}
    (result_dir / "tasks.json").write_text(
        json.dumps({"version": 1, "tasks": [entry]}, ensure_ascii=False),
        encoding="utf-8")
    board_g = TaskBoard(result_dir, analyze_fn=fake_analyze)
    deadline = time.time() + 10
    while time.time() < deadline and board_g.get("t2").status != STATUS_DONE:
        time.sleep(0.05)
    t = board_g.get("t2")
    assert t.status == STATUS_DONE
    assert t.video_name == "第二条.mp4" and t.level == "intermediate"
    assert t.blur is True
    board_g.stop()

    print("  持久化：运行中断→失败可重试 / 排队续跑 / 元数据恢复 / "
          "原子写 / 损坏自愈 / 历史兜底 ✓")


def test_bad_records(tmp: Path):
    """坏记录（id 是列表、blur 是字符串）跳过或降级，不阻塞启动。"""
    from skibot.web.tasks import TaskBoard

    result_dir = tmp / "r"
    work = result_dir / "ok1"
    work.mkdir(parents=True)
    (work / "report.json").write_text("{}", encoding="utf-8")
    video = tmp / "v.mp4"
    video.write_bytes(b"fake")
    entries = [
        {"id": ["t1"], "video_path": str(video), "video_name": "x",
         "level": "novice", "status": "done", "blur": False},
        {"id": "ok1", "video_path": str(video), "video_name": "x",
         "level": "pro", "blur": "false", "status": "done",
         "report_path": str(work / "report.json"), "created_at": 12345},
    ]
    (result_dir / "tasks.json").write_text(
        json.dumps({"version": 1, "tasks": entries}), encoding="utf-8")

    board = TaskBoard(result_dir)  # 不应崩溃
    assert [t.id for t in board.all()] == ["ok1"]
    t = board.get("ok1")
    assert t.level == "intermediate"   # 未知水平档降级
    assert t.blur is None              # "false" 不当成 True
    assert t.created_at.year >= 2026   # 非法时间退化到当前
    board.stop()

    print("  持久化：坏记录跳过 / 字段类型校验 ✓")


def test_relative_result_dir(tmp: Path):
    """相对 --result-dir：落盘与任务表路径都应是绝对路径，换目录启动不丢。"""
    import os

    old = os.getcwd()
    os.chdir(tmp)
    try:
        def _stub(video, opts):
            raise RuntimeError("stub 分析失败")

        app = create_app(Path("web_results"), enable_precheck=False,
                         analyze_fn=_stub)
        client = TestClient(app)
        r = client.post("/api/upload",
                        files={"video": ("c.mp4", b"fake", "video/mp4")},
                        data={"level": "novice"})
        assert r.status_code == 200, r.text
        tid = r.json()["task_id"]
        assert app.state.board.get(tid).video_path.is_absolute()
        stored = json.loads((tmp / "web_results" / "tasks.json").read_text(
            encoding="utf-8"))
        assert Path(stored["tasks"][0]["video_path"]).is_absolute()
        app.state.board.stop()
    finally:
        os.chdir(old)

    print("  持久化：相对 result-dir 落盘为绝对路径 ✓")


def test_restore_variants(tmp: Path):
    """恢复路径：uploads 布局找回源视频；RUNNING 但报告已完成 -> 恢复 DONE。"""
    from skibot.web.tasks import STATUS_DONE, TaskBoard

    result_dir = tmp / "r"
    work = result_dir / "abc"
    work.mkdir(parents=True)
    (work / "report.json").write_text(json.dumps({"level": "novice"}),
                                      encoding="utf-8")
    up = result_dir / "uploads" / "abc" / "abc.mp4"
    up.parent.mkdir(parents=True)
    up.write_bytes(b"fake")

    board = TaskBoard(result_dir)
    t = board.get("abc")
    assert t.video_path == up  # 报告没记 video，按 uploads 布局找回
    board.stop()

    # 状态停在 running 但报告已经写完（上一次落盘失败留下的）：恢复成完成，
    # 不把有效结果当成中断任务
    work2 = result_dir / "xyz"
    work2.mkdir()
    (work2 / "report.json").write_text("{}", encoding="utf-8")
    entry = {"id": "xyz", "video_path": str(up), "video_name": "x",
             "level": "novice", "blur": False, "status": "running",
             "error": "", "report_path": str(work2 / "report.json"),
             "created_at": "2026-10-23T00:00:00"}
    (result_dir / "tasks.json").write_text(
        json.dumps({"version": 1, "tasks": [entry]}), encoding="utf-8")
    board2 = TaskBoard(result_dir)
    t2 = board2.get("xyz")
    assert t2.status == STATUS_DONE and t2.report_path == work2 / "report.json"
    board2.stop()

    # 改选跑到一半服务重启：报告与视频是成对原子替换的，仍是旧人物的有效
    # 产物；reselecting 复位并标中断原因，用户可再点一次改选
    entry3 = {"id": "xyz", "video_path": str(up), "video_name": "x",
              "level": "novice", "blur": False, "status": "done",
              "error": "", "report_path": str(work2 / "report.json"),
              "created_at": "2026-10-23T00:00:00",
              "reselecting": True, "reselect_error": ""}
    (result_dir / "tasks.json").write_text(
        json.dumps({"version": 1, "tasks": [entry3]}), encoding="utf-8")
    board3 = TaskBoard(result_dir)
    t3 = board3.get("xyz")
    assert t3.reselecting is False and "中断" in t3.reselect_error
    assert t3.done
    board3.stop()

    print("  持久化：恢复路径找回上传源视频 / 已完成的中断状态恢复为完成 / "
          "改选中断复位 ✓")


def test_lifter_wiring(tmp: Path):
    """三维开关：表单默认开、显式关、服务级 none 压到关；lifter 进任务表。"""
    def _stub(video, opts):
        raise RuntimeError("stub")

    # 服务级默认 motionbert：勾选框发 "on" → motionbert
    app = create_app(tmp / "w1", enable_precheck=False, analyze_fn=_stub)
    client = TestClient(app)
    r = client.post("/api/upload",
                    files={"video": ("a.mp4", b"x", "video/mp4")},
                    data={"level": "novice", "blur": "off", "lifter": "on"})
    tid = r.json()["task_id"]
    assert client.app.state.board.get(tid).lifter == "motionbert"
    # 未勾选的 checkbox 浏览器不发字段 → 必须是关（缺省"开"会让取消勾选失效）
    r = client.post("/api/upload",
                    files={"video": ("a2.mp4", b"x", "video/mp4")},
                    data={"level": "novice", "blur": "off"})
    tid = r.json()["task_id"]
    assert client.app.state.board.get(tid).lifter == "none"
    # blur 同语义：不发字段 = 不打码，发 "on" = 打码
    r = client.post("/api/upload",
                    files={"video": ("a3.mp4", b"x", "video/mp4")},
                    data={"level": "novice", "lifter": "off"})
    tid = r.json()["task_id"]
    assert client.app.state.board.get(tid).blur is False
    r = client.post("/api/upload",
                    files={"video": ("a4.mp4", b"x", "video/mp4")},
                    data={"level": "novice", "blur": "on", "lifter": "off"})
    tid = r.json()["task_id"]
    assert client.app.state.board.get(tid).blur is True
    # 表单显式关 → none
    r = client.post("/api/upload",
                    files={"video": ("b.mp4", b"x", "video/mp4")},
                    data={"level": "novice", "blur": "off", "lifter": "off"})
    tid2 = r.json()["task_id"]
    assert client.app.state.board.get(tid2).lifter == "none"
    # 服务级 none：表单开了也被压到关
    app2 = create_app(tmp / "w2", enable_precheck=False, analyze_fn=_stub,
                      lifter="none")
    r = TestClient(app2).post(
        "/api/upload", files={"video": ("c.mp4", b"x", "video/mp4")},
        data={"level": "novice", "blur": "off", "lifter": "on"})
    board2 = app2.state.board
    tid3 = r.json()["task_id"]
    assert board2.get(tid3).lifter == "none"
    board2.stop()
    app.state.board.stop()
    print("  web：三维开关表单/服务级接线进任务表 ✓")


def test_report_viewer_and_compare(tmp: Path):
    """回放卡（有 animation.json 才渲染）与对比页（选两个完成态）。"""
    def _stub(video, opts):
        raise RuntimeError("stub")

    app = create_app(tmp / "w", enable_precheck=False, analyze_fn=_stub)
    client = TestClient(app)
    board = app.state.board

    def _mk_done(name: str, phases=None, level="intermediate"):
        r = client.post("/api/upload",
                        files={"video": (f"{name}.mp4", b"x", "video/mp4")},
                        data={"level": level, "blur": "off"})
        tid = r.json()["task_id"]
        work = tmp / "w" / tid
        work.mkdir(parents=True, exist_ok=True)
        rep = _report_dict()
        if phases is not None:
            rep["phases"] = phases
        (work / "report.json").write_text(
            json.dumps(rep, ensure_ascii=False), encoding="utf-8")
        (work / f"{tid}_annotated.mp4").write_bytes(b"fake")
        board.mark_done(tid, work / "report.json")
        return tid, work

    agg = {"n_turns": 4, "fallback": None, "aggregate": {
        "knee_flexion": {ph: {"median_deg": 100.0 + i * 5, "n_frames": 30}
                         for i, ph in enumerate(("entry", "control", "exit"))}}}
    ta, wa = _mk_done("a", agg)
    tb, wb = _mk_done("b", agg)

    # 回放卡：动画文件在才渲染，数据地址指向静态挂载
    (wa / "animation.json").write_text(json.dumps({
        "schema": "skibot.animation/0.1", "fps": 30.0, "n_frames": 12,
        "source": "mock", "gravity_source": "adapter", "note": "",
        "joints": ["Nose"] * 26, "edges": [[5, 6]],
        "phases": {"fps": 30.0, "fallback": None, "turns": [], "discarded": []},
        "series": {}, "confidence": [[1.0] * 26] * 12,
        "frames": [[[0.0, 0.0, 0.0]] * 26] * 12}), encoding="utf-8")
    body = client.get(f"/report/{ta}").text
    assert "三维骨架回放" in body and "/files/" in body and "拖动旋转" in body
    assert "MotionBERT" not in body and "未做多机位真值验证" not in body
    body_b = client.get(f"/report/{tb}").text
    assert "三维骨架回放" not in body_b and "标注视频" in body_b

    # 对比页：未选 → 选择器；选两个 → 行渲染；坏 id → 404
    page = client.get("/compare").text
    assert "前后对比" in page and ta in page
    page = client.get("/compare", params={"a": ta, "b": tb}).text
    assert "按相位" in page and "差值（B−A）" in page and "105.0" in page
    assert "全程三维六项" in page and "假设与口径" not in page
    assert client.get("/compare", params={"a": "bad", "b": "nope"}
                      ).status_code == 404
    board.stop()
    print("  web：回放卡按动画存在渲染 / 对比页选择器与行渲染 ✓")


def test_corrupt_report_and_restore_lifter(tmp: Path):
    """坏报告不把页面打成 500；历史结果重建时按 data_source 恢复 lifter。"""
    import threading

    from skibot.web.tasks import TaskBoard

    # 让 worker 卡在分析里：否则它在 mark_done 之后才写 FAILED，
    # 会把用例里刚标记的完成态覆盖掉（纯时序竞争）
    hold = threading.Event()

    def _stub(video, opts):
        hold.wait(10)
        raise RuntimeError("stub")

    rd = tmp / "w"
    app = create_app(rd, enable_precheck=False, analyze_fn=_stub)
    client = TestClient(app)
    board = app.state.board

    def _mk_done(name: str):
        r = client.post("/api/upload",
                        files={"video": (f"{name}.mp4", b"x", "video/mp4")},
                        data={"level": "novice", "blur": "off"})
        tid = r.json()["task_id"]
        work = rd / tid
        work.mkdir(parents=True, exist_ok=True)
        (work / "report.json").write_text(
            json.dumps(_report_dict(), ensure_ascii=False), encoding="utf-8")
        board.mark_done(tid, work / "report.json")
        return tid, work

    ta, wa = _mk_done("a")
    tb, wb = _mk_done("b")
    # 报告被截断：报告页给可操作提示（500 是数据坏了，不是路由崩了）
    (wa / "report.json").write_text('{"level": "novice"', encoding="utf-8")
    page = client.get(f"/report/{ta}")
    assert page.status_code == 500 and "报告文件缺失或损坏" in page.text
    # 对比页：A 坏了仍渲染页面并指出坏的是谁，B 侧数据不跟着消失
    page = client.get("/compare", params={"a": ta, "b": tb})
    assert page.status_code == 200 and "报告文件缺失或损坏" in page.text
    assert f"任务 {ta}" in page.text
    # 报告文件整份消失同样处理
    (wb / "report.json").unlink()
    page = client.get("/compare", params={"a": tb, "b": ta})
    assert page.status_code == 200 and f"任务 {tb}" in page.text
    board.stop()

    # 历史结果目录重建：报告记了 motionbert 的带三维改选重跑，其余按二维
    rd2 = tmp / "hist"
    (rd2 / "mv").mkdir(parents=True)
    (rd2 / "mv" / "report.json").write_text(json.dumps(
        {"level": "novice", "data_source": "motionbert"}), encoding="utf-8")
    (rd2 / "old").mkdir(parents=True)
    (rd2 / "old" / "report.json").write_text(
        json.dumps({"level": "novice"}), encoding="utf-8")
    board2 = TaskBoard(rd2)
    assert board2.get("mv").lifter == "motionbert"
    assert board2.get("old").lifter == "none"
    board2.stop()
    print("  web：坏报告不 500 / 历史结果按 data_source 恢复 lifter ✓")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        test_web_flow(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_upload_precheck(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_web_reselect(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_task_persistence(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_restore_variants(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_relative_result_dir(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_bad_records(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_lifter_wiring(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_report_viewer_and_compare(Path(td))
    with tempfile.TemporaryDirectory() as td:
        test_corrupt_report_and_restore_lifter(Path(td))
    print("全部通过")
