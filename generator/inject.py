"""会计一致的异常注入引擎（对应方案 §5.4）。

初稿实现四类重点注入 + 现金流背离，合计五类；其余类型按需扩展。
每个注入只改“业务科目 + 对应现金流”，再交给 reconcile() 滚动货币资金、
用权益做平衡项，保证三条恒等式成立（§5.4）。

注入元数据（InjectionMeta）是注入样本金标准的**第一来源**（方案 §4.2）：
- 记录注入信号、注入起始年份、被改动的单元格（含改前/改后值与 source_record_id）、
  注入强度、期望被识别的目标异常；
- 恒等式校验结果随注入一并落库，恒等式不成立的样本直接判为无效；
- “注入后实际触发了哪些异常”由 generator.ground_truth.finalize_injection() 回填，
  以避免 generator 反向依赖 eval。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from generator.base import (
    CN,
    Company,
    _growth,
    reconcile,
    record_id_of,
    row_id_of,
)
from generator.invariants import check_identities

# 支持注入的信号（重点四类 + 现金流）
INJECTABLE = {
    "receivables_revenue_divergence",
    "inventory_cost_divergence",
    "goodwill_net_assets_pressure",
    "gross_net_margin_divergence",
    "cashflow_profit_divergence",
}

# 主动扰动 vs 现金流对应项 vs reconcile 派生
ROLE_PRIMARY = "primary"        # 目标扰动（决定异常是否成立）
ROLE_CASH_OFFSET = "cash_offset"  # 为守住现金流恒等式而做的对应调整
ROLE_DERIVED = "derived"        # reconcile 复算派生（毛利/总资产/权益/货币资金…）


@dataclass
class CellEdit:
    """单元格级改动记录，可直接与 source_record_id 对齐（方案 §5.3）。"""

    statement: str
    metric_key: str
    metric_name: str
    period: str
    row_id: Optional[str]
    source_record_id: Optional[str]
    value_before: float
    value_after: float
    delta: float
    role: str
    reason: str

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class InjectionMeta:
    """注入元数据 = 注入样本的独立 Ground Truth 载体（方案 §4.2、§5.4）。"""

    # ---- 注入意图（注入时即确定，不依赖任何规则复算）----
    signal_type: str
    strength: float
    injection_start_year: int
    injected_years: List[str] = field(default_factory=list)
    expected_signal_type: str = ""
    expected_periods: List[str] = field(default_factory=list)

    # ---- 改动明细 ----
    edits: List[CellEdit] = field(default_factory=list)          # primary + cash_offset
    derived_edits: List[CellEdit] = field(default_factory=list)   # reconcile 派生

    # ---- 恒等式校验（注入即校验，方案 §5.4）----
    identity_ok: bool = True
    identity_violations: List[str] = field(default_factory=list)

    # ---- 注入后实测（由 ground_truth.finalize_injection 回填）----
    finalized: bool = False
    baseline_triggered: List[dict] = field(default_factory=list)  # 注入前基底已触发
    post_triggered: List[dict] = field(default_factory=list)      # 注入后规则实际触发
    target_triggered: bool = False                                # 目标信号是否真被触发
    target_severity: Optional[str] = None                         # 注入后目标信号严重度
    target_measure_x: Optional[float] = None
    target_periods_rule: List[str] = field(default_factory=list)   # 规则复算出的期间
    periods_match_injection: Optional[bool] = None                 # 规则期间与注入年份是否一致
    composite_signals: List[str] = field(default_factory=list)     # 连带触发的其它信号
    valid: bool = True
    invalid_reasons: List[str] = field(default_factory=list)

    def primary_edits(self) -> List[CellEdit]:
        return [e for e in self.edits if e.role == ROLE_PRIMARY]

    def edited_record_ids(self) -> List[str]:
        return [e.source_record_id for e in self.edits if e.source_record_id]

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


def _edit(meta: InjectionMeta, statement: str, key: str, year: int,
          old_value: float, new_value: float, role: str, reason: str) -> None:
    meta.edits.append(CellEdit(
        statement=statement,
        metric_key=key,
        metric_name=CN.get(key, key),
        period=str(year),
        row_id=row_id_of(statement, key),
        source_record_id=record_id_of(statement, key, year),
        value_before=float(old_value),
        value_after=float(new_value),
        delta=float(new_value) - float(old_value),
        role=role,
        reason=reason,
    ))


def _copy(company: Company) -> Company:
    return Company(
        name=company.name, years=list(company.years),
        income={k: dict(v) for k, v in company.income.items()},
        balance={k: dict(v) for k, v in company.balance.items()},
        cashflow={k: dict(v) for k, v in company.cashflow.items()},
    )


def _collect_derived(base: Company, after: Company, meta: InjectionMeta,
                     tol: float = 1e-6) -> None:
    """记录 reconcile 造成的派生变化（毛利/货币资金/总资产/权益等）。"""
    explicit = {(e.statement, e.metric_key, e.period) for e in meta.edits}
    for statement in ("income", "balance", "cashflow"):
        b_tab, a_tab = base.table(statement), after.table(statement)
        for key, a_series in a_tab.items():
            b_series = b_tab.get(key, {})
            for year, a_val in a_series.items():
                b_val = b_series.get(year)
                if b_val is None:
                    continue
                if (statement, key, str(year)) in explicit:
                    continue
                if abs(float(a_val) - float(b_val)) <= tol:
                    continue
                meta.derived_edits.append(CellEdit(
                    statement=statement,
                    metric_key=key,
                    metric_name=CN.get(key, key),
                    period=str(year),
                    row_id=row_id_of(statement, key),
                    source_record_id=record_id_of(statement, key, year),
                    value_before=float(b_val),
                    value_after=float(a_val),
                    delta=float(a_val) - float(b_val),
                    role=ROLE_DERIVED,
                    reason="reconcile 复算派生（三表勾稽平衡项）",
                ))


def _prev_value(c: Company, statement: str, key: str, t: int) -> Optional[float]:
    """取「已经被本次注入更新过」的上年值。

    增速类注入必须相对**更新后**的上年值计算，否则第二年的异常会被自己抹平：
    若第 2 年按原始上年值乘目标增速，而上年值已被抬高，则实际增速回落到正常水平，
    「连续两年背离」的注入意图无法在数据中成立（元数据与数据不一致）。
    """
    idx = c.years.index(t)
    if idx == 0:
        return None
    return c.table(statement)[key][c.years[idx - 1]]


def inject(company: Company, signal_type: str, strength: float = 1.0
           ) -> tuple[Company, InjectionMeta]:
    """对 company 做会计一致注入，返回（注入后公司, 注入元数据）。

    注入元数据即该样本的注入侧 Ground Truth：包含目标信号、注入年份、
    改动单元格的改前/改后值、以及三条恒等式的校验结果。
    """
    if signal_type not in INJECTABLE:
        raise ValueError(f"暂不支持注入类型：{signal_type}")

    years = list(company.years[-2:])
    meta = InjectionMeta(
        signal_type=signal_type,
        strength=strength,
        injection_start_year=years[0],
        injected_years=[str(y) for y in years],
        expected_signal_type=signal_type,
        expected_periods=[str(y) for y in years],
    )
    yrs = years
    c = _copy(company)

    if signal_type == "receivables_revenue_divergence":
        for t in yrs:
            rev_g = _growth(c.income["revenue"], c.years, t)
            target_g = rev_g + 0.25 * strength
            old = c.balance["accounts_receivable"][t]
            prev = _prev_value(c, "balance", "accounts_receivable", t)
            new = (prev * (1 + target_g)) if prev is not None else old * (1 + target_g)
            c.balance["accounts_receivable"][t] = new
            _edit(meta, "balance", "accounts_receivable", t, old, new, ROLE_PRIMARY,
                  f"应收账款增速抬高至营业收入增速+{0.25*strength:.0%}")
            d = new - old
            old_cfo = c.cashflow["cfo"][t]
            c.cashflow["cfo"][t] = old_cfo - d
            _edit(meta, "cashflow", "cfo", t, old_cfo, c.cashflow["cfo"][t],
                  ROLE_CASH_OFFSET, "应收增加未收现，等额冲减经营现金流")

    elif signal_type == "inventory_cost_divergence":
        for t in yrs:
            cogs_g = _growth(c.income["cogs"], c.years, t)
            target_g = cogs_g + 0.25 * strength
            old = c.balance["inventory"][t]
            prev = _prev_value(c, "balance", "inventory", t)
            new = (prev * (1 + target_g)) if prev is not None else old * (1 + target_g)
            c.balance["inventory"][t] = new
            _edit(meta, "balance", "inventory", t, old, new, ROLE_PRIMARY,
                  f"存货增速抬高至营业成本增速+{0.25*strength:.0%}")
            d = new - old
            old_cfo = c.cashflow["cfo"][t]
            c.cashflow["cfo"][t] = old_cfo - d
            _edit(meta, "cashflow", "cfo", t, old_cfo, c.cashflow["cfo"][t],
                  ROLE_CASH_OFFSET, "存货占用资金，等额冲减经营现金流")

    elif signal_type == "goodwill_net_assets_pressure":
        for t in yrs:
            old = c.balance["goodwill"][t]
            new = c.balance["equity"][t] * (0.25 * strength)
            c.balance["goodwill"][t] = new
            _edit(meta, "balance", "goodwill", t, old, new, ROLE_PRIMARY,
                  f"商誉抬升至所有者权益的{0.25*strength:.0%}")
            d = new - old
            old_cfi = c.cashflow["cfi"][t]
            c.cashflow["cfi"][t] = old_cfi - d  # 现金收购 → 投资现金流出
            _edit(meta, "cashflow", "cfi", t, old_cfi, c.cashflow["cfi"][t],
                  ROLE_CASH_OFFSET, "现金收购形成商誉，等额计入投资活动现金流出")

    elif signal_type == "gross_net_margin_divergence":
        # 该信号看的是「毛利率变动 - 净利率变动」的逐年变化，因此扰动必须逐年递进，
        # 否则第 2 年的毛利率/净利率与第 1 年同比例，变动量归零、异常不成立。
        for step, t in enumerate(yrs, start=1):
            # 毛利率逐年抬升：营业成本按 (1-2%·strength)^step 递进下调
            old_cogs = c.income["cogs"][t]
            new_cogs = old_cogs * (1 - 0.02 * strength) ** step
            c.income["cogs"][t] = new_cogs
            _edit(meta, "income", "cogs", t, old_cogs, new_cogs, ROLE_PRIMARY,
                  f"营业成本按第{step}年累计下调至{(1-0.02*strength)**step:.4f}倍，抬升毛利率")
            # 净利率逐年下滑：净利润按 (1-30%·strength)^step 递进下调
            old_np = c.income["net_profit"][t]
            new_np = old_np * (1 - 0.30 * strength) ** step
            c.income["net_profit"][t] = new_np
            _edit(meta, "income", "net_profit", t, old_np, new_np, ROLE_PRIMARY,
                  f"净利润按第{step}年累计下调至{(1-0.30*strength)**step:.4f}倍，压低净利率")
            d = old_np - new_np  # 现金费用（净利下滑的现金对应项）
            old_cfo = c.cashflow["cfo"][t]
            c.cashflow["cfo"][t] = old_cfo - d
            _edit(meta, "cashflow", "cfo", t, old_cfo, c.cashflow["cfo"][t],
                  ROLE_CASH_OFFSET, "净利下滑对应现金费用支出，冲减经营现金流")

    elif signal_type == "cashflow_profit_divergence":
        for t in yrs:
            old = c.cashflow["cfo"][t]
            # 经营现金流/净利润随强度下降：strength=1→0.30，strength=2→0.10（更低=更异常）
            target_ratio = max(0.05, 0.5 - 0.20 * strength)
            new = c.income["net_profit"][t] * target_ratio
            c.cashflow["cfo"][t] = new
            _edit(meta, "cashflow", "cfo", t, old, new, ROLE_PRIMARY,
                  f"经营现金流/净利润压低至{target_ratio:.2f}")

    out = reconcile(c)
    _collect_derived(company, out, meta)

    violations = check_identities(out)
    meta.identity_violations = violations
    meta.identity_ok = not violations
    if violations:
        meta.valid = False
        meta.invalid_reasons.append("identity_violation")
    return out, meta
