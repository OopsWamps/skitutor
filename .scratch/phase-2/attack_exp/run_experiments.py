"""issue 11 排查归因的攻击实验。四条攻击线各出一组数据。

运行：.venv/Scripts/python.exe .scratch/phase-2/attack_exp/run_experiments.py [all|thr|silver|mis|pair]
提升结果缓存在 cache/<name>.npz，第二次跑只重算切分与统计。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np

from skibot.phases import segment_phases
from skibot.skeleton import CONF_MIN, L_HIP, R_HIP
from skibot.trc_io import load_kp

CACHE = Path(__file__).resolve().parent / "cache"
MANIFEST = json.loads((ROOT / "datasets/recreational_ski/phase_annotation_manifest.json")
                      .read_text(encoding="utf-8"))
ANN_DIR = ROOT / "datasets/recreational_ski/annotations"


# ---------- 缓存生产链信号（lean/knee/lateral），与 check_phase_annotation.auto_segment 一致 ----------

def series_for(clip: dict) -> dict:
    name = clip["name"]
    f = CACHE / f"{name}.npz"
    if f.exists():
        d = np.load(f)
        return {k: d[k] for k in d.files}
    from skibot.metrics3d import compute_series3d
    from skibot.motionbert import MotionBERTLifter
    from skibot.render import probe_video
    lifter = MotionBERTLifter()
    _, _, fps = probe_video(Path(clip["video"]))
    kp = load_kp(Path(clip["trc"]))
    sk3 = lifter.lift(kp)
    s3 = compute_series3d(sk3)
    left, right = kp[:, L_HIP], kp[:, R_HIP]
    ok = (left[:, 2] >= CONF_MIN) & (right[:, 2] >= CONF_MIN)
    lateral = np.where(ok, (left[:, 0] + right[:, 0]) / 2.0, np.nan)
    lean = np.asarray(s3["trunk_lean_frontal"], float)
    knee = np.asarray(s3["knee_flexion"], float)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez(f, lean=lean, knee=knee, lateral=lateral, fps=np.array([fps]))
    return {"lean": lean, "knee": knee, "lateral": lateral, "fps": np.array([fps])}


def discarded_counts(seg) -> dict:
    out = {}
    for d in seg.discarded:
        out[d.reason] = out.get(d.reason, 0) + 1
    return out


def run_threshold_attr() -> list:
    """攻击线1：单门放宽与全关的检出对比。"""
    rows = []
    for clip in MANIFEST["clips"]:
        d = series_for(clip)
        fps = float(d["fps"][0])
        base = dict(fps=fps, knee=d["knee"], lateral=d["lateral"])
        variants = {
            "default_6_0.5_0.5": dict(min_amplitude_deg=6.0, min_duration_s=0.5, min_confidence=0.5),
            "conf0": dict(min_amplitude_deg=6.0, min_duration_s=0.5, min_confidence=0.0),
            "dur0": dict(min_amplitude_deg=6.0, min_duration_s=0.0, min_confidence=0.5),
            "amp3": dict(min_amplitude_deg=3.0, min_duration_s=0.5, min_confidence=0.5),
            "conf0_dur0": dict(min_amplitude_deg=6.0, min_duration_s=0.0, min_confidence=0.0),
            "alloff": dict(min_amplitude_deg=0.0, min_duration_s=0.0, min_confidence=0.0),
        }
        row = {"clip": clip["name"], "fps": fps}
        for vn, vp in variants.items():
            seg = segment_phases(d["lean"], **base, **vp)
            row[vn] = len(seg.turns)
            if vn == "default_6_0.5_0.5":
                row["discarded_at_default"] = discarded_counts(seg)
        rows.append(row)
        print("[thr] " + clip["name"] + ": " + json.dumps(row, ensure_ascii=False))
    return rows


# ---------- 攻击线2：银标准形态学 ----------

def silver_signals(trc: Path):
    import preannotate_phases as pp
    kp = load_kp(trc)
    lateral_raw = pp._hip_lateral(kp)
    t = np.arange(len(lateral_raw))
    fin = np.isfinite(lateral_raw)
    x = np.interp(t, t[fin], lateral_raw[fin])
    knee = pp._knee_angles_2d(kp)
    return x, knee


def window_detrend(x: np.ndarray, win: int) -> np.ndarray:
    """中心滑动均值去趋势（NaN 已插值）。"""
    pad = np.pad(x, win // 2, mode="edge")
    kernel = np.ones(win) / win
    sm = np.convolve(pad, kernel, mode="same")
    return x - sm[win // 2:win // 2 + len(x)]


def edge_pipeline(x_dt: np.ndarray, knee: np.ndarray, fps: float) -> dict:
    """复刻 preannotate 的膝过滤 + 最小间隔过滤，返回各阶段数量。"""
    import preannotate_phases as pp
    fin_k = np.isfinite(knee)
    raw = len(pp._crossings(x_dt))
    if fin_k.sum() < 10:
        return dict(raw=raw, knee_pass=None, spaced=None)
    knee_thresh = float(np.nanpercentile(knee, 60))
    edges, local = [], []
    for c in pp._crossings(x_dt):
        lo = max(0, int(c - 0.35 * fps))
        hi = min(len(knee), int(c + 0.35 * fps) + 1)
        lk = np.nanmax(knee[lo:hi]) if fin_k[lo:hi].any() else np.nan
        if np.isfinite(lk) and lk >= knee_thresh:
            edges.append(c)
            local.append(float(lk))
    kept = []
    for e, k in zip(edges, local):
        if kept and e - kept[-1][0] < 12:
            if k > kept[-1][1]:
                kept[-1] = (e, k)
        else:
            kept.append((e, k))
    return dict(raw=raw, knee_pass=len(edges), spaced=len(kept))


def dominant_period_s(x: np.ndarray, fps: float):
    x = x - x.mean()
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), d=1.0 / fps)
    band = (freqs >= 0.1) & (freqs <= 1.2)  # 0.8~10s 周期带
    if not band.any() or not spec[band].any():
        return None
    f = freqs[band][int(np.argmax(spec[band]))]
    return float(1.0 / f) if f > 0 else None


def run_silver_check() -> list:
    """攻击线2：银标准换刃的间隔、原始过零、窗口去趋势对照、FFT 主周期。"""
    import preannotate_phases as pp
    rows = []
    for clip in MANIFEST["clips"]:
        name, trc = clip["name"], Path(clip["trc"])
        x, knee = silver_signals(trc)
        f = CACHE / f"{name}.npz"
        fps = float(np.load(f)["fps"][0]) if f.exists() else 25.0
        ann = json.loads((ANN_DIR / f"{name}_phase_annotation.json").read_text(encoding="utf-8"))
        edges = sorted(m["frame"] for m in ann["marks"] if m["kind"] == "edge")
        gaps = np.diff(edges) / fps if len(edges) > 1 else np.array([])
        xg = pp._detrend_smooth(x)  # 全片全局线性去趋势（= preannotate 现状）
        glob = edge_pipeline(xg, knee, fps)
        w5 = edge_pipeline(window_detrend(x, int(5 * fps)), knee, fps)
        w25 = edge_pipeline(window_detrend(x, int(2.5 * fps)), knee, fps)
        period = dominant_period_s(window_detrend(x, int(5 * fps)), fps)
        row = {
            "clip": name, "fps": fps, "dur_s": round(len(x) / fps, 1),
            "silver_edges": len(edges),
            "gap_median_s": round(float(np.median(gaps)), 2) if gaps.size else None,
            "gap_max_s": round(float(gaps.max()), 2) if gaps.size else None,
            "crossings_globalLin_raw": glob["raw"],
            "knee_pass_globalLin": glob["knee_pass"],
            "raw_win5s": w5["raw"],
            "knee_pass_win5s": w5["knee_pass"], "spaced_win5s": w5["spaced"],
            "knee_pass_win2.5s": w25["knee_pass"], "spaced_win2.5s": w25["spaced"],
            "fft_period_s": round(period, 2) if period else None,
        }
        rows.append(row)
        print("[silver] " + name + ": " + json.dumps(row, ensure_ascii=False))
    return rows


# ---------- 攻击线3：错分率口径分解 ----------

def pair_auto_to_ann(seg, ann_edges: list, fps: float) -> list:
    pairs = []
    used = set()
    for t in seg.turns:
        ae = t.start_frame
        rem = [k for k in range(len(ann_edges)) if k not in used]
        if not rem:
            continue
        dd = [abs(ae - ann_edges[k]) for k in rem]
        j = int(np.argmin(dd))
        if dd[j] <= 0.75 * fps:
            used.add(rem[j])
            pairs.append((ae, ann_edges[rem[j]]))
    return pairs


def run_misclass_split() -> list:
    import check_phase_annotation as chk
    rows = []
    for clip in MANIFEST["clips"]:
        name = clip["name"]
        ann = json.loads((ANN_DIR / f"{name}_phase_annotation.json").read_text(encoding="utf-8"))
        d = series_for(clip)
        fps = float(d["fps"][0])
        seg = segment_phases(d["lean"], fps=fps, knee=d["knee"],
                             lateral=d["lateral"])
        marks = sorted(ann["marks"], key=lambda m: m["frame"])
        ann_edges = [m["frame"] for m in marks if m["kind"] == "edge"]
        n = len(d["lean"])
        ref = chk._labels_from_annotation(
            {"fps": fps, "marks": marks, "control": ann.get("control", {})}, n)
        auto = chk._labels_from_auto(seg, n)
        both = (ref >= 0) & (auto >= 0)
        if not both.any():
            rows.append({"clip": name, "misclass_report": None, "n_both": 0})
            print("[mis] " + name + ": 无双方覆盖帧")
            continue
        wrong = ref[both] != auto[both]
        idx = np.where(both)[0]
        dist = np.min(np.abs(idx[:, None] - np.array(ann_edges)[None, :]), axis=1)
        near = dist < 0.25 * fps
        pairs = pair_auto_to_ann(seg, ann_edges, fps)
        auto_turns = {t.start_frame: t for t in seg.turns}
        m_idx, m_wrong = 0, 0
        for ae, a in pairs:
            k = ann_edges.index(a)
            nxt = ann_edges[k + 1] if k + 1 < len(ann_edges) else n
            t = auto_turns[ae]
            lo, hi = max(t.start_frame, a), min(t.end_frame, nxt)
            if hi <= lo:
                continue
            b = (ref[lo:hi] >= 0) & (auto[lo:hi] >= 0)
            m_wrong += int((ref[lo:hi][b] != auto[lo:hi][b]).sum())
            m_idx += int(b.sum())
        row = {
            "clip": name, "fps": fps,
            "misclass_report": round(float(wrong.mean()), 4),
            "n_both": int(both.sum()),
            "ref_covered_frames": int((ref >= 0).sum()),
            "auto_covered_frames": int((auto >= 0).sum()),
            "both_coverage_of_ref": round(float(both.sum() / max(1, (ref >= 0).sum())), 3),
            "frac_both_near_edge": round(float(near.mean()), 3),
            "misclass_near_edge_0.25s": round(float(wrong[near].mean()), 4) if near.any() else None,
            "misclass_interior": round(float(wrong[~near].mean()), 4) if (~near).any() else None,
            "matched_turns": len(pairs),
            "matched_internal_misclass": round(m_wrong / m_idx, 4) if m_idx else None,
            "matched_internal_frames": m_idx,
        }
        rows.append(row)
        print("[mis] " + name + ": " + json.dumps(row, ensure_ascii=False))
    return rows


# ---------- 攻击线4：配对误差合并统计 ----------

def run_pair_stats() -> dict:
    rows = []
    for clip in MANIFEST["clips"]:
        name = clip["name"]
        ann = json.loads((ANN_DIR / f"{name}_phase_annotation.json").read_text(encoding="utf-8"))
        d = series_for(clip)
        fps = float(d["fps"][0])
        seg = segment_phases(d["lean"], fps=fps, knee=d["knee"],
                             lateral=d["lateral"])
        ann_edges = sorted(m["frame"] for m in ann["marks"] if m["kind"] == "edge")
        pairs = pair_auto_to_ann(seg, ann_edges, fps)
        errs = sorted(abs(ae - a) / fps for ae, a in pairs)
        rows.append({"clip": name, "n_pairs": len(errs),
                     "errs_s": [round(e, 3) for e in errs]})
        print("[pair] " + name + ": n=" + str(len(errs)) + " errs=" + str(rows[-1]["errs_s"]))
    pooled = [e for r in rows for e in r["errs_s"]]
    ex = [e for r in rows if r["clip"] != "FW1S3VAKui8" for e in r["errs_s"]]
    rng = np.random.default_rng(0)
    boots = [float(np.median(rng.choice(pooled, len(pooled)))) for _ in range(5000)]
    boots_ex = [float(np.median(rng.choice(ex, len(ex)))) for _ in range(5000)]
    out = {
        "per_clip": rows,
        "pooled_n": len(pooled),
        "pooled_median_s": round(float(np.median(pooled)), 3),
        "pooled_p90_s": round(float(np.percentile(pooled, 90)), 3),
        "pooled_median_boot95": [round(float(np.percentile(boots, 2.5)), 3),
                                 round(float(np.percentile(boots, 97.5)), 3)],
        "excl_FW1S3VAKui8_n": len(ex),
        "excl_FW1S3VAKui8_median_s": round(float(np.median(ex)), 3),
        "excl_FW1S3VAKui8_p90_s": round(float(np.percentile(ex, 90)), 3),
        "excl_FW1S3VAKui8_boot95": [round(float(np.percentile(boots_ex, 2.5)), 3),
                                    round(float(np.percentile(boots_ex, 97.5)), 3)],
        "note": "所有配对误差按定义 <0.75s（配对 cap 截断），p90 是条件分布",
    }
    print("[pair] pooled: " + json.dumps(out, ensure_ascii=False))
    return out


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = {}
    if which in ("all", "thr"):
        out["threshold"] = run_threshold_attr()
    if which in ("all", "silver"):
        out["silver"] = run_silver_check()
    if which in ("all", "mis"):
        out["misclass"] = run_misclass_split()
    if which in ("all", "pair"):
        out["pairs"] = run_pair_stats()
    dest = Path(__file__).resolve().parent / f"attack_results_{which}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] -> {dest}")
