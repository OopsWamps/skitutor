"""对比页逻辑测试：python tests/test_compare.py

compare_reports 是纯函数：按相位对比、逐项判可比性、假设说明。
真实端到端渲染走 tests/test_web.py。
"""

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skibot.web.compare import compare_reports  # noqa: E402


def _rep(*, phases=None, metrics=(), level="intermediate",
         view="sagittal", source=None):
    return {
        "level": level,
        "view": {"detected": view},
        "data_source": source,
        "phases": phases,
        "metrics": list(metrics),
    }


def _agg(hip=None, knee=None):
    """aggregate 只填给定指标；None 单元格 = 无有效帧。"""
    out = {}
    for metric, vals in (("hip_flexion", hip), ("knee_flexion", knee)):
        if vals is None:
            continue
        out[metric] = {ph: {"median_deg": v, "n_frames": 10 if v is not None else 0}
                       for ph, v in zip(("entry", "control", "exit"), vals)}
    return {"n_turns": 4, "fallback": None, "aggregate": out or None}


def test_no_phases_both_sides():
    r = compare_reports(_rep(), _rep())
    rows = r["phase_rows"]
    assert len(rows) == 9  # 3 指标 × 3 相位
    assert all(row["status"] == "no_data" for row in rows)
    assert all("无相位数据" in row["reason"] for row in rows)
    assert r["n_comparable"] == 0
    print("  对比：两侧都没有相位 → 全部 no_data 带原因 ✓")


def test_one_side_missing():
    a = _rep(phases=_agg(hip=(140.0, 150.0, 160.0)))
    b = _rep(phases={"n_turns": 0, "fallback": "no_valid_turns", "aggregate": None})
    r = compare_reports(a, b)
    row = next(x for x in r["phase_rows"] if x["metric"] == "hip_flexion"
               and x["phase"] == "entry")
    assert row["status"] == "no_data" and "未检出合格弯" in row["reason"]
    print("  对比：一侧未检出合格弯 → 原因落到该侧 ✓")


def test_comparable_delta_and_direction():
    a = _rep(phases=_agg(hip=(140.0, 150.0, 160.0), knee=(100.0, None, 90.0)))
    b = _rep(phases=_agg(hip=(145.5, 150.0, 152.0), knee=(105.0, 95.0, 90.0)))
    r = compare_reports(a, b)
    by = {(x["metric"], x["phase"]): x for x in r["phase_rows"]}
    assert by[("hip_flexion", "entry")]["delta"] == 5.5
    assert by[("hip_flexion", "entry")]["direction"] == "增大"
    assert by[("hip_flexion", "control")]["delta"] == 0.0
    assert by[("hip_flexion", "control")]["direction"] == "持平"
    assert by[("hip_flexion", "exit")]["direction"] == "减小"
    # A 侧控制段无有效帧 → 该项不可比，原因是缺数据一侧的「无有效帧」
    row = by[("knee_flexion", "control")]
    assert row["status"] == "no_data" and "无有效帧" in row["reason"]
    assert r["n_comparable"] == 5
    print("  对比：差值方向与逐项缺数据原因 ✓")


def test_overall_and_notes():
    m3 = lambda v: {"metric": "hip_flexion", "name": "屈髋角", "basis": "3d",
                    "median_deg": v}  # noqa: E731
    a = _rep(phases=_agg(hip=(140.0, 150.0, 160.0)), metrics=[m3(170.0)],
             level="novice", view="sagittal", source="motionbert")
    b = _rep(phases=_agg(hip=(140.0, 150.0, 160.0)), metrics=[m3(165.0)],
             level="advanced", view="frontal")
    r = compare_reports(a, b)
    ov = {x["metric"]: x for x in r["overall_rows"]}
    assert ov["hip_flexion"]["delta"] == -5.0
    assert ov["hip_flexion"]["status"] == "ok"
    notes = " ".join(r["notes"])
    assert "水平档不同" in notes and "视角判定不同" in notes
    assert "三维数据来源不同" in notes and "未做多机位真值验证" in notes
    print("  对比：全程三维行 + 假设说明 ✓")


def test_overall_missing_side():
    m3 = {"metric": "knee_flexion", "name": "膝弯曲角", "basis": "3d",
          "median_deg": 100.0}
    r = compare_reports(_rep(metrics=[m3]), _rep())
    ov = r["overall_rows"][0]
    assert ov["status"] == "no_data" and "无三维列" in ov["reason"]
    print("  对比：一侧无三维列 → 全程行不可比 ✓")


if __name__ == "__main__":
    test_no_phases_both_sides()
    test_one_side_missing()
    test_comparable_delta_and_direction()
    test_overall_and_notes()
    test_overall_missing_side()
    print("全部通过")
