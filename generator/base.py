"""清洁基底公司与文本序列化（对应方案 §5.4 三表一致基底）。

Company 内部以英文 metric_key 存数；对外文本与评估索引统一用中文表头，
保证模型回显的中文名能直接回表比对（方案 §5.3 单元格级证据）。

单元格级证据（方案 §5.3）：
- 每个 (报表, 科目, 年度) 单元格拥有稳定的 source_record_id，格式 `<row_id>_<年度>`，
  例如 `IS_R02_2023`；row_id 由报表代码 + 该科目在报表中的固定行号构成；
- 行号布局由 ROW_LAYOUT 冻结，company_to_text() 与 build_record_index() 共用同一布局，
  保证「模型看到的行号」与「评估器回表的行号」严格一致；
- D3 证据可追溯性据此做严格回表校验，而非仅检查科目名与年份是否存在。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
    "non_current_assets": "非流动资产",
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


# ---- 报表元信息与冻结行布局（source_record_id 的稳定性来源，方案 §5.3）----
STATEMENT_META: Dict[str, Dict[str, str]] = {
    "income": {"code": "IS", "cn": "利润表", "source_file": "income_statement.csv"},
    "balance": {"code": "BS", "cn": "资产负债表", "source_file": "balance_sheet.csv"},
    "cashflow": {"code": "CF", "cn": "现金流量表", "source_file": "cash_flow.csv"},
}

# 每张报表的固定行顺序；表头占第 1 行，故数据行号 = 序号 + 2。
# 该布局一经冻结不得随意调整，否则历史结果中的 source_record_id 会失效。
# 新增科目只能**追加到末尾**（已有行号不变），因此 R12~R14 的语义顺序看起来不连贯，
# 但这是为了 source_record_id 的向后兼容，属于有意取舍。
ROW_LAYOUT: Dict[str, List[str]] = {
    "income": ["revenue", "cogs", "gross_profit", "net_profit", "nonrecurring"],
    "balance": [
        "cash", "accounts_receivable", "inventory", "goodwill", "current_assets",
        "current_liabilities", "short_borrow", "total_assets", "equity", "retained",
        # 追加：不披露这三行时，模型可见的表格里「总资产 = 负债 + 权益」无法核对
        # （总资产含固定资产、负债含非流动负债），会诱发无意义的"数据矛盾"误报。
        "fixed_assets", "non_current_assets", "non_current_liabilities",
    ],
    "cashflow": ["cfo", "cfi", "cff"],
}

UNIT = "元"


@dataclass(frozen=True)
class SourceRecord:
    """单元格级证据记录（方案 §5.3），供 D1/D3 严格回表。"""

    record_id: str      # 如 IS_R02_2023
    row_id: str         # 如 IS_R02
    statement: str      # income / balance / cashflow
    statement_cn: str   # 利润表 / 资产负债表 / 现金流量表
    source_file: str    # 如 income_statement.csv
    source_row: int     # 报表内固定行号（表头为第 1 行）
    source_column: str  # 年度，如 "2023"
    metric_key: str     # 英文 key
    metric_name: str    # 中文科目名
    period: str         # 年度，如 "2023"
    value: float
    unit: str = UNIT


def row_id_of(statement: str, metric_key: str) -> Optional[str]:
    """返回某科目在报表中的稳定 row_id；不在冻结布局中返回 None。"""
    layout = ROW_LAYOUT.get(statement, [])
    if metric_key not in layout:
        return None
    row_no = layout.index(metric_key) + 2  # 表头占第 1 行
    return f"{STATEMENT_META[statement]['code']}_R{row_no:02d}"


def record_id_of(statement: str, metric_key: str, period) -> Optional[str]:
    """返回单元格级 source_record_id，如 IS_R02_2023。"""
    rid = row_id_of(statement, metric_key)
    return f"{rid}_{period}" if rid else None


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

    def table(self, statement: str) -> Dict[str, Dict[int, float]]:
        return {"income": self.income, "balance": self.balance, "cashflow": self.cashflow}[statement]


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
    """生成与扫描 prompt 一致的 CSV 文本（方案 §3.5 输入形态）。

    每行带稳定 row_id，模型可据此回填 source_record_id / source_row / source_column，
    使 D3 证据可追溯性成为可测量指标（方案 §5.3）。
    """
    y = c.years
    lines = [
        f"公司：{c.name}",
        f"单位：{UNIT}",
        "证据规则：source_record_id = <row_id>_<年度>（例如 IS_R02_2023）；"
        "source_row 取 row_id 中的数字；source_column 与 period 取年度。",
    ]
    for statement in ("income", "balance", "cashflow"):
        meta = STATEMENT_META[statement]
        table = c.table(statement)
        lines.append("")
        lines.append(
            f"{meta['cn']}（statement={statement}, source_file={meta['source_file']}）："
        )
        lines.append("row_id,source_row,科目," + ",".join(str(t) for t in y))
        for metric_key in ROW_LAYOUT[statement]:
            if metric_key not in table:
                continue
            rid = row_id_of(statement, metric_key)
            row_no = int(rid.split("_R")[1])
            vals = ",".join(f"{table[metric_key][t]:.0f}" for t in y)
            lines.append(f"{rid},{row_no},{CN[metric_key]},{vals}")
    return "\n".join(lines)


def build_record_index(c: Company) -> Dict[str, SourceRecord]:
    """source_record_id -> SourceRecord，供 D1/D3 严格回表（方案 §5.3）。"""
    idx: Dict[str, SourceRecord] = {}
    for statement in ("income", "balance", "cashflow"):
        meta = STATEMENT_META[statement]
        table = c.table(statement)
        for metric_key in ROW_LAYOUT[statement]:
            if metric_key not in table:
                continue
            rid = row_id_of(statement, metric_key)
            row_no = int(rid.split("_R")[1])
            for t in c.years:
                if t not in table[metric_key]:
                    continue
                record_id = f"{rid}_{t}"
                idx[record_id] = SourceRecord(
                    record_id=record_id,
                    row_id=rid,
                    statement=statement,
                    statement_cn=meta["cn"],
                    source_file=meta["source_file"],
                    source_row=row_no,
                    source_column=str(t),
                    metric_key=metric_key,
                    metric_name=CN[metric_key],
                    period=str(t),
                    value=float(table[metric_key][t]),
                )
    return idx


def build_lookup_index(c: Company) -> Dict[Tuple[str, str], SourceRecord]:
    """(科目标识, 年度) -> SourceRecord。

    科目标识同时接受中文科目名与英文 metric_key，便于在模型未给出
    source_record_id 时做降级定位（该降级只用于 D1，不计入 D3 严格可追溯）。
    """
    out: Dict[Tuple[str, str], SourceRecord] = {}
    for rec in build_record_index(c).values():
        out[(rec.metric_name, rec.period)] = rec
        out[(rec.metric_key, rec.period)] = rec
    return out


def build_value_index(c: Company) -> Dict[tuple, float]:
    """(中文表头, 年度) -> 数值。保留兼容旧调用；新代码请用 build_record_index。"""
    idx: Dict[tuple, float] = {}
    for key, cn in CN.items():
        for table_name, table in (("income", c.income), ("balance", c.balance), ("cashflow", c.cashflow)):
            if key in table:
                for t in c.years:
                    if t in table[key]:
                        idx[(cn, t)] = table[key][t]
    return idx
