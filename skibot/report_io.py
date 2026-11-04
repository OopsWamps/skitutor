"""报告读取：report.json 的解析与可读判据，全站唯一事实源。

此前四处各写一份解析、四种容错口径：报告页严格形状检查、改选只取
person.id 并吞掉所有异常、改选重读全文只为拿 video 标签、任务表恢复用
{} 兜底——同一个文件，「能不能看」与「能抢救出什么」两个问题没有各自
的统一答案。现在收敛成两个入口：

- read_report：这份报告能不能作为报告页渲染？判据 = JSON 对象 + view
  对象 + metrics 列表（与报告页模板的最小读取面一致）。报告页、对比页、
  改选的 person/label 都走它；不可读返回 None，由调用方决定展示口径。
- read_report_meta：从可能很旧或写了一半的 report.json 里能抢救出什么
  元数据？只用于启动恢复/历史重建路径——读不出就给 {}，绝不抛异常，
  也不判断可渲染性（旧报告没有 view/metrics 是常态，不能因此丢任务）。

读写共用 artifacts 的 Windows 锁重试；这里不做字段级访问的封装，键名
仍由调用方按 report schema 取（schema 的唯一事实源在 report.build_report）。
"""

from __future__ import annotations

import json
from pathlib import Path

from . import artifacts


def read_report(path: str | Path) -> dict | None:
    """report.json → 报告 dict；读不出来（被删/写一半/损坏/形状不对）返回
    None。调用方不应再自己 json.loads report.json。"""
    try:
        data = json.loads(artifacts.read_text_retry(Path(path)))
    except (OSError, ValueError):
        return None
    # 合法 JSON 但形状不对（空对象、写了一半、别的文件冒充）：报告页会
    # 在模板里取 report.view 时抛 UndefinedError，同样按坏报告处理
    if (not isinstance(data, dict) or not isinstance(data.get("view"), dict)
            or not isinstance(data.get("metrics"), list)):
        return None
    return data


def read_report_meta(path: str | Path) -> dict:
    """report.json → 能解析出的顶层 dict；任何失败给 {}。

    与 read_report 的差别：不设形状门槛。启动恢复要把历史任务找回来
    （level/video/data_source），哪怕这份报告已经渲染不出来。
    """
    try:
        data = json.loads(artifacts.read_text_retry(Path(path)))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
