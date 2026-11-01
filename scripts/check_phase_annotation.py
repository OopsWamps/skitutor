"""人工/预标注相位 vs 自动切分的对比验收（issue 11）。

读取标注 JSON（annotate_server 的人工标注或 preannotate_phases 的银标准），
对同素材跑生产自动切分（MotionBERT 提升 + segment_phases），产出：

- 换刃边界误差（匹配对的中位 / p90，秒）
- 每段弯数误差
- 相位帧错分率（双方都有定义的帧上，三相位标签不一致的占比）

参考相位推导规则（写死，供复现）：标注换刃 e_i 与顶点 a_i 之间为一弯，
L_i = e_{i+1} − e_i；控制段 = [a_i − 0.25·L_i, a_i + 0.25·L_i]（弯中段），
入弯 = (e_i, 控制起点)，出弯 = (控制终点, e_{i+1})。标注文件里另存
control_start/control_end 帧可覆盖该默认。

用法：
    python scripts/check_phase_annotation.py --manifest <manifest.json>
        [--annotations-dir DIR] [--match-s 0.75]

manifest 是 phase_annotation_manifest.json；标注 JSON 从 --annotations-dir
按 <name>_phase_annotation.json 取（人工标注或预标注均可，报告须注明来源）。
输出对比表与通过/不通过，明细落 phase_annotation_check.json。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from skibot.metrics3d import compute_series3d  # noqa: E402
from skibot.motionbert import MotionBERTLifter  # noqa: E402
from skibot.phases import segment_phases  # noqa: E402
from skibot.render import probe_video  # noqa: E402
from skibot.skeleton import CONF_MIN, L_HIP, R_HIP  # noqa: E402
from skibot.trc_io import load_kp  # noqa: E402

EDGE_MATCH_S = 0.75      # 换刃配对的最大距离（秒）
PHASE_LABELS = ("入弯", "控制", "出弯")


def auto_segment(trc: Path, video: Path, lifter: MotionBERTLifter):
    """生产自动切分：MotionBERT 提升 + segment_phases（与 lit_anchor 同链）。"""
    _, _, fps = probe_video(video)
    if not fps:
        raise ValueError(f"{video} 读不出帧率")
    kp = load_kp(trc)
    sk3 = lifter.lift(kp)
    series3d = compute_series3d(sk3)
    left = kp[:, L_HIP]
    right = kp[:, R_HIP]
    ok = (left[:, 2] >= CONF_MIN) & (right[:, 2] >= CONF_MIN)
    lateral = np.where(ok, (left[:, 0] + right[:, 0]) / 2.0, np.nan)
    seg = segment_phases(series3d["trunk_lean_frontal"], fps=fps,
                         knee=series3d["knee_flexion"], lateral=lateral)
    return seg, float(fps), kp.shape[0]


def _labels_from_annotation(ann: dict, n_frames: int) -> np.ndarray:
    """标注 → 逐帧三相位标签（0/1/2），未覆盖帧为 -1。

    退化输入（重复边帧、控制段倒置/越界）跳过该弯或截取到弯内，不静默
    改写相邻帧语义；倒置控制段直接抛错——标注文件错了要修标注。
    """
    edges = sorted(m["frame"] for m in ann["marks"] if m["kind"] == "edge")
    apexes = {m["frame"] for m in ann["marks"] if m["kind"] == "apex"}
    labels = np.full(n_frames, -1, dtype=int)
    for i in range(len(edges) - 1):
        e0, e1 = edges[i], edges[i + 1]
        if e1 <= e0:  # 重复边帧：零长弯跳过
            continue
        cands = [f for f in apexes if e0 < f < e1]
        apex = min(cands, key=lambda f: abs(f - (e0 + e1) / 2)) if cands \
            else (e0 + e1) // 2
        cs = ann.get("control", {}).get(str(i), {}).get("start")
        ce = ann.get("control", {}).get(str(i), {}).get("end")
        length = e1 - e0
        c_start = int(cs) if cs is not None else int(apex - 0.25 * length)
        c_end = int(ce) if ce is not None else int(apex + 0.25 * length)
        if c_start >= c_end:
            raise ValueError(f"标注弯 {i}（{e0}→{e1}）的控制段倒置："
                             f"{c_start} ≥ {c_end}，请修正标注")
        c_start = max(e0, min(c_start, e1))
        c_end = max(e0, min(c_end, e1))
        labels[e0:c_start] = 0
        labels[c_start:c_end] = 1
        labels[c_end:e1] = 2
    return labels


def _labels_from_auto(seg, n_frames: int) -> np.ndarray:
    labels = np.full(n_frames, -1, dtype=int)
    for t in seg.turns:
        labels[t.entry.start_frame:t.entry.end_frame] = 0
        labels[t.control.start_frame:t.control.end_frame] = 1
        labels[t.exit.start_frame:t.exit.end_frame] = 2
    return labels


def _match_ordered(a: list[float], b: list[float],
                   cap: float) -> list[tuple[int, int]]:
    """保序最大配对（次优总 |Δ|）：两条升序序列，|Δ|≤cap 才可配。

    顺序贪心在弯数差异大时会交叉配对/丢基数（相邻标注边间距 ≲2·cap 时），
    DP 的 O(A·M) 在百级规模可忽略。
    """
    na, nb = len(a), len(b)
    # dp[i][j] = (配对数, -总差) 用前 i 个 a 与前 j 个 b 的最优解
    dp = [[(0, 0.0)] * (nb + 1) for _ in range(na + 1)]
    for i in range(1, na + 1):
        for j in range(1, nb + 1):
            best = max(dp[i - 1][j], dp[i][j - 1])
            d = abs(a[i - 1] - b[j - 1])
            if d <= cap:
                c, cost = dp[i - 1][j - 1]
                cand = (c + 1, cost - d)  # cost 存负数，取 max 即最小总差
                if cand > best:
                    best = cand
            dp[i][j] = best
    # 回溯：在每个格子复算配对候选是否就是该格的最优值（浮点按存储值
    # 精确比较——候选由同一算术路径生成）
    pairs: list[tuple[int, int]] = []
    i, j = na, nb
    while i > 0 and j > 0:
        d = abs(a[i - 1] - b[j - 1])
        c, cost = dp[i - 1][j - 1]
        cand = (c + 1, cost - d) if d <= cap else None
        if cand is not None and dp[i][j] == cand:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i][j] == dp[i - 1][j]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs


def compare_clip(ann: dict, trc: Path, video: Path,
                 lifter: MotionBERTLifter, match_s: float = EDGE_MATCH_S) -> dict:
    seg, fps, n_frames = auto_segment(trc, video, lifter)
    marks = sorted(ann["marks"], key=lambda m: m["frame"])
    ann_edges = [m["frame"] for m in marks if m["kind"] == "edge"]
    ann_apexes = [m["frame"] for m in marks if m["kind"] == "apex"]
    auto_edges = [t.start_frame for t in seg.turns]

    # 保序最大配对（cap 内），消除顺序贪心的交叉对与基数损失
    pairs_idx = _match_ordered(auto_edges, ann_edges, match_s * fps)
    pairs = [(auto_edges[i], ann_edges[j]) for i, j in pairs_idx]
    edge_err_s = sorted(abs(a - b) / fps for a, b in pairs)

    # 顶点误差：标注换刃 k 与 k+1 之间的标注顶点 vs 同弯 auto 的 peak_frame
    ann_apex_of: dict[int, int] = {}
    for k, a in enumerate(ann_edges):
        if k + 1 >= len(ann_edges):
            continue
        e0, e1 = a, ann_edges[k + 1]
        cands = [p for p in ann_apexes if e0 < p < e1]
        if cands:
            ann_apex_of[a] = min(cands, key=lambda f: abs(f - (e0 + e1) / 2))
    apex_err_s = []
    for ae, a in pairs:
        apex = ann_apex_of.get(a)
        if apex is None:
            continue
        t = next(t for t in seg.turns if t.start_frame == ae)
        apex_err_s.append(abs(t.peak_frame - apex) / fps)

    n_ann_turns = max(0, len(ann_edges) - 1)
    ref_labels = _labels_from_annotation(
        {"fps": fps, "marks": marks, "control": ann.get("control", {})}, n_frames)
    auto_labels = _labels_from_auto(seg, n_frames)
    both = (ref_labels >= 0) & (auto_labels >= 0)
    n_both = int(both.sum())
    n_wrong = int((ref_labels[both] != auto_labels[both]).sum())

    # 定义底线：把自动自己的边/顶点喂进同一套参考相位规则再与自动标签比，
    # 得到纯规则差的错分率（正弦波形上约 17%，与切分质量无关）。验收门
    # 用「超出底线的量」，原始值同时保留。
    floor = None
    excess = None
    if seg.turns:
        auto_marks = ([{"frame": t.start_frame, "kind": "edge"}
                       for t in seg.turns]
                      + [{"frame": t.peak_frame, "kind": "apex"}
                         for t in seg.turns])
        self_labels = _labels_from_annotation(
            {"fps": fps, "marks": sorted(auto_marks, key=lambda m: m["frame"])},
            n_frames)
        fl_both = (self_labels >= 0) & (auto_labels >= 0)
        if fl_both.sum():
            fl_wrong = int((self_labels[fl_both] != auto_labels[fl_both]).sum())
            floor = round(fl_wrong / int(fl_both.sum()), 4)
            raw = n_wrong / n_both if n_both else None
            if raw is not None:
                excess = round(max(0.0, raw - floor), 4)

    return {
        "clip": trc.parent.name.split("_Sports2D")[0],
        "annotator": ann.get("annotator", "unknown"),
        "n_ann_edges": len(ann_edges), "n_auto_turns": len(seg.turns),
        "n_matched": len(pairs),
        "n_unmatched_ann": len(ann_edges) - len(pairs),
        "n_unmatched_auto": len(auto_edges) - len(pairs),
        "count_error": abs(n_ann_turns - len(seg.turns)),
        "edge_err_median_s": (round(float(np.median(edge_err_s)), 3)
                              if edge_err_s else None),
        "edge_err_p90_s": (round(float(np.percentile(edge_err_s, 90)), 3)
                           if edge_err_s else None),
        "apex_err_median_s": (round(float(np.median(apex_err_s)), 3)
                              if apex_err_s else None),
        "phase_frames_both": n_both,
        "phase_misclass": (round(n_wrong / n_both, 4) if n_both else None),
        "phase_misclass_floor": floor,
        "phase_misclass_excess": excess,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="标注 vs 自动切分对比验收")
    ap.add_argument("--manifest", type=Path, required=True,
                    help="素材清单（phase_annotation_manifest.json）")
    ap.add_argument("--annotations-dir", type=Path,
                    default=Path("datasets/recreational_ski/annotations"),
                    help="标注 JSON 目录（<name>_phase_annotation.json）")
    ap.add_argument("--match-s", type=float, default=EDGE_MATCH_S,
                    help=f"换刃配对最大距离秒（默认 {EDGE_MATCH_S}）")
    ap.add_argument("--out", type=Path,
                    default=Path("datasets/recreational_ski/phase_annotation_check.json"))
    args = ap.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    lifter = MotionBERTLifter()
    results = []
    for clip in manifest["clips"]:
        name = clip["name"]
        ann_path = args.annotations_dir / f"{name}_phase_annotation.json"
        if not ann_path.exists():
            print(f"[skibot] {name}: 无标注（{ann_path.name}），跳过", file=sys.stderr)
            continue
        ann = json.loads(ann_path.read_text(encoding="utf-8"))
        r = compare_clip(ann, Path(clip["trc"]), Path(clip["video"]),
                         lifter, match_s=args.match_s)
        r["annotation"] = str(ann_path)
        r["form"] = clip.get("form")
        results.append(r)
        print(f"[skibot] {name}（{clip.get('form')}，标注来源 {r['annotator']}）："
              f"标注换刃 {r['n_ann_edges']}，自动弯 {r['n_auto_turns']}，"
              f"配对 {r['n_matched']}；换刃误差中位 {r['edge_err_median_s']}s / "
              f"p90 {r['edge_err_p90_s']}s；顶点中位 {r['apex_err_median_s']}s；"
              f"弯数误差 {r['count_error']}；相位错分率 {r['phase_misclass']}"
              f"（底线 {r['phase_misclass_floor']}，超出 "
              f"{r['phase_misclass_excess']}；{r['phase_frames_both']} 帧）")

    if not results:
        print("[skibot] 没有任何可对比的标注", file=sys.stderr)
        return 1

    # 各门只统计可评估素材；None（无可配对/无覆盖帧）不是失败，是缺数据
    def _evaluable(key: str) -> list[dict]:
        return [r for r in results if r.get(key) is not None]

    MIN_EVALUABLE = 6  # 可评估段少于该数时只报「数据不足」
    med = _evaluable("edge_err_median_s")
    p90 = _evaluable("edge_err_p90_s")
    mis = _evaluable("phase_misclass_excess")
    gate = {
        "边界中位<=0.25s": (all(r["edge_err_median_s"] <= 0.25 for r in med),
                            len(med)),
        "边界p90<=0.5s": (all(r["edge_err_p90_s"] <= 0.5 for r in p90), len(p90)),
        "弯数误差<=1": (all(r["count_error"] <= 1 for r in results), len(results)),
        "相位错分率超底线<=10%": (all(r["phase_misclass_excess"] <= 0.10
                                      for r in mis), len(mis)),
    }
    for k, (v, n_eval) in gate.items():
        status = "✓" if v else "✗"
        print(f"[skibot] {k}: {status}（可评估 {n_eval}/{len(results)} 段）")
    enough = all(n >= MIN_EVALUABLE for _, n in gate.values())
    all_pass = all(v for v, _ in gate.values())
    if not enough:
        overall = "数据不足（可评估段过少）"
    elif all_pass:
        overall = "通过"
    else:
        overall = "不通过"
    print(f"[skibot] 总体判定：{overall}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"clips": results,
                                    "gates": {k: {"pass": bool(v), "n_evaluable": n}
                                              for k, (v, n) in gate.items()},
                                    "overall": overall, "match_s": args.match_s},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[skibot] 明细已写出：{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
