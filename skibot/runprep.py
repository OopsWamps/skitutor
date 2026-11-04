"""评估运行准备：refs 加载、视频探测、lifter 解析——评估收尾前的公共前置。

此前 cli.analyze_one 与改选（reselect_one）各自串同一份前置，lifter 解析
还各写一套习惯用法；「准备一次评估」的知识只此一处。发布本身仍走
finalize.assess_and_publish（评估收尾 module 的职责不变），这里只负责把
refs / fps / 身高 / lifter / person 一次备齐再交给它。帧率探测每条分析
链只做一次，评估与相位切分共用；渲染要画面，仍由 render_annotated 自己
打开视频取帧，与这里的元数据探测互不替代。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .assess import Assessment
from .finalize import assess_and_publish
from .reference import DEFAULT_REFS, load as load_refs
from .render import probe_video


def publish(
    kp: np.ndarray,
    src: str | Path,
    *,
    level: str,
    label: str,
    report_path: str | Path,
    video_path: str | Path,
    person: dict,
    refs_path: str | Path | None = None,
    lifter: str = "none",
) -> Assessment:
    """备齐评估前置（refs、fps、身高、lifter）并发布报告与标注视频。

    refs_path 缺省取仓库根的 reference_values.toml（reference.DEFAULT_REFS）；
    lifter 名走 motionbert.make_lifter 的唯一解析点（none → 二维基线）。
    失败语义与评估收尾一致：任何一步抛出，旧产物原样保留。
    """
    refs = load_refs(refs_path or DEFAULT_REFS)
    _, frame_height, fps = probe_video(src)
    # make_lifter 对 "none" 返回 None；motionbert 分支在构造时按需载 torch。
    # import 放函数内：不碰三维的调用路径不加载模型定义模块
    from .motionbert import make_lifter

    lifter_obj = make_lifter(lifter)
    if lifter_obj is not None:
        print(f"[runprep] 三维提升：{lifter}（权重 {lifter_obj.checkpoint}）")
    return assess_and_publish(
        kp, src, refs=refs, level=level, label=label,
        report_path=report_path, video_path=video_path, person=person,
        frame_height=frame_height, fps=fps, lifter=lifter_obj)
