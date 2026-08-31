"""会计一致的异常注入引擎（对应方案 §5.4）。

初稿实现四类重点注入 + 现金流背离，合计五类；其余类型按需扩展。
每个注入只改“业务科目 + 对应现金流”，再交给 reconcile() 滚动货币资金、
用权益做平衡项，保证三条恒等式成立（§5.4）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from generator.base import Company, _growth, reconcile

# 支持注入的信号（重点四类 + 现金流）
INJECTABLE = {
    "receivables_revenue_divergence",
    "inventory_cost_divergence",
    "goodwill_net_assets_pressure",
    "gross_net_margin_divergence",
    "cashflow_profit_divergence",
}


@dataclass
class InjectionMeta:
    signal_type: str
    strength: float
    years: List[int]
    edits: List[Dict] = field(default_factory=list)  # 记录改了哪些单元格，供溯源


def _edit(statement: str, key: str, year: int, new_value: float, old_value: float, meta: InjectionMeta):
    meta.edits.append({"statement": statement, "key": key, "year": year, "old": old_value, "new": new_value})


def inject(company: Company, signal_type: str, strength: float = 1.0) -> tuple[Company, InjectionMeta]:
    """对 company 做会计一致注入，返回（注入后公司, 注入元数据）。"""
    if signal_type not in INJECTABLE:
        raise ValueError(f"暂不支持注入类型：{signal_type}")
    meta = InjectionMeta(signal_type=signal_type, strength=strength, years=list(company.years[-2:]))
    yrs = meta.years
    c = Company(name=company.name, years=list(company.years),
                income={k: dict(v) for k, v in company.income.items()},
                balance={k: dict(v) for k, v in company.balance.items()},
                cashflow={k: dict(v) for k, v in company.cashflow.items()})

    if signal_type == "receivables_revenue_divergence":
        for t in yrs:
            rev_g = _growth(c.income["revenue"], c.years, t)
            target_g = rev_g + 0.25 * strength
            old = c.balance["accounts_receivable"][t]
            new = old * (1 + target_g)
            c.balance["accounts_receivable"][t] = new
            _edit("balance", "accounts_receivable", t, new, old, meta)
            d = new - old
            old_cfo = c.cashflow["cfo"][t]
            c.cashflow["cfo"][t] = old_cfo - d
            _edit("cashflow", "cfo", t, c.cashflow["cfo"][t], old_cfo, meta)

    elif signal_type == "inventory_cost_divergence":
        for t in yrs:
            cogs_g = _growth(c.income["cogs"], c.years, t)
            target_g = cogs_g + 0.25 * strength
            old = c.balance["inventory"][t]
            new = old * (1 + target_g)
            c.balance["inventory"][t] = new
            _edit("balance", "inventory", t, new, old, meta)
            d = new - old
            old_cfo = c.cashflow["cfo"][t]
            c.cashflow["cfo"][t] = old_cfo - d
            _edit("cashflow", "cfo", t, c.cashflow["cfo"][t], old_cfo, meta)

    elif signal_type == "goodwill_net_assets_pressure":
        for t in yrs:
            old = c.balance["goodwill"][t]
            new = c.balance["equity"][t] * (0.25 * strength)
            c.balance["goodwill"][t] = new
            _edit("balance", "goodwill", t, new, old, meta)
            d = new - old
            old_cfi = c.cashflow["cfi"][t]
            c.cashflow["cfi"][t] = old_cfi - d  # 现金收购 → 投资现金流出
            _edit("cashflow", "cfi", t, c.cashflow["cfi"][t], old_cfi, meta)

    elif signal_type == "gross_net_margin_divergence":
        for t in yrs:
            # 毛利率略升：营业成本小幅下降（制造“毛利上升”表象）
            old_cogs = c.income["cogs"][t]
            new_cogs = old_cogs * (1 - 0.02 * strength)
            c.income["cogs"][t] = new_cogs
            _edit("income", "cogs", t, new_cogs, old_cogs, meta)
            # 净利率下降更多：净利多降（制造“净利下滑”表象）
            old_np = c.income["net_profit"][t]
            new_np = old_np * (1 - 0.30 * strength)
            c.income["net_profit"][t] = new_np
            _edit("income", "net_profit", t, new_np, old_np, meta)
            d = old_np - new_np  # 现金费用（净利下滑的现金对应项）
            old_cfo = c.cashflow["cfo"][t]
            c.cashflow["cfo"][t] = old_cfo - d
            _edit("cashflow", "cfo", t, c.cashflow["cfo"][t], old_cfo, meta)

    elif signal_type == "cashflow_profit_divergence":
        for t in yrs:
            old = c.cashflow["cfo"][t]
            # 经营现金流/净利润随强度下降：strength=1→0.30，strength=2→0.10（更低=更异常）
            target_ratio = max(0.05, 0.5 - 0.20 * strength)
            new = c.income["net_profit"][t] * target_ratio
            c.cashflow["cfo"][t] = new
            _edit("cashflow", "cfo", t, new, old, meta)

    return reconcile(c), meta
