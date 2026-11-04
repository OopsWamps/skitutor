"""三维相位切分：侧倾过零切弯，按 50% 侧倾分入弯/控制/出弯（ADR-0001、spec「相位划分」）。

主信号是相对重力的带符号侧倾，即 `metrics3d` 的 `trunk_lean_frontal`：
正值 = 向受试者左侧倾。一个弯是一次半周期（过零 → 极值 → 过零）。换刃
就是侧倾过零；三个相位以本弯最大侧倾的 50% 为界。

交叉校验用两路信号：
- `lateral`：画面里髋中点的横向轨迹，去线性趋势后看过零点是否与主信号
  的换刃时刻一致；
- `knee`：膝弯周期，看局部极大是否对齐本弯侧倾极值、局部极小是否对齐
  换刃。

置信度是三路边界一致性的平均；低置信弯不进入相位聚合。阈值全部是
TODO_VALIDATE，等 issue 11 的人工标注验收（边界中位 ≤0.25s、p90 ≤0.5s）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# TODO_VALIDATE：以下阈值是规格给定值或未标定的初值，issue 11 用人工标注复核。
MIN_AMPLITUDE_DEG = 6.0          # 半周期幅值下限（换刃零点到本弯峰值）
MIN_DURATION_S = 0.5             # 单弯时长下限
MIN_CONFIDENCE = 0.5             # 低于此值的弯不进相位聚合
CROSS_TOLERANCE_S = 0.5          # 边界一致性容差（issue 11 的 p90 线）
MIN_LATERAL_AMPLITUDE_PX = 0.5   # 横向交叉信号的显著性下限
MIN_KNEE_AMPLITUDE_DEG = 0.5     # 膝弯交叉信号的显著性下限
DETREND_WINDOW_FRAMES = 2        # 去趋势只取主信号过零附近这么多帧
NEAR_ZERO_DEG = 0.5              # 没有足够过零帧时的近零回退阈值

PHASE_NAMES = ("entry", "control", "exit")
PHASE_METRICS = ("hip_flexion", "knee_flexion", "trunk_lean_sagittal")
# 相位的中文显示名：对比页、报告页与回放卡的唯一来源，模板与 JS 不再各抄一份
PHASE_LABELS = {"entry": "入弯", "control": "控制", "exit": "出弯"}


@dataclass(frozen=True)
class PhaseInterval:
    """半开区间 [start_frame, end_frame)，时间由帧号 / fps 得出。"""
    start_frame: int
    end_frame: int
    start_time_s: float
    end_time_s: float

    def to_dict(self) -> dict[str, object]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_time_s": round(self.start_time_s, 3),
            "end_time_s": round(self.end_time_s, 3),
        }


@dataclass(frozen=True)
class Turn:
    """一个通过幅值/时长/置信度检查的弯。"""
    index: int
    start_frame: int
    end_frame: int
    start_time_s: float
    end_time_s: float
    peak_frame: int
    peak_time_s: float
    peak_lean_deg: float
    amplitude_deg: float
    peak_to_peak_deg: float
    duration_s: float
    entry: PhaseInterval
    control: PhaseInterval
    exit: PhaseInterval
    confidence: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "start_time_s": round(self.start_time_s, 3),
            "end_time_s": round(self.end_time_s, 3),
            "peak_frame": self.peak_frame,
            "peak_time_s": round(self.peak_time_s, 3),
            "peak_lean_deg": round(self.peak_lean_deg, 2),
            "amplitude_deg": round(self.amplitude_deg, 2),
            "peak_to_peak_deg": round(self.peak_to_peak_deg, 2),
            "duration_s": round(self.duration_s, 3),
            "entry": self.entry.to_dict(),
            "control": self.control.to_dict(),
            "exit": self.exit.to_dict(),
            "confidence": None if self.confidence is None else round(self.confidence, 3),
        }


@dataclass(frozen=True)
class DiscardedTurn:
    """没通过检查的区间或半周期；reason 用短码，detail 给人看。"""
    start_frame: int
    end_frame: int
    reason: str
    detail: str
    confidence: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "reason": self.reason,
            "detail": self.detail,
            "confidence": None if self.confidence is None else round(self.confidence, 3),
        }


@dataclass
class PhaseSegmentation:
    """一段滑行的相位切分结果。

    turns 只含被接受的弯；discarded 记录首尾不完整、幅值/时长不足、
    置信度不足的区间与原因。没有合格弯时 fallback="no_valid_turns"，
    调用方回退到全程统计并注明。
    """
    turns: list[Turn]
    discarded: list[DiscardedTurn]
    fps: float
    fallback: str | None = None

    @property
    def has_turns(self) -> bool:
        return bool(self.turns)

    def to_dict(self) -> dict[str, object]:
        return {
            "fps": self.fps,
            "fallback": self.fallback,
            "turns": [t.to_dict() for t in self.turns],
            "discarded": [d.to_dict() for d in self.discarded],
        }


def _interval(start: int, end: int, fps: float) -> PhaseInterval:
    return PhaseInterval(start, end, start / fps, end / fps)


def _zero_crossings(x: np.ndarray) -> list[float]:
    """带符号序列的过零点（分数帧位置）。

    零平台按“已知的零”处理：反号两侧之间若夹着零平台，过零取平台中心，
    不再跨平台线性插值；平台之后没有非零样本时，平台第一个零是结束换刃；
    信号从零平台开始时，平台最后一个零是起始换刃。平台之后先把状态推进
    到下一个非零样本，避免同一次符号变化被记两次。
    NaN 跳过；相邻非零样本之间没有零样本时才线性插值。
    """
    n = len(x)
    crossings: list[float] = []
    last_idx: int | None = None
    last_sign = 0
    i = 0
    while i < n:
        value = x[i]
        if not np.isfinite(value):
            i += 1
            continue
        if value == 0.0:
            j = i
            while j < n and (not np.isfinite(x[j]) or x[j] == 0.0):
                j += 1
            center = (i + j - 1) / 2.0
            if last_idx is None:
                # 首部零平台：离开零平台的最后一个零是起始换刃
                if j < n:
                    crossings.append(float(j - 1))
            elif j >= n:
                # 尾部零平台：回到零的第一个零是结束换刃
                crossings.append(float(i))
            else:
                next_sign = 1 if x[j] > 0 else -1
                if next_sign != last_sign:
                    crossings.append(center)
            if j < n:
                last_idx = j
                last_sign = 1 if x[j] > 0 else -1
            i = j
            continue
        sign = 1 if value > 0 else -1
        if last_idx is not None and sign != last_sign:
            a, b = float(x[last_idx]), float(value)
            frac = last_idx + abs(a) / (abs(a) + abs(b)) * (i - last_idx)
            crossings.append(float(frac))
        last_idx = i
        last_sign = sign
        i += 1
    return crossings


def _detrend(x: np.ndarray, reference: np.ndarray | None = None,
             main_crossings: list[float] | None = None) -> np.ndarray:
    """去线性趋势；画面横向轨迹在跟拍镜头里带缓慢漂移。

    有主信号时只在主信号过零附近、且位于首尾完整段之间的帧上拟合漂移，
    避免把长弯自身的低频形状或首尾平段当成漂移减掉。没有足够过零帧时
    退回主信号近零帧，再退回全部有限帧。
    """
    y = np.asarray(x, dtype=float).copy()
    finite = np.isfinite(y)
    fit_mask = finite
    selected = False
    if reference is not None:
        ref = np.asarray(reference, dtype=float)
        crossings = (main_crossings if main_crossings is not None
                     else _zero_crossings(ref))
        if len(crossings) >= 2:
            idx = np.arange(len(y))
            inside = (idx >= crossings[0]) & (idx <= crossings[-1])
            near = np.zeros(len(y), dtype=bool)
            for c in crossings:
                near |= np.abs(idx - c) <= DETREND_WINDOW_FRAMES
            cand = finite & inside & near
            if int(cand.sum()) >= 2:
                fit_mask = cand
                selected = True
        if not selected:
            near_zero = finite & np.isfinite(ref) & (np.abs(ref) < NEAR_ZERO_DEG)
            if int(near_zero.sum()) >= 2:
                fit_mask = near_zero
    if int(fit_mask.sum()) < 2:
        return y
    idx = np.arange(len(y))
    coeff = np.polyfit(idx[fit_mask], y[fit_mask], 1)
    y[finite] -= np.polyval(coeff, idx[finite])
    return y


def _local_extrema(x: np.ndarray) -> tuple[list[int], list[int]]:
    maxima: list[int] = []
    minima: list[int] = []
    for i in range(1, len(x) - 1):
        a, b, c = x[i - 1], x[i], x[i + 1]
        if not (np.isfinite(a) and np.isfinite(b) and np.isfinite(c)):
            continue
        if b >= a and b > c:
            maxima.append(i)
        if b <= a and b < c:
            minima.append(i)
    return maxima, minima


def _nearest_agreement(target: float, features: list[int] | list[float],
                       fps: float, tolerance_s: float) -> float | None:
    if not features:
        return None
    error_s = min(abs(float(f) - target) for f in features) / fps
    return max(0.0, 1.0 - error_s / tolerance_s)


def _confidence(*, start: float, end: float, peak: float,
                side_lean: np.ndarray,
                main_crossings: list[float],
                lateral: np.ndarray | None, knee: np.ndarray | None,
                fps: float, tolerance_s: float) -> float | None:
    """三路边界一致性：横向轨迹的过零点 + 膝弯的极值。

    每一路先过显著性门槛：去趋势后的横向残差或膝弯范围小于阈值时，
    视为没有振荡信号，不产生分数。这样纯漂移、平坦信号或浮点残差
    不会伪造高置信度，也不会把合格弯误判成低置信；两路都无信号时
    返回 None（未知，不误判）。
    """
    scores: list[float] = []
    if lateral is not None:
        lateral_arr = np.asarray(lateral, dtype=float)
        residual = _detrend(lateral_arr, side_lean, main_crossings)
        finite = np.isfinite(residual)
        if finite.any() and (
                float(np.nanmax(residual) - np.nanmin(residual))
                >= MIN_LATERAL_AMPLITUDE_PX):
            crossings = _zero_crossings(residual)
            for target in (start, end):
                score = _nearest_agreement(target, crossings, fps, tolerance_s)
                if score is not None:
                    scores.append(score)
    if knee is not None:
        knee_arr = np.asarray(knee, dtype=float)
        finite = np.isfinite(knee_arr)
        if finite.any() and (
                float(np.nanmax(knee_arr) - np.nanmin(knee_arr))
                >= MIN_KNEE_AMPLITUDE_DEG):
            maxima, minima = _local_extrema(knee_arr)
            for target in (start, end):
                score = _nearest_agreement(target, minima, fps, tolerance_s)
                if score is not None:
                    scores.append(score)
            score = _nearest_agreement(peak, maxima, fps, tolerance_s)
            if score is not None:
                scores.append(score)
    return float(np.mean(scores)) if scores else None


def _turn_phases(lean: np.ndarray, start: int, end: int, peak_frame: int,
                 fps: float) -> tuple[PhaseInterval, PhaseInterval, PhaseInterval]:
    """以本弯最大侧倾的 50% 为界切三段；50% 以上归控制段。"""
    peak_abs = abs(float(lean[peak_frame]))
    threshold = 0.5 * peak_abs
    entry_end = peak_frame
    for i in range(start, peak_frame + 1):
        if abs(float(lean[i])) >= threshold:
            entry_end = i
            break
    exit_start = end
    for i in range(peak_frame + 1, end):
        if abs(float(lean[i])) < threshold:
            exit_start = i
            break
    entry_end = min(max(entry_end, start), end)
    exit_start = min(max(exit_start, entry_end), end)
    return (_interval(start, entry_end, fps),
            _interval(entry_end, exit_start, fps),
            _interval(exit_start, end, fps))


def segment_phases(
    side_lean: np.ndarray,
    *,
    fps: float,
    knee: np.ndarray | None = None,
    lateral: np.ndarray | None = None,
    min_amplitude_deg: float = MIN_AMPLITUDE_DEG,
    min_duration_s: float = MIN_DURATION_S,
    min_confidence: float = MIN_CONFIDENCE,
    cross_tolerance_s: float = CROSS_TOLERANCE_S,
) -> PhaseSegmentation:
    """从带符号侧倾切出弯与三相位。

    side_lean：逐帧带符号侧倾（度），正 = 向受试者左侧倾。
    fps：帧率；单弯时长判据依赖它。
    knee / lateral：可选的交叉校验信号，长度与 side_lean 相同。
    返回的 turns 只含通过幅值 ≥6°、时长 ≥0.5s、置信度 ≥0.5 的弯。
    min_amplitude_deg：半周期幅值下限，从换刃零点到本弯最大侧倾；
    用峰值绝对值判，不用区间 max-min（NaN 缺口会低估后者）。
    `Turn.peak_to_peak_deg` 仍给区间 max-min，只作诊断，不参与筛选。
    """
    lean = np.asarray(side_lean, dtype=float)
    if lean.ndim != 1:
        raise ValueError(f"side_lean 必须是一维，收到 {lean.shape}")
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError(f"fps 必须是正数，收到 {fps}")
    for name, other in (("knee", knee), ("lateral", lateral)):
        if other is not None and np.asarray(other).shape != lean.shape:
            raise ValueError(f"{name} 与 side_lean 长度不同：{np.asarray(other).shape} vs {lean.shape}")

    n = len(lean)
    crossings = _zero_crossings(lean)
    discarded: list[DiscardedTurn] = []
    turns: list[Turn] = []

    if len(crossings) < 2:
        return PhaseSegmentation(
            [], [DiscardedTurn(0, n, "no_crossings", "侧倾没有形成完整半周期")],
            fps, fallback="no_valid_turns")

    # 切片用 ceil：换刃前的样本属于上一侧，不能并进本弯；round 会把
    # 3.48 帧的换刃取成 3，把 -3° 的样本算进正侧峰值。
    head_end = int(np.ceil(crossings[0]))
    if 0 < head_end <= n:
        discarded.append(DiscardedTurn(0, head_end, "incomplete", "首段没有起始换刃"))
    tail_start = int(np.ceil(crossings[-1]))
    if 0 <= tail_start < n:
        discarded.append(DiscardedTurn(tail_start, n, "incomplete", "尾段没有结束换刃"))

    for i in range(len(crossings) - 1):
        start = int(np.ceil(crossings[i]))
        end = int(np.ceil(crossings[i + 1]))
        if end <= start:
            discarded.append(DiscardedTurn(start, max(end, start), "degenerate",
                                           "换刃间隔不足一帧"))
            continue
        seg = lean[start:end]
        finite = np.isfinite(seg)
        if not finite.any():
            discarded.append(DiscardedTurn(start, end, "no_finite_data",
                                           "区间内没有有效侧倾"))
            continue
        peak_rel = int(np.nanargmax(np.abs(seg)))
        peak_frame = start + peak_rel
        peak_lean = float(lean[peak_frame])
        amplitude = abs(peak_lean)
        peak_to_peak = float(np.nanmax(seg) - np.nanmin(seg))
        # 时长用未取整的换刃位置；半帧边界按帧差取整会变成 0.4s。
        duration_s = (crossings[i + 1] - crossings[i]) / fps
        confidence = _confidence(
            start=crossings[i], end=crossings[i + 1], peak=float(peak_frame),
            side_lean=lean, main_crossings=crossings,
            lateral=None if lateral is None else np.asarray(lateral, dtype=float),
            knee=None if knee is None else np.asarray(knee, dtype=float),
            fps=fps, tolerance_s=cross_tolerance_s)

        if amplitude < min_amplitude_deg:
            discarded.append(DiscardedTurn(
                start, end, "low_amplitude",
                f"幅值 {amplitude:.1f}° < {min_amplitude_deg:.1f}°",
                confidence))
            continue
        if duration_s < min_duration_s:
            discarded.append(DiscardedTurn(
                start, end, "too_short",
                f"时长 {duration_s:.2f}s < {min_duration_s:.2f}s", confidence))
            continue
        if confidence is not None and confidence < min_confidence:
            discarded.append(DiscardedTurn(
                start, end, "low_confidence",
                f"三信号边界一致性 {confidence:.2f} < {min_confidence:.2f}",
                confidence))
            continue

        entry, control, exit_ = _turn_phases(lean, start, end, peak_frame, fps)
        turns.append(Turn(
            index=len(turns), start_frame=start, end_frame=end,
            start_time_s=crossings[i] / fps, end_time_s=crossings[i + 1] / fps,
            peak_frame=peak_frame, peak_time_s=peak_frame / fps,
            peak_lean_deg=peak_lean, amplitude_deg=amplitude,
            peak_to_peak_deg=peak_to_peak,
            duration_s=duration_s, entry=entry, control=control, exit=exit_,
            confidence=confidence))

    fallback = None if turns else "no_valid_turns"
    return PhaseSegmentation(turns, discarded, fps, fallback=fallback)


def phase_labels(seg: PhaseSegmentation, n_frames: int) -> np.ndarray:
    """逐帧相位标签：entry/control/exit，未进合格弯的帧为空串。"""
    labels = np.full(n_frames, "", dtype="<U7")
    for turn in seg.turns:
        for name in PHASE_NAMES:
            interval = getattr(turn, name)
            labels[interval.start_frame:interval.end_frame] = name
    return labels


def aggregate_phases(
    series: dict[str, np.ndarray],
    seg: PhaseSegmentation,
    *,
    metrics: tuple[str, ...] = PHASE_METRICS,
) -> dict[str, dict[str, dict[str, float | int | None]]] | None:
    """按相位聚合矢状三项；没有合格弯时返回 None，调用方回退全程值。

    只使用 seg.turns（已排除低置信弯），所以低置信弯的帧不会进入聚合。
    每个相位返回中位数与有效帧数；全 NaN 时中位数为 None，不写 0。
    """
    if not seg.turns:
        return None
    out: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for metric in metrics:
        values = np.asarray(series[metric], dtype=float)
        out[metric] = {}
        for phase in PHASE_NAMES:
            chunks = []
            for turn in seg.turns:
                interval = getattr(turn, phase)
                chunks.append(values[interval.start_frame:interval.end_frame])
            arr = np.concatenate(chunks) if chunks else np.array([])
            valid = arr[~np.isnan(arr)]
            out[metric][phase] = {
                "median_deg": None if valid.size == 0 else float(np.median(valid)),
                "n_frames": int(valid.size),
            }
    return out
