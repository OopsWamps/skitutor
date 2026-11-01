"""换刃/顶点预标注（独立 2D 信号，issue 11 的银标准）。

从 2D 像素关键点生成换刃与顶点候选，供人工在 annotate_server 里确认/
修正后成为正式标注：

- 换刃候选 = 髋中点横向轨迹（滚动均值高通去漂移）的过零点中，局部膝较直
  者——换刃时刻滑雪者蹬伸卸压。注意这是弱过滤（±0.35s 窗内 nanmax 高于
  全片 60 分位），长片/变节奏素材的候选数会偏多或偏少，人工确认不可省；
- 顶点 = 相邻换刃之间 2D 膝夹角最小帧（最深屈曲；膝缺测时用横向轨迹
  幅值极值兜底）。

信号来源与被测的自动切分（三维重力参考侧倾）独立：纯像素 2D vs 三维。
银标准不等于人工标注；对比报告里必须注明标注来源。

用法：
    python scripts/preannotate_phases.py --manifest <manifest.json> [--out-dir DIR]

manifest 是 datasets/recreational_ski/phase_annotation_manifest.json（字段
clips[].name/video/trc）。输出写到 <out-dir>/<name>_phase_annotation.json。
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

from skibot.phases import _zero_crossings  # noqa: E402  零平台语义与自动侧一致
from skibot.skeleton import (  # noqa: E402
    CONF_MIN, L_ANKLE, L_HIP, L_KNEE, R_ANKLE, R_HIP, R_KNEE)
from skibot.trc_io import load_kp  # noqa: E402

KNEE_PCTL_FOR_EDGE = 60   # 换刃候选要求膝夹角不低于此百分位（近伸直；弱过滤）
MIN_TURN_S = 0.5          # 相邻换刃最小间隔秒——与自动侧 MIN_DURATION_S 同规范


def _hip_lateral(kp: np.ndarray) -> np.ndarray:
    """髋中点横向轨迹：双髋低置信的帧记 NaN。"""
    left = kp[:, L_HIP]
    right = kp[:, R_HIP]
    ok = (left[:, 2] >= CONF_MIN) & (right[:, 2] >= CONF_MIN)
    return np.where(ok, (left[:, 0] + right[:, 0]) / 2.0, np.nan)


def _detrend_smooth(x: np.ndarray, fps: float, smooth: int = 3) -> np.ndarray:
    """滚动均值高通（窗 ≈5s）去漂移 + 均值平滑；NaN 先按邻域线性插值。

    不用全局线性拟合：对振荡信号做全局线性去趋势会把内部过零推偏
    0.1~0.3s（短片段最坏），与换刃误差门同量级；滚动均值的窗远长于弯
    周期，只吃缓慢漂移。
    """
    t = np.arange(len(x))
    fin = np.isfinite(x)
    x = np.interp(t, t[fin], x[fin])
    win = max(3, int(round(5.0 * fps)) | 1)  # 奇数化
    if len(x) > win:
        pad = win // 2
        padded = np.pad(x, pad, mode="edge")
        x = x - np.convolve(padded, np.ones(win) / win, mode="valid")
    if len(x) >= smooth:
        x = np.convolve(x, np.ones(smooth) / smooth, mode="same")
    return x


def _knee_angles_2d(kp: np.ndarray) -> np.ndarray:
    """左右 2D 膝夹角（180°=伸直）的逐帧均值；缺测侧忽略。"""

    def ang(f: int, hp: int, kn: int, an: int) -> float:
        if min(kp[f, hp, 2], kp[f, kn, 2], kp[f, an, 2]) < CONF_MIN:
            return np.nan
        v1 = kp[f, hp, :2] - kp[f, kn, :2]
        v2 = kp[f, an, :2] - kp[f, kn, :2]
        n = np.linalg.norm(v1) * np.linalg.norm(v2)
        if n < 1e-9:
            return np.nan
        return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / n, -1, 1))))

    t = kp.shape[0]
    out = np.full(t, np.nan)
    for f in range(t):
        vals = [ang(f, L_HIP, L_KNEE, L_ANKLE), ang(f, R_HIP, R_KNEE, R_ANKLE)]
        if np.isfinite(vals).any():
            out[f] = np.nanmean(vals)
    return out


def preannotate(trc: Path, fps: float) -> dict:
    kp = load_kp(trc)
    lateral = _detrend_smooth(_hip_lateral(kp), fps)
    knee = _knee_angles_2d(kp)
    fin_k = np.isfinite(knee)
    if fin_k.sum() < 10:
        raise ValueError(f"{trc} 的 2D 膝角可用帧过少（{int(fin_k.sum())}），无法预标注")
    knee_thresh = float(np.nanpercentile(knee, KNEE_PCTL_FOR_EDGE))
    min_gap = max(1, int(round(MIN_TURN_S * fps)))

    # 首尾各 0.5s 内的过零丢弃：没有前半弯/后半弯支撑，且高通的端部
    # padding 会制造幻影过零
    edge_guard = 0.5 * fps
    n = len(lateral)
    crossings = [c for c in _zero_crossings(lateral)
                 if edge_guard <= c <= n - 1 - edge_guard]

    edges: list[float] = []
    for c in crossings:
        lo = max(0, int(c - 0.35 * fps))
        hi = min(len(knee), int(c + 0.35 * fps) + 1)
        local_knee = np.nanmax(knee[lo:hi]) if fin_k[lo:hi].any() else np.nan
        if np.isfinite(local_knee) and local_knee >= knee_thresh:
            edges.append(c)
    # 最小弯长：间隔过近的候选保留膝更直（局部膝夹角更高）的一个
    local = []
    for e in edges:
        lo = max(0, int(e - 0.35 * fps))
        hi = min(len(knee), int(e + 0.35 * fps) + 1)
        local.append(float(np.nanmax(knee[lo:hi])) if fin_k[lo:hi].any() else 0.0)
    filtered: list[tuple[float, float]] = []
    for e, k in zip(edges, local):
        if filtered and e - filtered[-1][0] < min_gap:
            if k > filtered[-1][1]:  # 换成膝更直的候选
                filtered[-1] = (e, k)
        else:
            filtered.append((e, k))
    edges = [e for e, _ in filtered]

    marks: list[dict] = []
    for e in edges:
        marks.append({"frame": int(round(e)), "kind": "edge"})
    for i in range(len(edges) - 1):
        lo, hi = int(edges[i]) + 1, int(edges[i + 1])
        seg = knee[lo:hi]
        if fin_k[lo:hi].any():
            apex = lo + int(np.nanargmin(seg))
        else:  # 膝缺测：横向幅值极值兜底
            apex = lo + int(np.nanargmax(np.abs(lateral[lo:hi])))
        marks.append({"frame": int(apex), "kind": "apex"})
    marks.sort(key=lambda m: m["frame"])
    return {"trc": str(trc), "fps": fps, "annotator": "silver-2d",
            "marks": marks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="换刃/顶点预标注（银标准）")
    ap.add_argument("--manifest", type=Path, required=True,
                    help="素材清单（phase_annotation_manifest.json）")
    ap.add_argument("--out-dir", type=Path,
                    default=Path("datasets/recreational_ski/annotations"),
                    help="标注 JSON 输出目录")
    args = ap.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    args.out_dir.mkdir(parents=True, exist_ok=True)
    from skibot.render import probe_video

    for clip in manifest["clips"]:
        name = clip["name"]
        trc = Path(clip["trc"])
        _, _, fps = probe_video(Path(clip["video"]))
        if not fps:
            print(f"[skibot] {name}: 读不出帧率，跳过", file=sys.stderr)
            continue
        try:
            ann = preannotate(trc, float(fps))
        except ValueError as e:
            print(f"[skibot] {name}: 预标注失败（{e}）", file=sys.stderr)
            continue
        ann["video"] = clip["video"]
        ann["form"] = clip.get("form")
        out = args.out_dir / f"{name}_phase_annotation.json"
        out.write_text(json.dumps(ann, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        n_edge = sum(1 for m in ann["marks"] if m["kind"] == "edge")
        print(f"[skibot] {name}: 预标注 {n_edge} 换刃 / "
              f"{len(ann['marks']) - n_edge} 顶点 → {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
