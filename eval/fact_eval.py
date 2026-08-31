"""D1 原始数值准确性 与 D3 证据可追溯性（对应方案 §6.1）。

- D1：fact_basis 中声称来自报表的数值，按 (中文表头, 年度) 回表，相对误差 <= 1% 视为命中；
- D3：fact_basis 的 metric_name 属于已知科目且 period 在窗口年度内，视为可追溯。
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

import yaml

from app.schema import AnomalyCard
from generator.base import CN, Company, build_value_index

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")


def _cn_headers() -> set:
    return set(CN.values())


def eval_facts(card: AnomalyCard, company: Company) -> Dict[str, float]:
    """返回该卡片在 D1/D3 上的指标。"""
    idx = build_value_index(company)
    d1_hits, d1_total = 0, 0
    d3_hits, d3_total = 0, 0
    for f in card.fact_basis:
        d3_total += 1
        name = f.metric_name or ""
        period = str(f.period or "")
        if name in _cn_headers() and period in {str(y) for y in company.years}:
            d3_hits += 1
        d1_total += 1
        actual = idx.get((name, int(period) if period.isdigit() else period))
        if actual is not None and f.value is not None and actual != 0:
            if abs(f.value - actual) / abs(actual) <= 0.01:
                d1_hits += 1
    return {
        "d1_hits": d1_hits, "d1_total": d1_total,
        "d3_hits": d3_hits, "d3_total": d3_total,
    }


def aggregate_facts(cards: List[AnomalyCard], company: Company) -> Dict[str, float]:
    """汇总多个卡片的 D1/D3，返回命中率。"""
    h1 = t1 = h3 = t3 = 0
    for c in cards:
        r = eval_facts(c, company)
        h1 += r["d1_hits"]; t1 += r["d1_total"]
        h3 += r["d3_hits"]; t3 += r["d3_total"]
    return {
        "d1_rate": (h1 / t1) if t1 else None,
        "d3_rate": (h3 / t3) if t3 else None,
        "d1_hits": h1, "d1_total": t1,
        "d3_hits": h3, "d3_total": t3,
    }
