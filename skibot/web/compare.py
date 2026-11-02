"""对比页逻辑：两份报告按相位对比，逐项判可比性（issue 18 的 UI 提前量）。

纯函数、不碰 IO：web.app 读两份 report.json 喂进来，页面上只做展示。
规格明确不做逐弯匹配——对比单位是相位聚合（入弯/控制/出弯中位数），
每个「指标 × 相位」单独判可比，不可比项带原因，不填 0、不静默消失。
"""

from __future__ import annotations

from ..phases import PHASE_METRICS

PHASE_LABELS = {"entry": "入弯", "control": "控制", "exit": "出弯"}
# 与 phases.PHASE_METRICS 对应的展示名（键名变更在这里断）
PHASE_METRIC_LABELS = {
    "hip_flexion": "屈髋角（三维）",
    "knee_flexion": "膝弯曲角（三维）",
    "trunk_lean_sagittal": "躯干前倾·矢状（三维）",
}


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
            if va is not None and vb is not None:
                delta = round(vb - va, 1)
                n_comparable += 1
                row.update({
                    "delta": delta,
                    "direction": "增大" if delta > 0 else
                                 ("减小" if delta < 0 else "持平"),
                    "status": "ok", "reason": "",
                })
            else:
                # 原因取先缺数据的一侧；两侧都缺就并列
                reason = ra if va is None else rb
                if va is None and vb is None:
                    reason = f"A：{ra}；B：{rb}"
                row.update({
                    "delta": None, "direction": None,
                    "status": "no_data", "reason": reason,
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
        if va is not None and vb is not None:
            delta = round(vb - va, 1)
            n_comparable += 1
            row2.update({
                "delta": delta,
                "direction": "增大" if delta > 0 else
                             ("减小" if delta < 0 else "持平"),
                "status": "ok", "reason": "",
            })
        else:
            row2.update({
                "delta": None, "direction": None, "status": "no_data",
                "reason": "无三维列（未开三维）" if (ma is None or mb is None)
                          else "无有效帧",
            })
        overall.append(row2)

    return {
        "phase_rows": rows,
        "overall_rows": overall,
        "notes": _notes(a, b),
        "n_comparable": n_comparable,
        "a_turns": (a.get("phases") or {}).get("n_turns"),
        "b_turns": (b.get("phases") or {}).get("n_turns"),
    }
