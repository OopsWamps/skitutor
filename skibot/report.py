"""结构化报告生成（JSON）。

决策依据 docs/DECISIONS.md 第 9 条：报告是价值层，标注视频是信任层；不做评分。
每项指标给出：区间、达标率、一句话判断。参考值未校准期间强制带水印字段。
二维条目消费 metrics.TWO_D_METRICS（含视角门控）；传入 series3d 时在同一
metrics[] 追加 basis="3d" 的六项，参考带未建立，只给描述统计。相位对象在
issue 12 接入。字段迁移见 docs/report_schema.md。
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

import numpy as np

from . import artifacts
from .metrics import LEGACY_METRICS, METRICS, active_for_view, metric_label
from .metrics3d import KNEE_ABDUCTION_SERIES, TRUNK_LEAN_SERIES
from .reference import LEVELS, MetricRef, lookup, pct_in_range
from .skeleton3d import GRAVITY_SOURCES

UNCALIBRATED_NOTICE = "参考区间为未校准的占位初值，本报告仅供自查，不构成教学结论。"

# 质量提示阈值（验证进展 #3 的报告侧缓解）：深蹲相位 2D 压缩放大膝弯误差；
# 人物过小（关键点 bbox < 画面高 20%）时像素噪声富集（其 #2：EPFL 误差 1.8 倍）。
DEEP_SQUAT_KNEE_DEG = 90.0
DEEP_SQUAT_RATIO_WARN = 0.2
SMALL_SUBJECT_RATIO = 0.2
LOW_CONF_FRAME_RATIO_WARN = 0.3


def _verdict(series: np.ndarray, ref: MetricRef) -> str:
    valid = series[~np.isnan(series)]
    if valid.size == 0:
        return "有效数据不足，无法判断"
    median = float(np.median(valid))
    if median < ref.lo:
        return ref.verdict_low or "低于参考区间"
    if median > ref.hi:
        return ref.verdict_high or "高于参考区间"
    return ref.verdict_ok


def _band_percentile(median: float | None, ref: MetricRef) -> float | None:
    """中位在参考带内的百分位位置：0=带下沿，100=带上沿，带外 <0 / >100。

    教练评审（docs/coach_review_simulated.md §4）：落带率受相位混合与帧间
    自相关影响难解读，并列中位数+百分位让用户知道自己在带里的位置。
    """
    if median is None or ref.hi <= ref.lo:
        return None
    return round(100.0 * (median - ref.lo) / (ref.hi - ref.lo), 1)


def _median_or_none(series: np.ndarray | None) -> float | None:
    if series is None:
        return None
    valid = series[~np.isnan(series)]
    if valid.size == 0:
        return None
    return round(float(np.median(valid)), 1)


def _n_valid(series: np.ndarray) -> int:
    return int(np.count_nonzero(~np.isnan(series)))


def _dominant_side(left: float | None, right: float | None) -> str | None:
    if left is None and right is None:
        return None
    if right is None:
        return "left"
    if left is None:
        return "right"
    return "left" if left >= right else "right"


def _build_metrics3d(series: dict[str, np.ndarray], *,
                     gravity_source: str,
                     slope_source: str) -> list[dict[str, object]]:
    """三维六项的报告条目。参考带未建立，条目只给描述统计。

    与二维条目共用 `metrics[]`，靠 `basis` 区分；`reference` / `pct_in_range`
    / `verdict` 全为 null，避免读方把三维数误当带内判断。
    """
    notes = {
        "hip_flexion": "辅助量用同侧肩，主指标用两侧肩中点构成的躯干轴，两者不可互换",
        "knee_flexion": "三维列 0° = 伸直；二维回归列是膝夹角（180° = 伸直），不可直接相减",
        "knee_abduction": "正 = 膝向中线；描述统计，不构成内扣风险判断",
        "hip_shoulder_separation": "参考带建在 |ψ| 上；正 = 左肩向前",
        "thoraco_pelvic_obliquity": "参考带建在 |φ| 上；正 = 左肩高于右肩",
    }
    out: list[dict[str, object]] = []
    for name, definition in METRICS.items():
        item: dict[str, object] = {
            "metric": name,
            "name": definition.label,
            "basis": "3d",
            "definition": definition.definitions.get("3d"),
            "reference": None,
            "pct_in_range": None,
            "band_percentile": None,
            "verdict": None,
            "note": "三维参考带尚未建立（issue 09/12），本项只作描述统计",
        }
        if name == "hip_flexion":
            s = series["hip_flexion"]
            item.update({
                "median_deg": _median_or_none(s),
                "n_valid_frames": _n_valid(s),
                "zero_deg": 180.0,
                "helper": {"hip_flexion_3pt_deg": _median_or_none(series["hip_flexion_3pt"])},
            })
        elif name == "knee_flexion":
            s = series["knee_flexion"]
            item.update({
                "median_deg": _median_or_none(s),
                "n_valid_frames": _n_valid(s),
                "zero_deg": 0.0,
                "helper": {
                    "knee_interior_3pt_deg": _median_or_none(series["knee_interior_3pt"]),
                },
            })
        elif name == "trunk_lean":
            # 分量键由 metrics3d 的分组元组一次性解包：键名或拆并方式变了，
            # 这里立刻报错，而不是各处裸写字面量键悄悄漂移
            sag, frt, tot, slo = (series[k] for k in TRUNK_LEAN_SERIES)
            item.update({
                "median_deg": _median_or_none(sag),
                "n_valid_frames": _n_valid(sag),
                "zero_deg": 0.0,
                "sagittal_deg": _median_or_none(sag),
                "frontal_deg": _median_or_none(frt),
                "total_deg": _median_or_none(tot),
                "slope_normal_deg": _median_or_none(slo),
                "gravity_source": gravity_source,
                "slope_source": slope_source,
            })
            if gravity_source not in GRAVITY_SOURCES:
                item["note"] = "重力来源不可信，躯干前倾不出数"
            elif slope_source == "none":
                item["note"] = f"重力来源 {gravity_source}；未提供坡度，坡面版本为空"
            else:
                item["note"] = f"重力来源 {gravity_source}；坡面版本按现场 IMU 坡度计算"
        elif name == "knee_abduction":
            primary, kleft, kright = (series[k] for k in KNEE_ABDUCTION_SERIES)
            left = _median_or_none(kleft)
            right = _median_or_none(kright)
            item.update({
                "median_deg": _median_or_none(primary),
                "n_valid_frames": _n_valid(primary),
                "zero_deg": 0.0,
                "left_deg": left,
                "right_deg": right,
                "side": _dominant_side(left, right),
            })
        elif name == "hip_shoulder_separation":
            s = series["hip_shoulder_separation"]
            item.update({
                "median_deg": _median_or_none(s),
                "n_valid_frames": _n_valid(s),
                "abs_median_deg": _median_or_none(np.abs(s)),
                "zero_deg": 0.0,
            })
        elif name == "thoraco_pelvic_obliquity":
            s = series["thoraco_pelvic_obliquity"]
            item.update({
                "median_deg": _median_or_none(s),
                "n_valid_frames": _n_valid(s),
                "abs_median_deg": _median_or_none(np.abs(s)),
                "zero_deg": 0.0,
            })
        if name in notes:
            item["note"] = f"{item['note']}；{notes[name]}"
        out.append(item)
    return out


def build_report(
    video: str,
    view_result,
    series: dict[str, np.ndarray],
    refs: dict[str, dict[str, MetricRef]],
    level: str,
    person: dict | None = None,
    calibrated: bool = False,
    quality: dict | None = None,
    series3d: dict[str, np.ndarray] | None = None,
    gravity_source: str = "unknown",
    slope_source: str = "none",
    data_source: str | None = None,
    phases: dict | None = None,
) -> dict:
    if level not in LEVELS:
        raise ValueError(f"未知水平档: {level}，可选 {LEVELS}")

    metrics_out = []
    knee = series.get("knee_flexion")
    if knee is None:
        squat = np.nan
    else:
        valid_knee = knee[~np.isnan(knee)]
        squat = np.nan if valid_knee.size == 0 else float(np.mean(valid_knee < DEEP_SQUAT_KNEE_DEG))
    quality = dict(quality) if quality else {}
    if not np.isnan(squat):
        quality["deep_squat_frame_ratio"] = round(squat, 3)
    notices = [] if calibrated else [UNCALIBRATED_NOTICE]

    # 视角门控：唯一规则在 metrics.active_for_view——整视频单一视角时按
    # 注册表筛；mixed（跟拍混合视角）下 sagittal/frontal 指标已在
    # metrics.compute_series 逐帧门控，两侧都放行。render 叠加层走同一条
    # 规则，两边不可能漂移。
    view = view_result.view
    for name in active_for_view(view):
        s = series[name]
        ref = lookup(refs, name, level)
        p = pct_in_range(s, ref)
        valid = s[~np.isnan(s)]
        item: dict[str, object] = {
            "metric": name,
            "name": metric_label(name),
            "basis": "2d",
            "reference": [ref.lo, ref.hi],
            "median_deg": None if valid.size == 0 else round(float(np.median(valid)), 1),
            "pct_in_range": None if np.isnan(p) else round(p, 3),
            "band_percentile": _band_percentile(
                None if valid.size == 0 else float(np.median(valid)), ref),
            "n_valid_frames": int(valid.size),
            "verdict": _verdict(s, ref),
        }
        definition = (METRICS[name] if name in METRICS else LEGACY_METRICS[name]
                      ).definitions.get("2d")
        if definition:
            item["definition"] = definition
        if name == "knee_abduction":
            item["definition"] = "2d_projection"
            item["note"] = ("二维投影口径（旧 knee_valgus），"
                            "与三维 JCS 膝内外翻不可直接比较")
        if name == "shoulder_hip_align":
            item["legacy"] = True
            item["replaced_by"] = ["hip_shoulder_separation", "thoraco_pelvic_obliquity"]
            item["note"] = "旧合并口径；三维报告拆成横断面 H-SEP 与冠状面 obliquity"
        if name == "knee_flexion" and not np.isnan(squat) and squat >= DEEP_SQUAT_RATIO_WARN:
            item["caveat"] = (f"深蹲相位帧占 {squat*100:.0f}%，2D 投影在此相位放大膝弯误差，"
                              "数值仅供参考")
        metrics_out.append(item)

    if series3d is not None:
        metrics_out.extend(_build_metrics3d(
            series3d, gravity_source=gravity_source, slope_source=slope_source))

    if series3d is None:
        assumptions = [
            "纯 2D 图像平面角度，假设相机大致垂直于动作平面",
            "躯干前倾角以图像竖直方向为零基准，未做相机倾斜校正；"
            "坡面坡度与相机不水平会以表观前倾进入读数",
            "角度约定与 Sports2D 一致（见 reference_values.toml 头部）",
            "指标基准为二维投影（basis=2d）：knee_abduction 是旧 knee_valgus 的"
            "二维口径，与三维 JCS 不可直接比较；shoulder_hip_align 是旧合并口径，"
            "三维报告拆成 H-SEP 与 obliquity（ADR-0001）",
        ]
    else:
        assumptions = [
            "二维列（basis=2d）是图像平面角度，假设相机大致垂直于动作平面",
            "二维列的躯干前倾以图像竖直方向为零基准，未做相机倾斜校正；"
            "坡面坡度与相机不水平会以表观前倾进入读数",
            "二维列的角度约定与 Sports2D 一致（见 reference_values.toml 头部）",
            "二维列的 knee_abduction 是旧 knee_valgus 的投影口径，"
            "与三维 JCS 不可直接比较；shoulder_hip_align 是旧合并口径，"
            "三维报告拆成 H-SEP 与 obliquity（ADR-0001）",
            f"三维列（basis=3d）在 root-relative 骨架上计算，视角门控不适用；"
            f"躯干前倾的重力来源为 {gravity_source}",
            "三维参考带尚未建立（issue 09/12），三维列只作描述统计，不作判断",
        ]

    report = {
        "schema": "skibot.report/0.2",
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "video": str(video),
        "level": level,
        "view": {"detected": view_result.view, "score": round(view_result.score, 2),
                 "n_valid_frames": view_result.n_valid_frames, **view_result.detail},
        "assumptions": assumptions,
        "calibrated": calibrated,
        "notice": " ".join(notices) if notices else None,
        "metrics": metrics_out,
    }
    if data_source is not None:
        report["data_source"] = data_source
    if phases is not None:
        # 可选相位对象（schema 0.2 增量字段）：弯数、fallback、按相位聚合
        # 与口径注记；逐弯明细在 animation.json，报告只带摘要
        report["phases"] = phases
    size_ratio = quality.get("person_height_ratio")
    if size_ratio is not None and size_ratio < SMALL_SUBJECT_RATIO:
        notices.append(f"人物在画面中偏小（约为画面高的 {size_ratio*100:.0f}%），"
                       "关键点误差增大，数值仅供参考")
    if view == "mixed":
        notices.append("跟拍混合视角：侧面指标取自侧面帧、正面指标取自正面帧"
                       "（各自有效帧数见 n_valid_frames），相机转向造成的投影误差无法完全排除")
    if view == "ambiguous":
        notices.append("视角判定不明确，二维指标未输出；已知拍摄视角时"
                       "可显式指定视角重跑")
    low_conf = quality.get("low_conf_frame_ratio")
    if low_conf is not None and low_conf >= LOW_CONF_FRAME_RATIO_WARN:
        notices.append(f"低置信度帧占 {low_conf*100:.0f}%，骨架质量受限，数值仅供参考")
    if quality:
        report["quality"] = quality
    if notices:  # 质量提示在 report 构造后追加，回写 notice
        report["notice"] = " ".join(notices)
    if person is not None:
        report["person"] = person
    return report


def save_json(report: dict, path: str | Path) -> Path:
    """报告原子落盘：并发刷新报告页的请求不会读到半截 JSON。"""
    path = Path(path)
    artifacts.write_text_atomic(path, json.dumps(report, ensure_ascii=False, indent=2))
    return path
