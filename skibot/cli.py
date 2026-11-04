"""命令行入口：skibot analyze <video...> / skibot render

流程（docs/DECISIONS.md 第 4 条）：打码 → Sports2D 管线（backend）→ 主轨迹
选择（select_person）→ 评估流程（assess：视角分类 → 五指标 → 参考区间 →
评估结果）→ 报告落盘 + 自绘标注视频（render，只画选中者）。
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows GBK 控制台兜底

from . import __version__
from . import artifacts
from . import runprep
from .assess import assess
from .backend import DEFAULT_TRACK_ORDER, discover_tracks, run_sports2d
from .privacy import blur_faces
from .reference import DEFAULT_REFS, load as load_refs
from .render import render_annotated
from .select_person import sample_pants_pixels, select_person
from .trc_io import load_kp


@dataclass
class AnalyzeOptions:
    """一次分析的全部选项。新增 CLI 旗标只动这里与 argparse，调用点不动。"""
    level: str = "intermediate"
    track_order: str = DEFAULT_TRACK_ORDER
    blur: bool = True
    result_dir: Path = Path("results")
    refs: Path = DEFAULT_REFS
    color: str | None = None
    person: int | None = None
    lifter: str = "none"  # 三维提升：none（默认，二期回归基线）/ motionbert


def analyze_one(video: Path, opts: AnalyzeOptions) -> Path:
    work = Path(opts.result_dir) / video.stem
    work.mkdir(parents=True, exist_ok=True)

    src = video
    if opts.blur:
        src, n_faces = blur_faces(video, artifacts.blurred_path(work))
        print(f"[skibot] 人脸打码：检出 {n_faces} 处（Haar 召回有限，雪镜/面罩场景见 privacy.py 说明）")
    else:
        # 同一结果目录里上一次打码留下的副本必须删掉：改选按副本是否存在
        # 推断首次分析的打码设置，留着旧副本会拿到过期画面
        artifacts.blurred_path(work).unlink(missing_ok=True)

    print(f"[skibot] Sports2D 分析中：{src}")
    run_start = time.time()
    run_sports2d(src, work, opts.track_order)

    tracks = discover_tracks(work)
    # 只认本次运行产出（新建或更新）的轨迹。backend 只按文件名找轨迹，
    # 复用同一结果目录重跑时，上一次的旧 trc 在 Sports2D 失败（崩溃/超时/
    # 可执行文件缺失）后仍会留在原地，直接采纳会产出基于旧关键点的"成功"
    # 报告。不预先删除旧轨迹——重跑失败时它们还是旧报告改选与预览的
    # 数据源（web 重试与 CLI 重跑受同一道防线保护）。
    fresh = {pid: p for pid, p in tracks.items()
             if p.stat().st_mtime >= run_start}
    if not fresh:
        raise FileNotFoundError(
            f"Sports2D 没有产出新轨迹（保留原有 {len(tracks)} 条旧轨迹），检查其日志")
    tracks = fresh
    kps = {pid: load_kp(p) for pid, p in tracks.items()}
    print(f"[skibot] 检测到 {len(kps)} 个人物轨迹：{sorted(kps)}")

    if opts.person is not None:
        if opts.person not in kps:
            raise ValueError(f"指定了 person {opts.person}，但只有 {sorted(kps)}")
        chosen, reason = opts.person, f"人工指定 person {opts.person}"
    else:
        colors_by_pid = None
        if opts.color:
            colors_by_pid = {pid: sample_pants_pixels(src, kp) for pid, kp in kps.items()}
        sel = select_person(kps, colors_by_pid=colors_by_pid, color_name=opts.color)

        hdr = (f"{'pid':>4} {'有效帧':>6} {'尺寸px':>8} {'位移px':>9}"
               f" {'尺寸分':>6} {'位移分':>6}")
        if opts.color:
            hdr += f" {'颜色分':>6}"
        print(f"[skibot] 候选打分：\n{hdr}")
        for r in sel.rows:
            line = (f"{r.pid:>4} {r.n_valid:>6} {r.size_px:>8.1f} {r.disp_px:>9.1f}"
                    f" {r.size_n:>6.3f} {r.disp_n:>6.3f}")
            if r.color is not None:
                line += f" {r.color:>6.3f}"
            line += f"  score={r.score}"
            print(line)
        if sel.color_fallback:
            print(f"[skibot] 警告：颜色 '{opts.color}' 最高得分仅 {sel.color_best}，"
                  "未匹配到可信目标，回退到尺寸+位移判定", file=sys.stderr)
        chosen, reason = sel.pid, sel.reason
    print(f"[skibot] 选定 person {chosen}：{reason}")

    kp = kps[chosen]
    print(f"[skibot] 关键点：{tracks[chosen].name}，{kp.shape[0]} 帧")

    # refs / fps / 身高 / lifter 的前置与改选共用一条链（runprep）；
    # 报告与标注视频经评估收尾成对原子发布
    res = runprep.publish(
        kp, src, level=opts.level, label=str(video),
        report_path=artifacts.report_path(work),
        video_path=artifacts.annotated_path(work),
        person={"id": chosen, "selection": reason},
        refs_path=opts.refs, lifter=opts.lifter)
    report = res.report
    print(f"[skibot] 视角判定：{report['view']['detected']}"
          f"（score={report['view']['score']:.2f}，{report['view']['n_valid_frames']} 有效帧）")
    if res.skeleton3d is not None:
        n_valid = int(res.skeleton3d.valid_mask.sum())
        print(f"[skibot] 三维骨架：{report['data_source']}，"
              f"有效帧 {n_valid}/{res.skeleton3d.n_frames}，"
              f"重力来源 {res.skeleton3d.gravity_source}")
    out = artifacts.report_path(work)
    print(f"[skibot] 报告已写出：{out}")
    print(f"[skibot] 标注视频（自绘，仅选中者）：{artifacts.annotated_path(work)}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="skibot", description="滑雪视频体态分析")
    parser.add_argument("--version", action="version", version=f"skibot {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="分析一段或多段滑雪视频")
    a.add_argument("videos", nargs="+", type=Path, help="视频文件路径")
    a.add_argument("--level", choices=("novice", "intermediate", "advanced"),
                   default="intermediate", help="滑雪水平档，决定参考区间")
    a.add_argument("--track-order", default=DEFAULT_TRACK_ORDER,
                   help=f"Sports2D 内部人物排序策略（默认 {DEFAULT_TRACK_ORDER}）；最终选人用 --color/--person")
    a.add_argument("--color", default=None,
                   help="按衣着颜色锁定主角，如 --color pink 或 --color #ff69b4")
    a.add_argument("--person", type=int, default=None,
                   help="直接指定 person 编号（跳过自动选人）")
    a.add_argument("--no-blur", action="store_true", help="关闭人脸打码（默认开）")
    a.add_argument("--lifter", choices=("none", "motionbert"), default="none",
                   help="三维提升模型（默认 none：只出二维报告；motionbert 需已"
                        "放置权重与 torch，见 scripts/check_motionbert.py）")
    a.add_argument("--result-dir", type=Path, default=Path("results"), help="输出目录")
    a.add_argument("--refs", type=Path, default=DEFAULT_REFS,
                   help="参考区间表路径")

    r = sub.add_parser("render", help="从已有 trc 重新渲染标注视频（跳过分析）")
    r.add_argument("--video", type=Path, required=True, help="画面来源视频（建议用打码后的）")
    r.add_argument("--trc", type=Path, required=True, help="px trc 文件")
    r.add_argument("--out", type=Path, required=True, help="输出 mp4 路径")
    r.add_argument("--view", choices=("sagittal", "frontal", "mixed"), default=None,
                   help="叠加哪套指标（默认按评估判定，mixed 走逐帧门控）")
    r.add_argument("--level", choices=("novice", "intermediate", "advanced"),
                   default="intermediate", help="水平档，决定区间着色")
    r.add_argument("--refs", type=Path, default=DEFAULT_REFS,
                   help="参考区间表路径")

    w = sub.add_parser("serve", help="启动 Web 壳（上传页 + 任务页 + 报告页 + 对比页）")
    w.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    w.add_argument("--host", default="127.0.0.1", help="监听地址（默认本机）")
    w.add_argument("--result-dir", type=Path, default=Path("web_results"),
                   help="分析产物目录（默认 web_results/）")
    w.add_argument("--no-precheck", action="store_true",
                   help="关闭上传预检（默认开启：容器/分辨率/时长/人物占比）")
    w.add_argument("--lifter", choices=("none", "motionbert"), default="motionbert",
                   help="三维提升档（默认 motionbert：上传默认开三维；none 把"
                        "上传页的三维开关压到关——权重/显卡不在位时用）")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .web.app import create_app
        uvicorn.run(create_app(args.result_dir,
                               enable_precheck=not args.no_precheck,
                               lifter=args.lifter),
                    host=args.host, port=args.port, log_level="info")
        return 0

    if args.cmd == "render":
        kp = load_kp(args.trc)
        refs = load_refs(args.refs)
        res = assess(kp, level=args.level, refs=refs, label=str(args.trc))
        if args.view is None and res.view.view == "ambiguous":
            print("[skibot] 视角判定不明确且未指定 --view，叠加层只画骨架；要出指标用 --view 强制",
                  file=sys.stderr)
        out = render_annotated(args.video, kp, res, args.out,
                               view=args.view)
        print(f"[skibot] 标注视频已写出：{out}")
        return 0

    if args.color and args.person is not None:
        print("[skibot] --color 与 --person 同给时以 --person 为准", file=sys.stderr)

    opts = AnalyzeOptions(level=args.level, track_order=args.track_order,
                          blur=not args.no_blur, result_dir=args.result_dir,
                          refs=args.refs, color=args.color, person=args.person,
                          lifter=args.lifter)

    rc = 0
    for v in args.videos:
        if not v.exists():
            print(f"[skibot] 找不到视频：{v}", file=sys.stderr)
            rc = 1
            continue
        try:
            analyze_one(v, opts)
        except Exception as e:  # noqa: BLE001 —— CLI 顶层兜底，逐视频失败不中断批量
            print(f"[skibot] {v.name} 分析失败：{e}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
