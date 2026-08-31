"""会计报表恒等式校验（对应方案 §5.4 注入后必须执行的三条恒等式）。"""
from __future__ import annotations

from typing import List

from generator.base import Company

TOL = 1.0  # 金额容差（元）


def check_identities(c: Company) -> List[str]:
    """返回违背恒等式的描述列表；空列表表示三表一致。"""
    violations: List[str] = []
    y = c.years
    for t in y:
        ta = c.balance["total_assets"][t]
        eq_side = (
            c.balance["current_liabilities"][t]
            + c.balance["non_current_liabilities"][t]
            + c.balance["equity"][t]
        )
        if abs(ta - eq_side) > TOL:
            violations.append(f"[{t}] 总资产({ta:.0f}) != 负债+权益({eq_side:.0f})")
        gp = c.income["revenue"][t] - c.income["cogs"][t]
        if abs(gp - c.income["gross_profit"][t]) > TOL:
            violations.append(f"[{t}] 毛利勾稽不符")
    for i, t in enumerate(y):
        if i == 0:
            continue
        prev = y[i - 1]
        expected_cash = (
            c.balance["cash"][prev]
            + c.cashflow["cfo"][t]
            + c.cashflow["cfi"][t]
            + c.cashflow["cff"][t]
        )
        if abs(expected_cash - c.balance["cash"][t]) > TOL:
            violations.append(
                f"[{t}] 现金恒等式不符：期望{expected_cash:.0f} 实际{c.balance['cash'][t]:.0f}"
            )
    return violations
