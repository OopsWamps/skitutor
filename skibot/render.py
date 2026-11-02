"""自绘标注视频：只画选中者，逐帧全量写出，叠加二维五项指标（过渡期口径）。

替代 Sports2D 的标注视频（实测两大问题：5 人全画、输出只剩 31/427 帧）。
骨架画在打码后的画面上（帧号与 trc 一致）；指标数值按参考区间绿/红着色。
序列、视角判定与参考区间全部出自 assess 的评估结果（Assessment）——
报告与叠加层出自同一次计算，本模块不重算、不向调用方要第二遍数据。
叠加哪套指标走 metrics.active_for_view 这一条规则（与报告共用）；显式
view 覆盖判定时在画面上留痕（override 标注），不让叠加层悄悄越出报告。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from . import artifacts, metrics as M
from .assess import Assessment
from .reference import lookup
from .skeleton import (
    L_ANKLE, L_HIP, L_KNEE, L_SHOULDER,
    R_ANKLE, R_HIP, R_KNEE, R_SHOULDER,
)

EDGES = [(L_SHOULDER, R_SHOULDER), (L_SHOULDER, L_HIP), (R_SHOULDER, R_HIP),
         (L_HIP, R_HIP), (L_HIP, L_KNEE), (L_KNEE, L_ANKLE),
         (R_HIP, R_KNEE), (R_KNEE, R_ANKLE)]
JOINTS = [L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE]

GREEN, RED, WHITE = (80, 220, 80), (60, 60, 230), (240, 240, 240)


def probe_video(video_src: str | Path) -> tuple[int, int, float]:
    """视频元数据探测：（宽, 高, 帧率）。打不开时抛 FileNotFoundError。"""
    cap = cv2.VideoCapture(str(video_src))
    if not cap.isOpened():
        raise FileNotFoundError(f"打不开视频: {video_src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return w, h, fps


def ensure_browser_mp4(path: Path) -> Path:
    """把 OpenCV 写出的 FMP4（MPEG-4 Part 2）原地转成浏览器可解的 H.264。

    cv2.VideoWriter 只有 mp4v 可用，浏览器 <video> 解不了 FMP4——报告页与
    回放卡的视频会黑屏。这里用 imageio-ffmpeg 自带的 ffmpeg 转 H.264
    （CRF 18 近无损、yuv420p、faststart），帧数与源 1:1（2026-10-26 在
    标注服务器上逐帧核对过）。ffmpeg 不可用时保留原编码并告警：视频仍能
    下载播放，只是网页内可能黑屏，不让整个分析失败。
    """
    tmp = path.with_name(path.name + ".h264.mp4")
    try:
        try:
            # imageio_ffmpeg 无类型标记（pyproject 已声明依赖）
            from imageio_ffmpeg import get_ffmpeg_exe  # type: ignore[import-untyped]
            r = subprocess.run(
                [get_ffmpeg_exe(), "-y", "-i", str(path),
                 "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(tmp)],
                capture_output=True)
            if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
                raise RuntimeError(f"ffmpeg 转码失败：{r.stderr[-300:]!r}")
            # 替换也可能因 Windows 文件锁重试耗尽而失败——此时原文件还是
            # mp4v，降级即可；临时文件必须清掉，不能留在结果目录里
            artifacts.replace_with_retry(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)  # 替换成功后已不存在，仅清理失败残留
    except Exception as e:  # noqa: BLE001 —— 编码是展示层，失败降级不挡分析
        print(f"[render] 警告：{path.name} 转 H.264 失败（{e}），"
              "保留 OpenCV 编码，网页内可能无法播放", file=sys.stderr)
    return path


def render_annotated(
    video_src: str | Path,
    kp: np.ndarray,                       # (frames,26,3) [x,y,conf]
    assessment: Assessment,               # assess 的评估结果：序列、视角、参考区间
    out_path: str | Path,
    view: str | None = None,              # 覆盖叠加哪套指标；默认按评估判定（画面留痕）
    transcode_h264: bool = True,          # 发布链自己转编码时置 False，免二次转码
) -> Path:
    cap = cv2.VideoCapture(str(video_src))
    if not cap.isOpened():
        raise FileNotFoundError(f"打不开视频: {video_src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))  # type: ignore[attr-defined]
    if not writer.isOpened():
        # 不检查的话 write 全部静默丢弃，调用方拿到一个空文件当成功
        cap.release()
        raise RuntimeError(f"无法创建标注视频写入器（路径不可写？）: {out_path}")

    series = assessment.series
    detected = view or assessment.view.view
    # 与报告共用的唯一门控规则；level 与参考区间都来自同一次评估
    active = M.active_for_view(detected)
    ref_by_metric = {m: lookup(assessment.refs, m, assessment.report["level"])
                     for m in active}
    override = view is not None and view != assessment.view.view

    f = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if f < kp.shape[0]:
            _draw_skeleton(frame, kp[f])
            _draw_metrics(frame, f, active, series, ref_by_metric,
                          override_view=detected if override else None)
        writer.write(frame)
        f += 1

    cap.release()
    writer.release()
    # 发布链（finalize）在临时文件上再转一次 H.264；这里关掉，避免同一段
    # 视频被完整转码两遍（一遍 CRF 缺省、一遍 CRF 18，白耗一倍时间且二次
    # 有损）。直接调用 render_annotated 的场景保留默认转码。
    if not transcode_h264:
        return out_path
    return _transcode_h264(out_path)


def _transcode_h264(out_path: Path) -> Path:
    """mp4v 浏览器 <video> 不支持，转 H.264 + yuv420p + faststart。

    转码失败（ffmpeg 缺失等）保留原文件并降级——本地播放器仍可播 mp4v。
    """
    import subprocess

    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        print("[skibot] imageio_ffmpeg 不可用，标注视频保持 mp4v（浏览器或无法播放）",
              file=sys.stderr)
        return out_path

    tmp = out_path.with_suffix(".h264.mp4")
    try:
        r = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", str(out_path),
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
             str(tmp)],
            check=False, capture_output=True)
        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            artifacts.replace_with_retry(tmp, out_path)
            return out_path
        print(f"[skibot] 标注视频 H.264 转码失败，保留 mp4v：{r.stderr[-200:]!r}",
              file=sys.stderr)
    except OSError as e:
        print(f"[skibot] 标注视频 H.264 替换失败，保留 mp4v：{e}", file=sys.stderr)
    finally:
        tmp.unlink(missing_ok=True)  # 替换成功后已不存在，仅清理失败残留
    return out_path


def _draw_skeleton(frame, kp_f) -> None:
    for a, b in EDGES:
        if kp_f[a][2] > 0 and kp_f[b][2] > 0:
            cv2.line(frame, tuple(kp_f[a][:2].astype(int)), tuple(kp_f[b][:2].astype(int)),
                     GREEN, 3, cv2.LINE_AA)
    for j in JOINTS:
        if kp_f[j][2] > 0:
            cv2.circle(frame, tuple(kp_f[j][:2].astype(int)), 5, WHITE, -1, cv2.LINE_AA)


def _draw_metrics(frame, f, active, series, refs,
                  override_view: str | None = None) -> None:
    y = 28
    cv2.putText(frame, "skibot", (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, GREEN, 2, cv2.LINE_AA)
    y += 30
    if override_view:
        # 覆盖判定时在画面留痕：叠加层画了报告没放行的指标集，得让人看见
        cv2.putText(frame, f"view: {override_view} (override)", (12, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, WHITE, 2, cv2.LINE_AA)
        y += 26
    for m in active:
        s = series[m]
        ref = refs[m]
        v = s[f] if f < len(s) else np.nan
        label = m  # cv2 Hershey 字体不支持中文，叠加层用指标键名；中文名见 JSON 报告
        if np.isnan(v):
            line, color = f"{label}: -", WHITE
        else:
            in_range = ref.lo <= v <= ref.hi
            color = GREEN if in_range else RED
            line = f"{label}: {v:.0f} deg (ref {ref.lo:.0f}-{ref.hi:.0f})"
        cv2.putText(frame, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
        y += 26
