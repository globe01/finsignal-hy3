"""阴性对照生成（对应方案 §7.3、§8.5 阴性/阈下边界）。

阴性对照 = 清洁公司（八类信号均不触发）；阈下边界 = 把某项指标推到接近但未过阈值。
"""
from __future__ import annotations

from typing import List

from generator.base import Company, make_clean_company, reconcile
from generator.inject import inject

# 仅对“可注入”类型做阈下边界（强度很小，使其刚好低于 θ）
_NEAR_THRESHOLD = {
    "receivables_revenue_divergence": 0.5,
    "inventory_cost_divergence": 0.5,
    "goodwill_net_assets_pressure": 0.5,
    "gross_net_margin_divergence": 0.5,
    "cashflow_profit_divergence": 0.5,
}


def make_negative_control(seed: int = 0, name: str = "阴性对照公司") -> Company:
    """清洁公司，金标准 G 应为空集。"""
    return make_clean_company(name=name, years=[2022, 2023, 2024])


def make_boundary_control(signal_type: str, seed: int = 0,
                          name: str = "阈下边界公司") -> Company:
    """把某项指标推到接近阈值但未触发，用于检验误报。"""
    c = make_clean_company(name=name, years=[2022, 2023, 2024])
    if signal_type in _NEAR_THRESHOLD:
        c, _ = inject(c, signal_type, strength=_NEAR_THRESHOLD[signal_type] * 0.4)
    return c
