"""三档水平参考区间：加载 reference_values.toml，查询与达标率计算。

决策依据 docs/DECISIONS.md 第 8 条。表内数值未校准前，报告必须带"参考值未校准"水印。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .metrics import resolve_metric

LEVELS = ("novice", "intermediate", "advanced")

# 仓库根的参考区间表：cli / web / 改选共用的唯一推导，别处不再各写一份路径
DEFAULT_REFS = Path(__file__).resolve().parent.parent / "reference_values.toml"

# 水平档的中文显示名：报告页、任务页、对比页、cmdk 都从这里取，
# 模板与 JS 不再各抄一份映射
LEVEL_CN = {"novice": "初学", "intermediate": "中级", "advanced": "竞技"}


@dataclass
class MetricRef:
    lo: float
    hi: float
    verdict_low: str = ""
    verdict_high: str = ""
    verdict_ok: str = "在参考区间内"


def load(path: str | Path) -> dict[str, dict[str, MetricRef]]:
    """返回 {metric: {level: MetricRef}}。文件缺失或结构非法时抛异常。"""
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    refs: dict[str, dict[str, MetricRef]] = {}
    for metric, levels in raw.items():
        refs[metric] = {}
        for level, table in levels.items():
            lo, hi = table["range"]
            refs[metric][level] = MetricRef(
                lo=lo,
                hi=hi,
                verdict_low=table.get("verdict_low", ""),
                verdict_high=table.get("verdict_high", ""),
                verdict_ok=table.get("verdict_ok", "在参考区间内"),
            )
    return refs


def lookup(refs: dict[str, dict[str, MetricRef]], metric: str, level: str) -> MetricRef:
    """查询某指标某档区间。缺档时回退到 novice（最宽区间，保守）。

    旧名按别名解析（knee_valgus → knee_abduction）。旧 TOML 只有旧键时
    回退到旧键本身，不要求表文件先迁移。
    """
    key = resolve_metric(metric)
    if key not in refs and metric in refs:
        key = metric
    if key not in refs:
        raise KeyError(f"参考区间表中没有指标: {metric}")
    by_level = refs[key]
    if level not in by_level:
        if level not in LEVELS:
            raise ValueError(f"未知水平档: {level}，可选 {LEVELS}")
        return by_level["novice"]
    return by_level[level]


def pct_in_range(series: np.ndarray, ref: MetricRef) -> float:
    """有效帧中落在区间内的比例（0~1）。全无效返回 NaN。"""
    valid = series[~np.isnan(series)]
    if valid.size == 0:
        return np.nan
    in_range = (valid >= ref.lo) & (valid <= ref.hi)
    return float(in_range.mean())
