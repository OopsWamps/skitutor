"""评估收尾测试：python tests/test_finalize.py

assess_and_publish 是报告与标注视频的唯一发布点：成对原子替换、渲染失败
保持旧产物、只落报告的抢救模式；lifter+fps 时 animation.json 同链成对
落盘、降级二维时删除。用合成侧视骨架 + 小视频，不跑 Sports2D。
"""

import json
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skibot.finalize import assess_and_publish  # noqa: E402
from skibot.reference import load  # noqa: E402
from skibot.skeleton import (  # noqa: E402
    L_HIP, L_KNEE, L_SHOULDER, NOSE, R_HIP, R_KNEE, R_SHOULDER,
)

ROOT = Path(__file__).resolve().parent.parent
REFS = load(ROOT / "reference_values.toml")


def side_kp(n: int = 12) -> np.ndarray:
    """n 帧直立侧面骨架（视角 sagittal）。"""
    frames = []
    for _ in range(n):
        kp = np.zeros((26, 3))
        kp[L_SHOULDER] = [10, 10, 0.9]
        kp[R_SHOULDER] = [11, 10, 0.9]
        kp[L_HIP] = [11, 20, 0.9]
        kp[R_HIP] = [12, 20, 0.9]
        kp[L_KNEE] = [11, 30, 0.9]
        kp[R_KNEE] = [12, 30, 0.9]
        kp[NOSE] = [14, 6, 0.9]
        frames.append(kp)
    return np.stack(frames)


def tiny_video(path: Path, n: int = 12) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 48))
    assert w.isOpened()
    for _ in range(n):
        w.write(np.full((48, 64, 3), 40, np.uint8))
    w.release()
    return path


def test_publish_report_and_video(tmp: Path):
    """发布后报告与标注视频就位，临时文件清理干净。"""
    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_finalize_"))
    src = tiny_video(tmp / "src.mp4")
    rp, vp = tmp / "report.json", tmp / "v_annotated.mp4"

    res = assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                             label="t.mp4", report_path=rp, video_path=vp)
    assert rp.exists() and rp.stat().st_size > 0
    assert vp.exists() and vp.stat().st_size > 0
    assert res.report["view"]["detected"] == "sagittal"
    # 发布的视频是浏览器可解的 H.264（OpenCV 的 FMP4 网页里黑屏），帧数不丢
    import cv2

    cap = cv2.VideoCapture(str(vp))
    fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
    codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).lower()
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert codec in ("h264", "avc1"), codec
    assert abs(n_frames - 12) <= 1, n_frames
    assert not list(tmp.glob("*.tmp")) and not list(tmp.glob("*.bak"))
    print("  finalize：报告 + 标注视频成对发布（H.264）✓")


def test_report_only_mode(tmp: Path):
    """video_path=None（抢救路径）：只落报告，不渲染。"""
    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_finalize_ro_"))
    rp = tmp / "report.json"
    assess_and_publish(side_kp(), None, refs=REFS, level="intermediate",
                       label="t.trc", report_path=rp)
    assert rp.exists()
    assert not any(tmp.glob("*.mp4"))
    print("  finalize：只落报告的抢救模式 ✓")


def test_render_failure_keeps_old_pair(tmp: Path):
    """渲染失败：旧报告与旧视频原样保留，临时文件清干净。"""
    import skibot.finalize as F

    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_finalize_fail_"))
    src = tiny_video(tmp / "src.mp4")
    rp, vp = tmp / "report.json", tmp / "v_annotated.mp4"
    assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                       label="t.mp4", report_path=rp, video_path=vp)
    old_report, old_video = rp.read_bytes(), vp.read_bytes()

    def boom(*a, **k):
        raise RuntimeError("渲染挂了")

    orig = F.render_annotated
    F.render_annotated = boom
    try:
        try:
            assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                               label="t.mp4", report_path=rp, video_path=vp)
            raise AssertionError("渲染失败应抛出")
        except RuntimeError:
            pass
    finally:
        F.render_annotated = orig

    assert rp.read_bytes() == old_report and vp.read_bytes() == old_video
    assert not list(tmp.glob("*.tmp")) and not list(tmp.glob("*.bak"))
    print("  finalize：渲染失败保持旧产物对 ✓")


def test_publish_with_lifter_writes_animation(tmp: Path):
    """lifter+fps：animation.json 与报告成对落盘，report 带 phases 摘要；
    转码只发生在 render_annotated 内部一次（finalize 不再自己转）。"""
    import skibot.finalize as F
    from skibot.skeleton3d import MockLifter

    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_finalize_anim_"))
    src = tiny_video(tmp / "src.mp4")
    rp, vp = tmp / "report.json", tmp / "v_annotated.mp4"
    seen: dict = {}
    orig_render = F.render_annotated

    def spy(*a, **k):
        seen.update(k)
        return orig_render(*a, **k)

    F.render_annotated = spy
    try:
        res = assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                                 label="t.mp4", report_path=rp, video_path=vp,
                                 fps=30.0, lifter=MockLifter())
    finally:
        F.render_annotated = orig_render
    assert "transcode_h264" not in seen  # 布尔参数已删除，发布链不再干预转码
    anim_p = tmp / "animation.json"
    assert anim_p.exists()
    anim = json.loads(anim_p.read_text(encoding="utf-8"))
    assert anim["schema"] == "skibot.animation/0.1"
    assert anim["fps"] == 30.0
    assert len(anim["frames"]) == 12 and len(anim["frames"][0]) == 26
    assert len(anim["frames"][0][0]) == 3
    assert len(anim["joints"]) == 26 and all(len(e) == 2 for e in anim["edges"])
    assert anim["source"] == "mock" and anim["phases"]["fps"] == 30.0
    # 逐帧相位归属与中文标签服务端算好（回放卡 JS 不再按 turns 自己推）
    assert anim["phase_of"] == [""] * 12  # 静态骨架没有合格弯
    assert anim["phase_labels"] == {"entry": "入弯", "control": "控制",
                                    "exit": "出弯"}
    # 序列是逐帧一维；置信与骨架同形
    assert all(isinstance(v, list) for v in anim["series"].values())
    assert len(anim["confidence"]) == 12
    # 报告带相位摘要：静态骨架没有合格弯 → aggregate None + fallback
    assert res.report["phases"]["n_turns"] == 0
    assert res.report["phases"]["fallback"] == "no_valid_turns"
    assert res.report["phases"]["aggregate"] is None
    assert not list(tmp.glob("*.tmp")) and not list(tmp.glob("*.bak"))
    print("  finalize：lifter 发布成对落 animation.json + phases 摘要 ✓")


def test_downgrade_removes_animation(tmp: Path):
    """同目录重跑降级成二维：旧动画在发布成功后删除，报告不再带 phases。"""
    from skibot.skeleton3d import MockLifter

    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_finalize_down_"))
    src = tiny_video(tmp / "src.mp4")
    rp, vp = tmp / "report.json", tmp / "v_annotated.mp4"
    assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                       label="t.mp4", report_path=rp, video_path=vp,
                       fps=30.0, lifter=MockLifter())
    assert (tmp / "animation.json").exists()
    res = assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                             label="t.mp4", report_path=rp, video_path=vp,
                             fps=30.0)
    assert not (tmp / "animation.json").exists()
    assert "phases" not in res.report
    assert not list(tmp.glob("*.tmp")) and not list(tmp.glob("*.bak"))
    print("  finalize：降级二维删旧动画 ✓")


def test_render_failure_keeps_animation_pair(tmp: Path):
    """渲染失败：旧报告、旧视频与旧动画原样保留（动画也回滚）。"""
    import skibot.finalize as F
    from skibot.skeleton3d import MockLifter

    tmp = Path(tempfile.mkdtemp(prefix="skibot_test_finalize_animfail_"))
    src = tiny_video(tmp / "src.mp4")
    rp, vp = tmp / "report.json", tmp / "v_annotated.mp4"
    assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                       label="t.mp4", report_path=rp, video_path=vp,
                       fps=30.0, lifter=MockLifter())
    old = {p.name: p.read_bytes()
           for p in (rp, vp, tmp / "animation.json")}

    def boom(*a, **k):
        raise RuntimeError("渲染挂了")

    orig = F.render_annotated
    F.render_annotated = boom
    try:
        try:
            assess_and_publish(side_kp(), src, refs=REFS, level="novice",
                               label="t.mp4", report_path=rp, video_path=vp,
                               fps=30.0, lifter=MockLifter())
            raise AssertionError("渲染失败应抛出")
        except RuntimeError:
            pass
    finally:
        F.render_annotated = orig
    assert {p.name: p.read_bytes()
            for p in (rp, vp, tmp / "animation.json")} == old
    assert not list(tmp.glob("*.tmp")) and not list(tmp.glob("*.bak"))
    print("  finalize：渲染失败保持报告/视频/动画三元组 ✓")


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        test_publish_report_and_video(t / "publish")
        test_report_only_mode(t / "reportonly")
        test_render_failure_keeps_old_pair(t / "fail")
        test_publish_with_lifter_writes_animation(t / "anim")
        test_downgrade_removes_animation(t / "down")
        test_render_failure_keeps_animation_pair(t / "animfail")
    print("全部通过")
