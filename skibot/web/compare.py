"""对比页逻辑：两份报告按相位对比，逐项判可比性（issue 18 的 UI 提前量）。

纯函数、不碰 IO：web.app 读两份 report.json 喂进来，页面上只做展示。
规格明确不做逐弯匹配——对比单位是相位聚合（入弯/控制/出弯中位数），
每个「指标 × 相位」单独判可比，不可比项带原因，不填 0、不静默消失。
"""

from __future__ import annotations

from ..phases import PHASE_LABELS, PHASE_METRICS

# 与 phases.PHASE_METRICS 对应的展示名（键名变更在这里断）
PHASE_METRIC_LABELS = {
    "hip_flexion": "屈髋角（三维）",
    "knee_flexion": "膝弯曲角（三维）",
    "trunk_lean_sagittal": "躯干前倾·矢状（三维）",
}
# 相位聚合里的躯干键与报告 metrics[].metric 不同名
PHASE_TO_REPORT = {"trunk_lean_sagittal": "trunk_lean"}


def _norm_ref(ref) -> list[float] | None:
    if isinstance(ref, (list, tuple)) and len(ref) == 2:
        try:
            return [float(ref[0]), float(ref[1])]
        except (TypeError, ValueError):
            return None
    return None


def _metric_ref(report: dict, metric: str) -> list[float] | None:
    key = PHASE_TO_REPORT.get(metric, metric)
    for m in report.get("metrics", []):
        if m.get("metric") == key and m.get("basis") == "3d":
            return _norm_ref(m.get("reference"))
    return None


def _band_state(v: float, ref: list[float]) -> str:
    if v < ref[0]:
        return "low"
    if v > ref[1]:
        return "high"
    return "ok"


def _interpret(va: float, vb: float, ref_a, ref_b) -> tuple[str, str]:
    """返回 (解释, 参考带标签)。

    三维指标当前没有校准参考带时不冒充好坏判断；等参考带建立后，
    同一个函数会自动给出“进入/离开参考带”的解释。
    """
    if ref_a is None or ref_b is None:
        return "无参考带，不判断好坏", "—"
    if ref_a != ref_b:
        return ("参考带不同，不判断好坏",
                f"A {ref_a[0]:g}–{ref_a[1]:g}° / B {ref_b[0]:g}–{ref_b[1]:g}°")
    ref = ref_a
    sa, sb = _band_state(va, ref), _band_state(vb, ref)
    label = f"参考 {ref[0]:g}–{ref[1]:g}°"
    if sa == sb == "ok":
        return "都在参考带内", label
    if sa == "ok" and sb != "ok":
        return "变差（离开参考带）", label
    if sa != "ok" and sb == "ok":
        return "变好（进入参考带）", label
    if sa == sb == "high":
        return ("仍偏大，但更接近参考带"
                if abs(vb - ref[1]) < abs(va - ref[1]) else "仍偏大"), label
    if sa == sb == "low":
        return ("仍偏小，但更接近参考带"
                if abs(vb - ref[0]) < abs(va - ref[0]) else "仍偏小"), label
    return "方向改变（需结合动作判断）", label


def _phase_value(report: dict, metric: str, phase: str) -> tuple[float | None, str]:
    """一份报告里某指标某相位的聚合中位数；拿不到时返回 (None, 原因)。"""
    phases = report.get("phases")
    if not phases:
        return None, "无相位数据（未开三维或帧率缺失）"
    agg = phases.get("aggregate")
    if agg is None:
        # aggregate None ⟺ 没有合格弯（phases.fallback="no_valid_turns"）
        return None, "未检出合格弯（单目置信门覆盖不足或侧倾幅值低于门槛）"
    cell = agg.get(metric, {}).get(phase, {})
    if cell.get("median_deg") is None:
        return None, "该相位无有效帧"
    return cell["median_deg"], ""


def _notes(a: dict, b: dict) -> list[str]:
    """假设说明：水平档/视角/数据来源差异 + 固定的相机坡度假设。"""
    notes = []
    if a.get("level") != b.get("level"):
        notes.append(f"水平档不同（{a.get('level')} / {b.get('level')}）："
                     "参考带档位不同，差值不含基准差异")
    va = a.get("view", {}).get("detected")
    vb = b.get("view", {}).get("detected")
    if va != vb:
        notes.append(f"视角判定不同（{va} / {vb}）：投影口径不同，对比仅供参考")
    if a.get("data_source") != b.get("data_source"):
        notes.append(f"三维数据来源不同（{a.get('data_source')} / "
                     f"{b.get('data_source')}）：角度口径不可直接比较")
    notes.append("两次拍摄的机位、坡度与雪况差异未测量，差值含拍摄条件差异；"
                 "单目三维未做多机位真值验证（issue 09 未完成）")
    return notes


def compare_reports(a: dict, b: dict) -> dict:
    """两份 report dict → 对比视图模型：相位行 + 全程三维行 + 假设说明。"""
    rows = []
    n_comparable = 0
    for metric in PHASE_METRICS:
        for phase in ("entry", "control", "exit"):
            va, ra = _phase_value(a, metric, phase)
            vb, rb = _phase_value(b, metric, phase)
            row: dict[str, object] = {
                "metric": metric, "metric_label": PHASE_METRIC_LABELS[metric],
                "phase": phase, "phase_label": PHASE_LABELS[phase],
                "a": va, "b": vb,
            }
            ref_a = _metric_ref(a, metric)
            ref_b = _metric_ref(b, metric)
            if va is not None and vb is not None:
                delta = round(vb - va, 1)
                n_comparable += 1
                verdict, ref_label = _interpret(va, vb, ref_a, ref_b)
                row.update({
                    "delta": delta,
                    "direction": "增大" if delta > 0 else
                                 ("减小" if delta < 0 else "持平"),
                    "status": "ok", "reason": "",
                    "verdict": verdict, "ref_label": ref_label,
                })
            else:
                # 原因取先缺数据的一侧；两侧都缺就并列
                reason = ra if va is None else rb
                if va is None and vb is None:
                    reason = f"A：{ra}；B：{rb}"
                row.update({
                    "delta": None, "direction": None,
                    "status": "no_data", "reason": reason,
                    "verdict": "无可比数据", "ref_label": "—",
                })
            rows.append(row)

    # 全程三维六项（basis=3d 的中位数），按 metric 名对齐；缺任一侧记原因
    overall = []
    a3 = {m["metric"]: m for m in a.get("metrics", [])
          if m.get("basis") == "3d"}
    b3 = {m["metric"]: m for m in b.get("metrics", [])
          if m.get("basis") == "3d"}
    for metric in sorted(a3.keys() | b3.keys()):
        ma, mb = a3.get(metric), b3.get(metric)
        va = ma.get("median_deg") if ma else None
        vb = mb.get("median_deg") if mb else None
        row2: dict[str, object] = {
            "metric": metric,
            "metric_label": (ma or mb or {}).get("name", metric),
            "a": va, "b": vb,
        }
        ref_a = _norm_ref((ma or {}).get("reference")) if ma else None
        ref_b = _norm_ref((mb or {}).get("reference")) if mb else None
        if va is not None and vb is not None:
            delta = round(vb - va, 1)
            n_comparable += 1
            verdict, ref_label = _interpret(va, vb, ref_a, ref_b)
            row2.update({
                "delta": delta,
                "direction": "增大" if delta > 0 else
                             ("减小" if delta < 0 else "持平"),
                "status": "ok", "reason": "",
                "verdict": verdict, "ref_label": ref_label,
            })
        else:
            row2.update({
                "delta": None, "direction": None, "status": "no_data",
                "reason": "无三维列（未开三维）" if (ma is None or mb is None)
                          else "无有效帧",
                "verdict": "无可比数据", "ref_label": "—",
            })
        overall.append(row2)

    return {
        "phase_rows": rows,
        "overall_rows": overall,
        "notes": _notes(a, b),
        "interpretation_note": (
            "差值只表示角度变化方向。三维指标目前没有校准参考带，"
            "因此“解释”列不判断动作变好或变差；参考带建立后会自动给出判断。"),
        "n_comparable": n_comparable,
        "a_turns": (a.get("phases") or {}).get("n_turns"),
        "b_turns": (b.get("phases") or {}).get("n_turns"),
    }
