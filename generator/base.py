"""清洁基底公司与文本序列化（对应方案 §5.4 三表一致基底）。

Company 内部以英文 metric_key 存数；对外文本与评估索引统一用中文表头，
保证模型回显的中文名能直接回表比对（方案 §5.3 单元格级证据）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

# 中文表头 <-> 英文 key 映射（文本里出现、评估索引也用中文）
CN = {
    "revenue": "营业收入",
    "cogs": "营业成本",
    "gross_profit": "毛利",
    "net_profit": "净利润",
    "nonrecurring": "非经常性损益",
    "cash": "货币资金",
    "accounts_receivable": "应收账款",
    "inventory": "存货",
    "goodwill": "商誉",
    "fixed_assets": "固定资产",
    "current_assets": "流动资产",
    "current_liabilities": "流动负债",
    "short_borrow": "短期借款",
    "non_current_liabilities": "非流动负债",
    "equity": "所有者权益",
    "retained": "未分配利润",
    "total_assets": "总资产",
    "cfo": "经营活动现金流量净额",
    "cfi": "投资活动现金流量净额",
    "cff": "筹资活动现金流量净额",
}


@dataclass
class Company:
    name: str
    years: List[int]
    income: Dict[str, Dict[int, float]] = field(default_factory=dict)
    balance: Dict[str, Dict[int, float]] = field(default_factory=dict)
    cashflow: Dict[str, Dict[int, float]] = field(default_factory=dict)

    def get(self, statement: str, key: str, year: int) -> float:
        table = {"income": self.income, "balance": self.balance, "cashflow": self.cashflow}[statement]
        return table[key][year]


def _growth(series: Dict[int, float], years: List[int], t: int) -> float:
    i = years.index(t)
    if i == 0:
        return 0.0
    prev = series[years[i - 1]]
    cur = series[t]
    if prev == 0:
        return 0.0
    return (cur - prev) / abs(prev)


def make_clean_company(name: str = "示例制造公司", years: List[int] | None = None) -> Company:
    """构造一个八类异常均不触发的清洁制造业公司，三表勾稽一致。"""
    years = years or [2022, 2023, 2024]
    rev = [1000.0, 1120.0, 1254.0]
    cogs = [800.0, 896.0, 1003.2]
    np_ = [80.0, 90.0, 100.0]
    nr = [5.0, 6.0, 7.0]
    cash = [200.0, 210.0, 220.0]
    ar = [150.0, 168.0, 188.0]
    inv = [120.0, 134.0, 150.0]
    gw = [0.0, 0.0, 0.0]
    fa = [300.0, 320.0, 340.0]
    cl = [400.0, 430.0, 470.0]
    sb = [100.0, 120.0, 150.0]
    ncl = [150.0, 160.0, 170.0]
    ret = [100.0, 140.0, 200.0]
    cfo = [60.0, 60.0, 60.0]
    cfi = [-40.0, -40.0, -40.0]
    cff = [-10.0, -10.0, -10.0]

    def d(values):
        return {y: v for y, v in zip(years, values)}

    c = Company(name=name, years=list(years))
    c.income = {
        "revenue": d(rev), "cogs": d(cogs), "net_profit": d(np_), "nonrecurring": d(nr),
    }
    c.balance = {
        "cash": d(cash), "accounts_receivable": d(ar), "inventory": d(inv), "goodwill": d(gw),
        "fixed_assets": d(fa), "current_liabilities": d(cl), "short_borrow": d(sb),
        "non_current_liabilities": d(ncl), "retained": d(ret),
    }
    c.cashflow = {"cfo": d(cfo), "cfi": d(cfi), "cff": d(cff)}
    return reconcile(c)


def reconcile(c: Company) -> Company:
    """复算派生科目、按现金流恒等式滚动货币资金、用权益做平衡项（方案 §5.4）。"""
    y = c.years
    # 派生：毛利
    for t in y:
        c.income.setdefault("gross_profit", {})[t] = c.income["revenue"][t] - c.income["cogs"][t]
    # 派生：流动资产 / 非流动资产 / 总资产
    for t in y:
        ca = c.balance["cash"][t] + c.balance["accounts_receivable"][t] + c.balance["inventory"][t]
        c.balance["current_assets"] = c.balance.get("current_assets", {})
        c.balance["current_assets"][t] = ca
        nca = c.balance["goodwill"][t] + c.balance["fixed_assets"][t]
        c.balance["non_current_assets"] = c.balance.get("non_current_assets", {})
        c.balance["non_current_assets"][t] = nca
        ta = ca + nca
        c.balance["total_assets"] = c.balance.get("total_assets", {})
        c.balance["total_assets"][t] = ta
        eq = ta - c.balance["current_liabilities"][t] - c.balance["non_current_liabilities"][t]
        c.balance.setdefault("equity", {})[t] = eq
    # 现金流恒等式滚动货币资金（首年货币资金作为期初，不做滚动）
    for i, t in enumerate(y):
        if i == 0:
            continue
        prev = y[i - 1]
        new_cash = (
            c.balance["cash"][prev]
            + c.cashflow["cfo"][t]
            + c.cashflow["cfi"][t]
            + c.cashflow["cff"][t]
        )
        c.balance["cash"][t] = new_cash
        # 现金变动后重算流动资产/总资产/权益
        ca = new_cash + c.balance["accounts_receivable"][t] + c.balance["inventory"][t]
        c.balance["current_assets"][t] = ca
        ta = ca + c.balance["non_current_assets"][t]
        c.balance["total_assets"][t] = ta
        c.balance["equity"][t] = ta - c.balance["current_liabilities"][t] - c.balance["non_current_liabilities"][t]
    return c


def company_to_text(c: Company) -> str:
    """生成与扫描 prompt 一致的 CSV 文本（方案 §3.5 输入形态）。"""
    y = c.years
    hdr = "年度," + ",".join(str(t) for t in y)

    def row(cn_key, key, table):
        vals = ",".join(f"{table[key][t]:.0f}" if abs(table[key][t]) < 1e9 else f"{table[key][t]:.0f}" for t in y)
        return f"{CN[cn_key]},{vals}"

    lines = [f"公司：{c.name}", "单位：元", "", "利润表：", hdr]
    lines.append(row("revenue", "revenue", c.income))
    lines.append(row("cogs", "cogs", c.income))
    lines.append(row("gross_profit", "gross_profit", c.income))
    lines.append(row("net_profit", "net_profit", c.income))
    lines.append(row("nonrecurring", "nonrecurring", c.income))
    lines.append("")
    lines.append("资产负债表：")
    lines.append(hdr)
    for cn_key, key in [
        ("cash", "cash"), ("accounts_receivable", "accounts_receivable"), ("inventory", "inventory"),
        ("goodwill", "goodwill"), ("current_assets", "current_assets"),
        ("current_liabilities", "current_liabilities"), ("short_borrow", "short_borrow"),
        ("total_assets", "total_assets"), ("equity", "equity"), ("retained", "retained"),
    ]:
        lines.append(row(cn_key, key, c.balance))
    lines.append("")
    lines.append("现金流量表：")
    lines.append(hdr)
    lines.append(row("cfo", "cfo", c.cashflow))
    lines.append(row("cfi", "cfi", c.cashflow))
    lines.append(row("cff", "cff", c.cashflow))
    return "\n".join(lines)


def build_value_index(c: Company) -> Dict[tuple, float]:
    """(中文表头, 年度) -> 数值，供 D1/D3 回表比对。"""
    idx: Dict[tuple, float] = {}
    for key, cn in CN.items():
        for table_name, table in (("income", c.income), ("balance", c.balance), ("cashflow", c.cashflow)):
            if key in table:
                for t in c.years:
                    idx[(cn, t)] = table[key][t]
    return idx
