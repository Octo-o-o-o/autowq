"""PnL 语义：累计值才差分，日 PnL 不得二次差分；缺失日期显式标出，不当零收益。

PnL 是研究指标，不是收入。收入只来自 payments 账本。
"""
from __future__ import annotations

import datetime as dt

from .errors import ContractError


def _sorted_points(points: list[dict]) -> list[tuple[dt.date, float]]:
    pts = []
    for p in points:
        d = dt.date.fromisoformat(p["date"])
        pts.append((d, float(p["value"])))
    pts.sort(key=lambda x: x[0])
    dup = [a[0] for a, b in zip(pts, pts[1:]) if a[0] == b[0]]
    if dup:
        raise ContractError([f"PnL 日期重复: {[d.isoformat() for d in dup]}"])
    return pts


def to_daily(points: list[dict], kind: str) -> list[tuple[dt.date, float]]:
    """返回日 PnL 序列。

    kind='daily' 直接返回；'cumulative' 取相邻差分（首点以 0 为基准）。
    对已按日历排序的输入做差分；缺口由 find_gaps 单独报告。
    """
    if kind not in ("cumulative", "daily"):
        raise ContractError([f"未知 pnl.kind: {kind}"])
    pts = _sorted_points(points)
    if kind == "daily":
        return pts
    out = []
    prev = 0.0
    for d, v in pts:
        out.append((d, v - prev))
        prev = v
    return out


def find_gaps(points: list[dict]) -> list[dict]:
    """相邻日期间隔 >1 个日历日即记缺口。缺口不是零收益，需要在对齐/相关性
    计算时显式处理；是否对应交易日由调用方结合平台日历判断。"""
    pts = _sorted_points(points)
    gaps = []
    for (d0, _), (d1, _) in zip(pts, pts[1:]):
        n = (d1 - d0).days - 1
        if n > 0:
            gaps.append({"after": d0.isoformat(), "before": d1.isoformat(), "missing_days": n})
    return gaps


def daily_corr_key(points: list[dict], kind: str) -> dict:
    """给相关性/对齐用的规范化视图：日收益 + 显式缺口列表。"""
    daily = to_daily(points, kind)
    return {
        "daily": [{"date": d.isoformat(), "value": v} for d, v in daily],
        "gaps": find_gaps(points),
        "n_points": len(daily),
    }
