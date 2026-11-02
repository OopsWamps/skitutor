"""评估流程：关键点 → 视角分类 → 二维五指标 → 可选三维骨架与六项指标 → 参考区间。

主路径（cli 选完人后）与抢救路径（scripts/report_from_trc.py）共用同一条
流程 —— 一期 #10 的量角对账走抢救路径，行为必须与主路径一致。
Assessment 是唯一出口：报告、角度序列、帧级视角归属一次算齐整体交付，
render 等下游收现成结果，不再重算。传入 lifter 时同一出口多带一份
Skeleton3D 与三维序列；二维输出不因三维而改变（issue 03/04）。二期弯相位
划分与相位参考带（issue 10/12）在此插入。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import metrics as M
from . import metrics3d as M3
from . import phases as P
from .metrics3d import TRUNK_LEAN_SERIES
from .skeleton import CONF_MIN, CORE_JOINTS, L_HIP, R_HIP, size_score
from .skeleton3d import Lifter, Skeleton3D
from .report import build_report
from .view import ViewResult, classify_view, classify_view_frames

LOW_CONF_CORE_RATIO = 0.75  # 核心八点有效占比低于此值的帧记为低置信度帧


@dataclass
class Assessment:
    """评估结果：报告 dict、二维序列、帧级视角归属、可选三维骨架与序列。

    下游（render、对账脚本）消费这个整体，不要自己再算 series/frame_view
    —— 报告与叠加层必须出自同一次计算。view（整视频视角判定）与 refs
    （本次评估用的参考区间）也随行：render 从这里取判定与着色数据，不再
    要调用方传第二遍、也不再翻 report dict。skeleton3d / series3d 只在
    传入 lifter 时存在；phases 还要再给 fps。它们不改变二维报告与序列。
    """
    report: dict
    series: dict[str, np.ndarray]
    frame_view: np.ndarray
    view: ViewResult
    refs: dict
    skeleton3d: Skeleton3D | None = None
    series3d: dict[str, np.ndarray] | None = None
    phases: P.PhaseSegmentation | None = None


def compute_quality(keypoints: np.ndarray, frame_height: float | None = None) -> dict:
    """质量统计（验证进展 #3 的报告侧缓解输入）：人物高度占比 + 低置信度帧占比。

    trc 的 conf 是 0/1 存在标记，判核心 8 点而非全部 26 点 —— 雪场远景
    缺脸/手脚趾很常见，全点口径会让提示常驻失去区分度。判读阈值与文案
    在 report 层，这里只出统计。
    """
    quality: dict = {}
    if frame_height:
        quality["person_height_ratio"] = round(size_score(keypoints) / frame_height, 3)
    core_ok = (keypoints[:, CORE_JOINTS, 2] > 0).mean(axis=1)
    quality["low_conf_frame_ratio"] = round(float((core_ok < LOW_CONF_CORE_RATIO).mean()), 3)
    return quality


def assess(keypoints: np.ndarray, *, level: str,
           refs: dict, label: str,
           person: dict | None = None, calibrated: bool = False,
           quality: dict | None = None, frame_height: float | None = None,
           lifter: Lifter | None = None,
           slope_sag_deg: float | None = None,
           fps: float | None = None) -> Assessment:
    """keypoints: (frames, n_kp, >=3)，skeleton 契约。返回评估结果（不落盘）。

    quality：调用方追加的质量统计项（同名键优先于内部统计）。
    lifter：可选的二维→三维适配器。传入时 Assessment 多带 Skeleton3D 与
    三维序列；三维指标对所有有效帧计算，不受视角门控影响。二维报告、序列
    与视角归属逐位不变。
    slope_sag_deg：现场 IMU 坡度在受试者矢状面的带符号分量；只影响三维
    躯干前倾的坡面版本，None 时该项为空。
    fps：帧率；只有同时有 lifter 时用它切相位。None 时不做相位切分，
    Assessment.phases 为 None（抢救路径没有视频帧率时走这条）。
    """
    frame_view = classify_view_frames(keypoints)
    vr = classify_view(keypoints, frame_view=frame_view)
    series = M.compute_series(keypoints, frame_view=frame_view)
    sk3 = lifter.lift(keypoints) if lifter is not None else None
    if sk3 is not None and sk3.n_frames != keypoints.shape[0]:
        raise ValueError(f"三维骨架帧数 {sk3.n_frames} != 二维关键点帧数 {keypoints.shape[0]}")
    series3d = None
    gravity_source = "unknown"
    slope_source = "none"
    if sk3 is not None:
        # 重力来源是 Skeleton3D 的契约字段（构造时校验），unknown 时不出口
        gravity_source = sk3.gravity_source
        series3d = M3.compute_series3d(sk3, slope_sag_deg=slope_sag_deg)
        slope_source = "imu" if slope_sag_deg is not None else "none"
    phase_seg = None
    if series3d is not None and fps is not None:
        # 横向轨迹用画面髋中点 x；主信号与膝弯周期都来自三维序列。
        # 相位主信号 = 躯干前倾的冠状分量（换刃过零），键取自分组的
        # TRUNK_LEAN_SERIES，键名变更在这里断而不是悄悄拿空。
        left = keypoints[:, L_HIP]
        right = keypoints[:, R_HIP]
        ok = (left[:, 2] >= CONF_MIN) & (right[:, 2] >= CONF_MIN)
        lateral = np.where(ok, (left[:, 0] + right[:, 0]) / 2.0, np.nan)
        phase_seg = P.segment_phases(
            series3d[TRUNK_LEAN_SERIES[1]], fps=fps,
            knee=series3d["knee_flexion"], lateral=lateral)
    q = dict(quality) if quality else {}
    for k, v in compute_quality(keypoints, frame_height).items():
        q.setdefault(k, v)
    if sk3 is not None and frame_view.size:
        # 三维列取消视角门控后，视角分类只作为质量统计保留；二维列不加
        # 这个字段，保持旧报告逐位不变。
        q.setdefault("view_frame_ratio", {
            "sagittal": round(float(np.mean(frame_view == "l")), 3),
            "frontal": round(float(np.mean(frame_view == "f")), 3),
            "none": round(float(np.mean(frame_view == "")), 3),
        })
    phases_summary = None
    if phase_seg is not None and series3d is not None:
        agg = P.aggregate_phases(series3d, phase_seg)
        if agg is not None:
            # 聚合值只留一位小数：报告是给人看的，相位聚合不是对账材料
            agg = {m: {ph: ({**v, "median_deg":
                             None if v["median_deg"] is None
                             else round(v["median_deg"], 1)}
                            if isinstance(v, dict) else v)
                       for ph, v in phases.items()}
                   for m, phases in agg.items()}
        phases_summary = {
            "n_turns": len(phase_seg.turns),
            "fallback": phase_seg.fallback,
            "aggregate": agg,
            "note": ("相位切分未通过人工标注验收（2026-10：单目置信门弯数"
                     "覆盖不足），逐弯数值仅供参考；三维参考带待多机位"
                     "验证后生成"),
        }
    report = build_report(label, vr, series, refs, level,
                          person=person, calibrated=calibrated, quality=q,
                          series3d=series3d, gravity_source=gravity_source,
                          slope_source=slope_source,
                          data_source=sk3.source if sk3 is not None else None,
                          phases=phases_summary)
    return Assessment(report=report, series=series, frame_view=frame_view,
                      view=vr, refs=refs,
                      skeleton3d=sk3, series3d=series3d, phases=phase_seg)
