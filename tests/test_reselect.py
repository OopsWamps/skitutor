"""改选人物核心测试：python tests/test_reselect.py

合成两人 px trc + 小视频，验证候选列表、改选重跑（报告/标注视频原地
覆盖）、预览图与异常路径。不跑 Sports2D。
"""

import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skibot.reselect import list_candidates, render_previews, reselect_one  # noqa: E402

# 站姿骨架的关键点（640x480 内）：Halpe 核心八点用真名，其余占位
ALIAS = {0: "Nose", 5: "LShoulder", 6: "RShoulder", 11: "LHip", 12: "RHip",
         13: "LKnee", 14: "RKnee", 15: "LAnkle", 16: "RAnkle"}
STAND = {5: (280, 120), 6: (320, 120), 11: (285, 240), 12: (315, 240),
         13: (280, 330), 14: (320, 330), 15: (278, 420), 16: (322, 420)}


def write_person_trc(path: Path, n_frames: int = 6, dx: float = 0.0,
                     valid_frames: int | None = None,
                     valid_from: int = 0) -> None:
    """n 帧站立骨架的 px trc；dx 让不同人物的横向位置不同。

    valid_frames 之后的帧写空单元格（trc 里丢点的形态），用于模拟人物
    中途出画。
    """
    names = [ALIAS.get(i, f"J{i}") for i in range(26)]
    lines = ["PathFileType\t4\t(X/Y/Z)\tfake.trc",
             "DataRate\tCameraFrameRate\tNumFrames\tNumMarkers\tUnits",
             f"30.00\t30.00\t{n_frames}\t26\tm",
             "Frame\tTime\t" + "\t".join(names), ""]
    limit = n_frames if valid_frames is None else valid_frames
    for f in range(n_frames):
        cells = [str(f + 1), f"{f / 30:.6f}"]
        for j in range(26):
            if f < valid_from or f >= limit:
                cells += ["", "", ""]
                continue
            x, y = STAND.get(j, (300.0 + 10 * j, 100.0 + 10 * j))
            cells += [f"{x + dx + f:.6f}", f"{y:.6f}", f"{0.9:.6f}"]
        lines.append("\t".join(cells))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def make_video(path: Path, n: int = 6, size=(640, 480)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, size)
    assert w.isOpened()
    for i in range(n):
        w.write(np.full((size[1], size[0], 3), 100 + i, np.uint8))
    w.release()
    return path


def make_work(tmp: Path, n_persons: int = 2) -> tuple[Path, Path]:
    work = tmp / "results" / "t1"
    work.mkdir(parents=True, exist_ok=True)
    for pid in range(n_persons):
        write_person_trc(work / f"t1_px_person{pid:02d}.trc", dx=30.0 * pid)
    return work, make_video(tmp / "uploads" / "t1" / "t1.mp4")


def test_candidates(tmp: Path):
    work, _ = make_work(tmp)
    cands = list_candidates(work)
    assert [c.pid for c in cands] == [0, 1]
    assert all(c.n_valid == 6 for c in cands)

    # 单人素材：只有这一个候选，不出多余的
    work1, _ = make_work(tmp / "single", n_persons=1)
    assert [c.pid for c in list_candidates(work1)] == [0]

    print("  改选：候选来自全部人物轨迹 / 单人不出多余候选 ✓")


def test_reselect(tmp: Path):
    import json

    work, video = make_work(tmp / "reselect")
    out = reselect_one(work, 1, level="novice", fallback_video=video)
    assert out == work / "report.json" and out.exists()
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["person"]["id"] == 1
    assert "人工改选" in report["person"]["selection"]
    anno = work / "t1_annotated.mp4"
    assert anno.exists() and anno.stat().st_size > 0

    # 换回 person 0：原地覆盖，报告与标注视频同步更新
    first = anno.read_bytes()
    reselect_one(work, 0, level="intermediate", fallback_video=video)
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    assert report["person"]["id"] == 0 and report["level"] == "intermediate"
    assert anno.read_bytes() != first  # 标注视频跟着换人了

    # 未落盘的人物：拒绝
    try:
        reselect_one(work, 9, level="novice", fallback_video=video)
        raise AssertionError("person 9 不该存在")
    except ValueError as e:
        assert "只有 [0, 1]" in str(e)

    # 改选沿用首次分析写下的视频标签，不让报告里的来源随改选漂移
    work2, video2 = make_work(tmp / "label", n_persons=1)
    (work2 / "report.json").write_text(json.dumps({"video": "原始标签.mp4"}),
                                       encoding="utf-8")
    reselect_one(work2, 0, level="novice", fallback_video=video2)
    assert json.loads((work2 / "report.json").read_text(
        encoding="utf-8"))["video"] == "原始标签.mp4"

    print("  改选：重跑评估与渲染 / 报告标注视频原地覆盖 / 越界拒绝 ✓")


def test_previews(tmp: Path):
    work, video = make_work(tmp / "previews")
    outs = render_previews(work, video)
    assert [p.name for p in outs] == ["candidate_person0.jpg", "candidate_person1.jpg"]
    assert all(p.exists() and p.stat().st_size > 0 for p in outs)

    # 视频打不开 → 空表，不阻塞改选
    assert render_previews(work, tmp / "nope.mp4") == []

    # 人数变少：已不存在候选的旧预览被清掉
    ghost = work / "candidate_person5.jpg"
    ghost.write_bytes(b"old")
    render_previews(work, video)
    assert not ghost.exists()

    print("  改选：编号骨架预览 / 视频缺失返回空 / 幽灵预览清理 ✓")


def test_render_failure_keeps_report(tmp: Path):
    """渲染失败时报告保持旧人物，不出现报告与视频错配。"""
    import skibot.finalize as F

    work, video = make_work(tmp / "renderfail")
    reselect_one(work, 0, level="novice", fallback_video=video)
    before = (work / "report.json").read_text(encoding="utf-8")
    before_video = (work / "t1_annotated.mp4").read_bytes()

    def boom(*a, **k):
        raise RuntimeError("渲染挂了")

    orig = F.render_annotated
    F.render_annotated = boom
    try:
        try:
            reselect_one(work, 1, level="novice", fallback_video=video)
            raise AssertionError("渲染失败应抛出")
        except RuntimeError as e:
            assert "渲染挂了" in str(e)
    finally:
        F.render_annotated = orig

    assert (work / "report.json").read_text(encoding="utf-8") == before
    assert (work / "t1_annotated.mp4").read_bytes() == before_video
    assert not list(work.glob("*.tmp.mp4"))  # 临时文件已清理

    print("  改选：渲染失败不更新报告与视频 ✓")


def test_report_replace_failure_rolls_back(tmp: Path):
    """视频已替换但报告替换失败：回滚视频，两边仍是旧人物。"""
    import skibot.artifacts as A
    import skibot.reselect as R

    work, video = make_work(tmp / "rollback")
    reselect_one(work, 0, level="novice", fallback_video=video)
    old_video = (work / "t1_annotated.mp4").read_bytes()
    old_report = (work / "report.json").read_bytes()

    real = A.replace_with_retry

    def flaky(src, dst, attempts=5):
        if dst.name == "report.json":
            raise PermissionError("报告被锁")
        return real(src, dst, attempts)

    A.replace_with_retry = flaky
    try:
        try:
            reselect_one(work, 1, level="novice", fallback_video=video)
            raise AssertionError("报告替换失败应抛出")
        except PermissionError:
            pass
    finally:
        A.replace_with_retry = real

    assert (work / "t1_annotated.mp4").read_bytes() == old_video  # 已回滚
    assert (work / "report.json").read_bytes() == old_report
    assert not (work / "t1_annotated.mp4.bak").exists()

    print("  改选：报告替换失败时视频回滚 ✓")


def test_bad_trc_and_long_trc(tmp: Path):
    """坏轨迹不拖垮候选列表；trc 比视频长时预览不越界画错帧。"""
    work, video = make_work(tmp / "badtrc")
    # 0 字节轨迹：加载失败，应被跳过而不是让整页崩掉
    (work / "t1_px_person02.trc").write_bytes(b"")
    assert [c.pid for c in list_candidates(work)] == [0, 1]

    # trc 60 帧但视频只有 6 帧，且人物从第 50 帧才有效：
    # 有效帧在视频范围外 → 不写预览，也不拿越界帧号画骨架
    write_person_trc(work / "t1_px_person03.trc", n_frames=60,
                     valid_from=50, dx=90.0)
    outs = render_previews(work, video)
    assert [p.name for p in outs] == ["candidate_person0.jpg",
                                      "candidate_person1.jpg"]
    assert not (work / "candidate_person3.jpg").exists()

    print("  改选：坏轨迹跳过 / 超长轨迹预览不越界 ✓")


def test_preview_matches_person(tmp: Path):
    """每张预览画的是对应人物在该帧的骨架，不是所有人的同一份。"""
    import skibot.reselect as R
    from skibot.trc_io import load_kp

    work, video = make_work(tmp / "match")
    drawn: list[np.ndarray] = []
    orig = R._draw_skeleton
    R._draw_skeleton = lambda img, kp_f: drawn.append(kp_f.copy())
    try:
        outs = render_previews(work, video)
    finally:
        R._draw_skeleton = orig
    assert len(drawn) == 2 and len(outs) == 2
    # 两个候选的骨架横向相差 dx=30（夹具里 pid 0/1 分别平移 0/30px）
    assert drawn[0][5][0] + 30 == drawn[1][5][0]
    assert not np.array_equal(drawn[0], drawn[1])

    print("  改选：预览骨架与人物一一对应 ✓")


def test_no_pose_rerun(tmp: Path):
    """改选只重跑评估与渲染：落盘的 trc 一个字节都不动。"""
    import hashlib

    work, video = make_work(tmp / "nopose")

    def snap():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(work.glob("*_px_person*.trc"))}

    before = snap()
    reselect_one(work, 1, level="novice", fallback_video=video)
    assert snap() == before

    print("  改选：不重跑姿态检测（trc 未改动）✓")


def test_blurred_source(tmp: Path):
    """打过码的首次分析：改选与预览都从打码副本取画面，不回退原片。"""
    import skibot.finalize as F
    import skibot.reselect as R

    work, video = make_work(tmp / "blur")
    blurred = work / "t1_blurred.mp4"
    blurred.write_bytes(video.read_bytes())  # 内容不重要，路径选择才是契约

    seen = {}
    orig_probe, orig_render = R.probe_video, F.render_annotated

    def fake_probe(p):
        seen["probe"] = Path(p)
        return 640, 480, 30.0

    def fake_render(src, kp, res, out, view=None, **kwargs):
        seen["render"] = Path(src)
        Path(out).write_bytes(b"x")
        return Path(out)

    R.probe_video, F.render_annotated = fake_probe, fake_render
    try:
        # 未显式给 blur：副本存在 → 用副本
        reselect_one(work, 1, level="novice", fallback_video=video)
        assert seen["probe"] == blurred and seen["render"] == blurred

        # 显式 blur=False（首次分析没打码）：即使留着旧副本也用上传原件
        seen.clear()
        reselect_one(work, 1, level="novice", fallback_video=video, blur=False)
        assert seen["probe"] == video and seen["render"] == video

        # 没有打码副本 → 用上传原件（未打码路径）
        work2, video2 = make_work(tmp / "noblur")
        seen.clear()
        reselect_one(work2, 0, level="novice", fallback_video=video2)
        assert seen["probe"] == video2

        # 显式 blur=True 但副本丢了：报错，绝不静默改用未打码原片
        work3, video3 = make_work(tmp / "lostblur")
        try:
            reselect_one(work3, 0, level="novice", fallback_video=video3,
                         blur=True)
            raise AssertionError("打码副本丢失时应报错")
        except FileNotFoundError as e:
            assert "打码副本" in str(e)
    finally:
        R.probe_video, F.render_annotated = orig_probe, orig_render

    print("  改选：打码副本优先 / 显式 blur 设置 / 副本丢失报错 ✓")


def test_preview_partial_track(tmp: Path):
    """轨迹只覆盖前几帧时，预览取该轨迹最近的有效帧而不是空骨架。"""
    import skibot.reselect as R
    from skibot.trc_io import load_kp

    work, video = make_work(tmp / "partial")
    # person1 只在前 2 帧有数据（模拟中途出画）
    trc = work / "t1_px_person01.trc"
    write_person_trc(trc, dx=30.0, valid_frames=2)

    drawn: list[np.ndarray] = []
    orig = R._draw_skeleton
    R._draw_skeleton = lambda img, kp_f: drawn.append(kp_f.copy())
    try:
        outs = render_previews(work, video)
    finally:
        R._draw_skeleton = orig
    assert len(outs) == 2
    # 画的是第 2 帧（索引 1）而不是没有数据的第 3 帧
    expected = load_kp(trc)
    assert any(np.array_equal(d, expected[1], equal_nan=True) for d in drawn)
    assert not any(np.array_equal(d, expected[3], equal_nan=True)
                   for d in drawn)

    print("  改选：部分轨迹的预览取最近有效帧 ✓")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        test_candidates(Path(td))
        test_reselect(Path(td))
        test_previews(Path(td))
        test_render_failure_keeps_report(Path(td))
        test_report_replace_failure_rolls_back(Path(td))
        test_bad_trc_and_long_trc(Path(td))
        test_preview_matches_person(Path(td))
        test_preview_partial_track(Path(td))
        test_no_pose_rerun(Path(td))
        test_blurred_source(Path(td))
    print("全部通过")
