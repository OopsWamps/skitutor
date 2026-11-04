"""缝合断裂的主轨迹（抢救路径）：开头被跟踪器串到背景人物时，从备用轨迹借回正确片段。

背景（2026-10-19，video.mp4）：Sports2D 的跟踪器按逐帧检测串联人物，主角开局
若小或被雾遮，轨迹要几秒后才"接上"主角 —— person02 前 2.7 秒的关键点其实落在
右侧远景滑雪者身上，主角当时在 person01 轨迹里；84 帧起 person02 才干净贴住主角。
select_person 是全视频统一选一条轨迹，救不了段级串轨，只能在此缝合。

缝合计划（帧号由 hip 位置 + 包围盒尺寸跳变人工核对，见 tmp_frames/cut_*.png）：
  - 0–81    用 person01（稳定贴主角，hip x≈600-612）
  - 82–83   两轨都不干净，按 81 与 84 两帧逐关键点线性补插
  - 84–末尾 用 person02（干净贴主角）

用法：python scripts/stitch_tracks.py [level] [--main-pid P] [--donor-pid P]
      [--donor-last N] [--main-first N]
默认值即 video.mp4 那次人工核对的结论，老的不带参调用行为不变。
产物：stitched trc（可再用 skibot render 重渲）+ 覆盖 report.json + 标注视频。

评估复用主流程组件（classify_view_frames、compute_series 的帧级门控、
build_report、Assessment、render_annotated），但不等于 skibot.assess 的
原样调用，两处差异：
  - 视角按完整信号段（--main-first 起）判定，是人工干预、detail 有注
    （见 classify_view_clean）：开头段肘/腕关键点缺失会让整片判定翻成
    ambiguous，属验证进展 #10 帧级门控口径之外的一次显式例外；
  - 序列计算带帧级门控（无信号帧不贡献任何指标），缝合脚本旧版没有这层。
"""

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from skibot import artifacts  # noqa: E402
from skibot import metrics as M  # noqa: E402
from skibot.assess import Assessment  # noqa: E402
from skibot.reference import load as load_refs  # noqa: E402
from skibot.render import render_annotated  # noqa: E402
from skibot.report import build_report, save_json  # noqa: E402
from skibot.trc_io import load_kp, stitch, write_trc  # noqa: E402
from skibot.view import ViewResult, classify_view, classify_view_frames  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "results" / "video"
S2D_DIR = WORK / "video_blurred_Sports2D"
BLURRED = WORK / "video_blurred.mp4"
ANNOTATED = artifacts.annotated_path(WORK)

TRC_PREFIX = "video_blurred_Sports2D_px_"
# 默认值 = video.mp4 那次人工核对的结论，命令行参数可覆盖
MAIN_PID, DONOR_PID = "person02", "person01"
DONOR_LAST, MAIN_FIRST = 81, 84  # 供体用到 81（含），主轨 84 起干净；82-83 补插


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="缝合断裂的主轨迹（抢救路径）")
    p.add_argument("level", nargs="?", default="advanced",
                   help="参考区间档位：novice/intermediate/advanced")
    p.add_argument("--main-pid", default=MAIN_PID, help="主轨迹（后半段干净）人物 id")
    p.add_argument("--donor-pid", default=DONOR_PID, help="供体轨迹（开头贴主角）人物 id")
    p.add_argument("--donor-last", type=int, default=DONOR_LAST, help="供体最后一帧（含）")
    p.add_argument("--main-first", type=int, default=MAIN_FIRST, help="主轨起始干净帧")
    return p.parse_args()


def classify_view_clean(kp: np.ndarray, main_first: int) -> ViewResult:
    """视角按完整信号段（main_first 起）判定 —— 人工干预，detail 留注。

    开头段肘/腕关键点全部缺失，view 的"左右置信度不对称"信号（侧面最强的
    一票）失效，剩余肩线/鼻偏信号对该姿态全投正面票，会把本就贴着 0.4
    边界的全片判定翻成 ambiguous。主体干净段信号齐全，判定可信。
    属验证进展 #10 帧级门控口径之外的一次显式例外。
    """
    vr = classify_view(kp[main_first:])
    vr.detail["note"] = (f"按 {main_first} 帧后的完整信号段判定；"
                         "开头段肘腕缺失、侧面信号不可用，不参与投票")
    return vr


def main() -> int:
    args = parse_args()
    main_trc = S2D_DIR / f"{TRC_PREFIX}{args.main_pid}.trc"
    donor_trc = S2D_DIR / f"{TRC_PREFIX}{args.donor_pid}.trc"
    kp_main, kp_donor = load_kp(main_trc), load_kp(donor_trc)
    if kp_main.shape != kp_donor.shape:
        raise ValueError(f"两轨形状不一致: {kp_main.shape} vs {kp_donor.shape}")

    kp = stitch(kp_main, kp_donor, args.donor_last, args.main_first)
    trc = write_trc(kp, main_trc, S2D_DIR / f"{TRC_PREFIX}stitched.trc")
    print(f"[stitch] 缝合轨迹：{args.donor_pid} 0-{args.donor_last} 帧 + 补插 "
          f"{args.donor_last + 1}-{args.main_first - 1} 帧 + {args.main_pid} {args.main_first} 帧起")
    print(f"[stitch] trc: {trc}")

    refs = load_refs(ROOT / "reference_values.toml")
    frame_view = classify_view_frames(kp)
    series = M.compute_series(kp, frame_view=frame_view)
    # 视角不走 frame_view 的整片汇总口径，见 classify_view_clean 的说明
    vr = classify_view_clean(kp, args.main_first)
    report = build_report("video.mp4", vr, series, refs, args.level,
                          person={"id": 2, "selection":
                                  f"轨迹缝合：主体 {args.main_pid}，开头 0-{args.donor_last} 帧借用 "
                                  f"{args.donor_pid}（{args.main_pid} 前 2.7 秒串到右侧背景人物）"})
    assessment = Assessment(report=report, series=series, frame_view=frame_view,
                            view=vr, refs=refs)
    v = report["view"]
    print(f"[stitch] 视角: {v['detected']} (score={v['score']:.2f}, {v['n_valid_frames']} 帧)")
    for m in report["metrics"]:
        print(f"[stitch] {m['metric']}: 中位 {m['median_deg']}°, "
              f"有效 {m['n_valid_frames']} 帧, 区间内 {m['pct_in_range']}")
    save_json(report, artifacts.report_path(WORK))
    print(f"[stitch] 报告: {WORK / 'report.json'}")

    anno = render_annotated(BLURRED, kp, assessment, ANNOTATED)
    print(f"[stitch] 标注视频: {anno}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
