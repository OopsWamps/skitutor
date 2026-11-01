"""相位标注对比脚本的纯逻辑测试：python tests/test_phase_annotation.py

覆盖标注→参考相位推导（含人工控制段覆盖）、自动→逐帧标签、
贪心最近配对的容量与去重。真实素材的端到端对比走 scripts。
"""

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from check_phase_annotation import (  # noqa: E402
    _labels_from_annotation, _labels_from_auto, _match_ordered)
from skibot.phases import PhaseInterval, Turn  # noqa: E402


def test_labels_from_annotation_quadrant_rule():
    ann = {"fps": 25.0, "marks": [
        {"frame": 100, "kind": "edge"},
        {"frame": 140, "kind": "apex"},   # 弯长 100，apex 偏中
        {"frame": 200, "kind": "edge"},
        {"frame": 240, "kind": "apex"},
        {"frame": 300, "kind": "edge"},
    ]}
    labels = _labels_from_annotation(ann, 400)
    # 弯 0：e=[100,200)，L=100，控制 = apex±25 = [115,165)
    assert (labels[100:115] == 0).all()    # 入弯
    assert (labels[115:165] == 1).all()    # 控制
    assert (labels[165:200] == 2).all()    # 出弯
    # 弯 1：e=[200,300)，控制 = [215,265)
    assert (labels[200:215] == 0).all()
    assert (labels[215:265] == 1).all()
    assert (labels[265:300] == 2).all()
    assert (labels[:100] == -1).all() and (labels[300:] == -1).all()
    # 没有顶点标注时 apex 回退弯中点
    ann2 = {"fps": 25.0, "marks": [
        {"frame": 100, "kind": "edge"}, {"frame": 200, "kind": "edge"}]}
    labels2 = _labels_from_annotation(ann2, 300)
    assert (labels2[100:125] == 0).all() and (labels2[125:175] == 1).all()
    assert (labels2[175:200] == 2).all()
    print("  标注→参考相位（四分位规则与 apex 回退）✓")


def test_labels_annotation_control_override():
    ann = {"fps": 25.0, "marks": [
        {"frame": 100, "kind": "edge"}, {"frame": 150, "kind": "apex"},
        {"frame": 200, "kind": "edge"}],
        "control": {"0": {"start": 130, "end": 180}}}
    labels = _labels_from_annotation(ann, 250)
    assert (labels[100:130] == 0).all()
    assert (labels[130:180] == 1).all()   # 人工控制段覆盖四分位默认
    assert (labels[180:200] == 2).all()
    print("  人工控制段边界覆盖 ✓")


def _fake_turn(start, peak, end):
    """entry=[start, peak-10) control=[peak-10,peak+10) exit=[peak+10,end)。"""
    return Turn(index=0, start_frame=start, end_frame=end,
                start_time_s=start / 25.0, end_time_s=end / 25.0,
                peak_frame=peak, peak_time_s=peak / 25.0,
                peak_lean_deg=10.0, amplitude_deg=8.0, peak_to_peak_deg=8.0,
                duration_s=(end - start) / 25.0,
                entry=PhaseInterval(start, peak - 10, start / 25.0,
                                    (peak - 10) / 25.0),
                control=PhaseInterval(peak - 10, peak + 10, (peak - 10) / 25.0,
                                      (peak + 10) / 25.0),
                exit=PhaseInterval(peak + 10, end, (peak + 10) / 25.0,
                                   end / 25.0),
                confidence=0.9)


def test_labels_from_auto():
    class Seg:
        turns = [_fake_turn(100, 150, 200), _fake_turn(200, 260, 300)]
    labels = _labels_from_auto(Seg(), 400)
    assert (labels[100:140] == 0).all() and (labels[140:160] == 1).all()
    assert (labels[160:200] == 2).all()
    assert (labels[200:250] == 0).all() and (labels[250:270] == 1).all()
    assert (labels[270:300] == 2).all()
    assert (labels[:100] == -1).all()
    print("  自动切分→逐帧标签 ✓")


def test_match_ordered():
    def vals(a, b, cap):
        return [(a[i], b[j]) for i, j in _match_ordered(a, b, cap)]

    # 保序：不会交叉配对（顺序贪心在此例会产生 (114,115),(118,105)）
    pairs = vals([114.0, 118.0], [105.0, 115.0], 12.0)
    assert (114.0, 105.0) in pairs and (118.0, 115.0) in pairs
    # 最大基数：贪心会丢对，DP 拿满 2 对
    assert len(vals([109.0, 122.0], [100.0, 116.0], 18.75)) == 2
    # cap 拒绝与空解
    assert vals([150.0], [100.0, 130.0], 10.0) == []
    assert vals([], [100.0], 10.0) == []
    # 最小总差：110 与 [105,116] 都可配时取更近者，200 配 205
    pairs = vals([110.0, 200.0], [105.0, 116.0, 205.0], 10.0)
    assert (110.0, 105.0) in pairs and (200.0, 205.0) in pairs
    print("  保序 DP 配对（不交叉、满基数、cap）✓")


if __name__ == "__main__":
    test_labels_from_annotation_quadrant_rule()
    test_labels_annotation_control_override()
    test_labels_from_auto()
    test_match_ordered()
    print("全部通过")
