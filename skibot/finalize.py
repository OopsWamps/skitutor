"""评估收尾：assess → 原子落盘报告 → 渲染标注视频，只此一条链。

此前 cli.analyze_one / reselect_one / render 子命令 / 抢救路径四处各串一遍，
且只有改选带原子替换——生产 web 主路径（任务表 worker 跑的 analyze_one）
反而没有报告/视频错配保护。收进一个 module 后：报告与标注视频都先写临时
文件、渲染成功后按「视频 → 动画 → 报告」顺序原子替换，任何一步失败都保持
旧产物；替换走 Windows 锁重试（artifacts）。复用同一结果目录重跑时，旧 trc 的
清场也属于管线职责，由 analyze_one 在跑姿态检测前完成。

有三维骨架且帧率已知时，同链多发布一份 animation.json（报告页回放卡的
数据源）；同目录重跑降级成二维时，旧动画在发布成功后删除——报告与回放
数据永远出自同一次评估。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from . import artifacts
from .assess import Assessment, assess
from .phases import PHASE_METRICS
from .render import ensure_browser_mp4, render_annotated
from .report import save_json
from .skeleton import HALPE_NAMES, SKELETON_EDGES
from .skeleton3d import Lifter


def build_animation(res: Assessment) -> dict | None:
    """Assessment → animation.json 载荷；无三维骨架或无帧率时返回 None。

    root-relative 逐帧骨架（3 位小数，缺测关节为 null）+ 相位切分明细 +
    三项矢状序列（1 位小数，回放卡指标叠加用）。fps 只记在
    PhaseSegmentation 里；没有相位切分（抢救路径没传帧率）就做不了
    视频-骨架对时，不产出动画。
    """
    sk3 = res.skeleton3d
    if sk3 is None or res.phases is None:
        return None
    series = res.series3d or {}

    def _nums2(rows: np.ndarray, nd: int) -> list:
        return [[None if not np.isfinite(v) else round(float(v), nd) for v in row]
                for row in np.asarray(rows, dtype=float)]

    def _nums1(values: np.ndarray, nd: int) -> list:
        return [None if not np.isfinite(v) else round(float(v), nd)
                for v in np.asarray(values, dtype=float)]

    return {
        "schema": "skibot.animation/0.1",
        "fps": res.phases.fps,
        "n_frames": sk3.n_frames,
        "source": sk3.source,
        "gravity_source": sk3.gravity_source,
        "note": "root-relative 骨架，y 轴向上（重力参考）；单目提升未做多机位验证",
        "joints": list(HALPE_NAMES),
        "edges": [list(e) for e in SKELETON_EDGES],
        "phases": res.phases.to_dict(),
        "series": {name: _nums1(series[name], 1) for name in PHASE_METRICS
                   if name in series},
        "confidence": _nums2(sk3.confidence, 2),
        "frames": [[[None if not np.isfinite(c) else round(float(c), 3)
                     for c in joint] for joint in frame]
                   for frame in sk3.joints],
    }


def _quiet_unlink(path: Path) -> None:
    """发布成功后的清理删除：重试耗尽只告警，不把成功翻成失败。

    残留的 .bak / 旧动画下次发布会被覆盖或再次尝试删除，不影响正确性。
    """
    try:
        artifacts.unlink_with_retry(path)
    except OSError as e:
        print(f"[finalize] 警告：清理 {path.name} 失败（{e}），"
              "文件留在结果目录，下次发布会覆盖或重试", file=sys.stderr)


def assess_and_publish(
    kp: np.ndarray,
    src: str | Path | None,
    *,
    refs: dict,
    level: str,
    label: str,
    report_path: str | Path,
    video_path: str | Path | None = None,
    person: dict | None = None,
    frame_height: float | None = None,
    fps: float | None = None,
    lifter: Lifter | None = None,
    slope_sag_deg: float | None = None,
    quality: dict | None = None,
    calibrated: bool = False,
) -> Assessment:
    """评估一次，报告与标注视频成对发布。

    video_path 给出时同时渲染标注视频（此时 src 必须给出——渲染要画面）；
    None 时只落报告（抢救路径、render 子命令不需要落盘的场景自行 assess）。
    失败语义：任何一步抛出，旧的报告/标注视频原样保留（先备份旧视频，
    报告替换失败则回滚视频），临时文件清理干净；发布后的报告与叠加层出自
    同一次评估。
    """
    if video_path is not None and src is None:
        raise ValueError("要渲染标注视频就必须给视频源 src")
    res = assess(kp, level=level, refs=refs, label=label, person=person,
                 calibrated=calibrated, quality=quality,
                 frame_height=frame_height, lifter=lifter,
                 slope_sag_deg=slope_sag_deg, fps=fps)

    report_path = Path(report_path)
    tmp_report = report_path.with_name(report_path.name + ".tmp")
    tmp_video: Path | None = None
    backup: Path | None = None
    if video_path is not None:
        video_path = Path(video_path)
        tmp_video = video_path.with_suffix(".tmp.mp4")
        backup = video_path.with_name(video_path.name + ".bak")
    had_old = video_path is not None and video_path.exists()
    # 动画与报告同链发布：有三维数据就成对替换，降级二维时删旧动画
    anim = build_animation(res)
    anim_path = artifacts.animation_path(report_path.parent)
    tmp_anim = anim_path.with_name(anim_path.name + ".tmp")
    backup_anim = anim_path.with_name(anim_path.name + ".bak")
    had_old_anim = anim_path.exists()
    try:
        save_json(res.report, tmp_report)
        if video_path is not None and tmp_video is not None and backup is not None:
            if src is None:  # 开头已把 src 与 video_path 成对校验；再判一次收窄类型
                raise ValueError("要渲染标注视频就必须给视频源 src")
            # 转码交给下面的 ensure_browser_mp4 统一做（CRF 18）：render 内
            # 再转一遍是同一段视频的第二次完整有损转码，白耗一倍时间
            render_annotated(src, kp, res, tmp_video, transcode_h264=False)
            if not tmp_video.exists() or tmp_video.stat().st_size == 0:
                raise RuntimeError("标注视频渲染失败：输出为空")
            # OpenCV 只会写 FMP4，浏览器 <video> 解不了（报告页/回放卡黑屏）；
            # 发布前转 H.264。转码失败在函数内降级保留原编码，不挡发布
            ensure_browser_mp4(tmp_video)
            # 先把旧视频挪开备份：报告替换若失败，把旧视频换回来，
            # 报告与视频仍停在旧产物，不会一半新一半旧
            if had_old:
                artifacts.replace_with_retry(video_path, backup)
            artifacts.replace_with_retry(tmp_video, video_path)
        if anim is not None:
            tmp_anim.write_text(json.dumps(anim, ensure_ascii=False),
                                encoding="utf-8")
            if had_old_anim:
                artifacts.replace_with_retry(anim_path, backup_anim)
            artifacts.replace_with_retry(tmp_anim, anim_path)
        artifacts.replace_with_retry(tmp_report, report_path)
    except Exception:
        if had_old and video_path is not None and backup is not None and backup.exists():
            try:
                artifacts.replace_with_retry(backup, video_path)  # 回滚到旧视频
            except OSError as e:
                # 回滚也失败：磁盘停在「新视频 + 旧报告」的错配态，必须喊
                # 出来——旧视频留在 .bak，让用户能手工恢复
                print(f"[finalize] 警告：旧标注视频回滚失败（{e}）；"
                      f"旧视频保留在 {backup}，当前视频已是新渲染产物，"
                      "报告与视频可能错配，请重新发布", file=sys.stderr)
        if had_old_anim and backup_anim.exists():
            try:
                artifacts.replace_with_retry(backup_anim, anim_path)
            except OSError as e:
                print(f"[finalize] 警告：旧动画回滚失败（{e}）；"
                      f"旧文件保留在 {backup_anim}，请重新发布", file=sys.stderr)
        raise
    else:
        # 发布已成功。清理与降级删除只告警不抛：Windows 上杀毒/静态请求
        # 可能短暂锁住 .bak 或旧动画，为此把已成功的任务翻成失败（还会在
        # except 里把新视频回滚成旧的）比留下一个多余文件糟得多
        if backup is not None:
            _quiet_unlink(backup)
        _quiet_unlink(backup_anim)
        if anim is None and had_old_anim:
            # 本次没有三维数据（未开 lifter / 缺帧率）：报告发布成功后
            # 才删旧动画——失败路径报告还是旧三维版，动画与它仍然配套
            _quiet_unlink(anim_path)
    finally:
        if tmp_video is not None:
            tmp_video.unlink(missing_ok=True)
        tmp_anim.unlink(missing_ok=True)
        tmp_report.unlink(missing_ok=True)
    return res
