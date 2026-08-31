"""D2 公式与计算正确性 + 安全公式注册表（对应方案 §5.2、§6.1）。

**安全约束**：评估器只调用白名单安全函数（本模块内定义），绝不 eval / exec
模型生成的表达式，也不从模型文本反解公式。

**严格 D2（本轮修复重点）**：按方案 §5.2 的五步依次检查，任一步失败即判 fail，
不再"生成多个候选结果、命中任意一个就算正确"：

1. `formula_known`   —— formula_id 必须存在于 config/formula_registry.yaml；
2. `signal_match`    —— formula_id 必须登记在该 signal_type 的允许列表内；
3. `operands_ok`     —— fact_basis 必须引用注册表要求的全部操作数科目（分子/分母齐备）；
4. `periods_ok`      —— 期间必须满足 period_mode（single 单年 / pair 需要 t-1 与 t）；
5. `arithmetic_ok`   —— 用注册表安全函数 + **公司原始数据**复算，与 reported_result 比对。

单位约定：注册表统一以小数表示比率（0.25 = 25 个百分点）。模型若以百分数上报
（25 而非 0.25），按 1/100 归一后再比对，并置 `scale_adjusted` 标记留痕；
这是同一公式的单位换算，不是"换一个公式定义再试一次"。

**归一必须设门槛**：无条件允许 ÷100 会退化成第二次机会 —— 当复算值接近 0 时，
任意上报值 ÷100 都落进绝对容差里，D2 被系统性抬高。因此只有「上报值量级本身
像百分数」且「复算值显著非零」时才允许归一，见 SCALE_MIN_* 常量。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.formulas import load_registry
from app.schema import AnomalyCard
from generator.base import CN

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")

# 每个 metric_key 所属报表
METRIC_LOC = {
    "revenue": "income", "cogs": "income", "gross_profit": "income",
    "net_profit": "income", "nonrecurring": "income",
    "cash": "balance", "accounts_receivable": "balance", "inventory": "balance",
    "goodwill": "balance", "fixed_assets": "balance", "current_assets": "balance",
    "current_liabilities": "balance", "short_borrow": "balance",
    "non_current_liabilities": "balance", "non_current_assets": "balance",
    "equity": "balance", "retained": "balance", "total_assets": "balance",
    "cfo": "cashflow", "cfi": "cashflow", "cff": "cashflow",
}

# 模型常用的同义 metric_key → 规范 key（只做命名归一，不改变语义）
_KEY_ALIASES = {
    "operating_cash_flow": "cfo", "ocf": "cfo", "net_operating_cash_flow": "cfo",
    "cash_flow_from_operations": "cfo",
    "accounts_receivables": "accounts_receivable", "receivables": "accounts_receivable",
    "cost_of_goods_sold": "cogs", "operating_cost": "cogs",
    "netprofit": "net_profit", "net_income": "net_profit",
    "shareholders_equity": "equity", "owners_equity": "equity", "net_assets": "equity",
    "short_term_borrowing": "short_borrow", "short_term_loans": "short_borrow",
    "monetary_funds": "cash", "cash_and_equivalents": "cash",
}

_CN_TO_KEY = {cn: key for key, cn in CN.items()}

D2_CHECKS = ("formula_known", "signal_match", "operands_ok", "periods_ok", "arithmetic_ok")

# 算术比对容差。注册表所有公式的输出都是无量纲小数（比率 / 小数化的百分点）。
# REL_TOL：相对容差，吸收模型对自身结果的有效数字取舍。
# ABS_TOL：绝对容差下限 = 小数保留 2 位时的半个末位（0.005，即 0.5 个百分点）。
#          原值 0.01 过宽：它等于 1 个百分点，会让「复算值接近 0」时几乎任何
#          |rep| < 1 的错误数值都判对，是 D2 系统性虚高的一条通道。
REL_TOL = 0.05
ABS_TOL = 0.005

# 百分数单位归一（÷100）的准入门槛。只有同时满足下面两条才允许归一后再比对：
#   1. 上报值量级本身像百分数（|rep| >= 1），否则「小数写成小数」谈不上单位问题；
#   2. 复算值显著非零（|expected| >= SCALE_MIN_EXPECTED），否则 ÷100 会把任意
#      数值压到 0 附近，靠 ABS_TOL 蒙对 —— 那是第二次机会，不是单位换算。
# 这两条门槛是为了避免 §5.2 严禁的「多候选放水」以单位归一的形式复活。
SCALE_MIN_REPORTED = 1.0
SCALE_MIN_EXPECTED = 0.005


def _registry() -> dict:
    """注册表读取统一走 app.formulas，保证 prompt 与评估器看到同一份定义。"""
    return load_registry()


def registry_formula_ids() -> List[str]:
    """供 prompt 与测试使用：注册表中登记的全部 formula_id。"""
    return list(_registry().keys())


def formula_ids_for_signal(signal_type: str) -> List[str]:
    return [fid for fid, spec in _registry().items()
            if signal_type in (spec.get("signal_types") or [])]


# ---- 白名单安全函数 ----
def ratio(a: float, b: float) -> Optional[float]:
    return a / b if b not in (0, 0.0) else None


def abs_ratio(a: float, b: float) -> Optional[float]:
    return (abs(a) / abs(b)) if b not in (0, 0.0) else None


def growth(series: Dict[int, float], years: List[int], t: int) -> Optional[float]:
    if t not in years:
        return None
    i = years.index(t)
    if i == 0:
        return None
    prev, cur = series.get(years[i - 1]), series.get(t)
    if prev is None or cur is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def growth_diff_pp(a: Dict[int, float], b: Dict[int, float],
                   years: List[int], t: int) -> Optional[float]:
    ga, gb = growth(a, years, t), growth(b, years, t)
    return None if ga is None or gb is None else ga - gb


def margin_gap_pp(rev: Dict[int, float], cogs: Dict[int, float], netp: Dict[int, float],
                  years: List[int], t: int) -> Optional[float]:
    """(毛利率_t-毛利率_{t-1}) - (净利率_t-净利率_{t-1})，小数。"""
    if t not in years:
        return None
    i = years.index(t)
    if i == 0:
        return None
    p = years[i - 1]

    def gm_at(yr):
        r = rev.get(yr)
        return None if not r else (r - cogs[yr]) / r

    def nm_at(yr):
        r = rev.get(yr)
        return None if not r else netp[yr] / r

    vals = [gm_at(t), gm_at(p), nm_at(t), nm_at(p)]
    if any(v is None for v in vals):
        return None
    return (vals[0] - vals[1]) - (vals[2] - vals[3])


def yoy_growth(series: Dict[int, float], years: List[int], t: int) -> Optional[float]:
    return growth(series, years, t)


_SAFE_FUNCS = {
    "ratio": ratio, "abs_ratio": abs_ratio, "growth": growth,
    "growth_diff_pp": growth_diff_pp, "margin_gap_pp": margin_gap_pp,
    "yoy_growth": yoy_growth,
}


def _series(company, metric_key: str) -> Optional[Dict[int, float]]:
    loc = METRIC_LOC.get(metric_key)
    if loc is None:
        return None
    table = company.table(loc) if hasattr(company, "table") else {
        "income": company.income, "balance": company.balance, "cashflow": company.cashflow,
    }[loc]
    return table.get(metric_key)


def recompute(formula_id: str, company, t: int) -> Optional[float]:
    """用公司**原始数据**复算某公式在年度 t 的结果（D2 第 5 步）。

    只走注册表登记的白名单函数；缺科目/除零/首年无同比时返回 None。
    """
    spec = _registry().get(formula_id)
    if spec is None or not spec.get("available", True):
        return None
    func = _SAFE_FUNCS.get(spec["func"])
    if func is None:
        return None
    inputs: List[str] = spec["inputs"]
    series = [_series(company, k) for k in inputs]
    if any(s is None for s in series):
        return None
    years = company.years

    if spec["func"] in ("ratio", "abs_ratio"):
        a, b = series[0].get(t), series[1].get(t)
        if a is None or b is None:
            return None
        return func(a, b)
    if spec["func"] == "growth_diff_pp":
        return growth_diff_pp(series[0], series[1], years, t)
    if spec["func"] == "margin_gap_pp":
        return margin_gap_pp(series[0], series[1], series[2], years, t)
    if spec["func"] in ("yoy_growth", "growth"):
        return func(series[0], years, t)
    return None


# ---------------- D2 严格评估 ----------------
@dataclass
class D2Result:
    card_index: int
    signal_type: str
    formula_id: Optional[str] = None
    status: str = "not_applicable"     # pass / fail / not_applicable
    checks: Dict[str, Optional[bool]] = field(default_factory=dict)
    period_used: Optional[int] = None
    expected: Optional[float] = None
    reported: Optional[float] = None
    scale_adjusted: bool = False
    issues: List[str] = field(default_factory=list)


def _cited_metric_keys(card: AnomalyCard) -> set:
    """卡片 fact_basis 引用到的规范化 metric_key 集合。"""
    keys = set()
    for f in card.fact_basis:
        raw = (f.metric_key or "").strip()
        key = _KEY_ALIASES.get(raw, raw)
        if not key and f.metric_name:
            key = _CN_TO_KEY.get(str(f.metric_name).strip(), "")
        if key:
            keys.add(key)
    return keys


def _cited_periods(card: AnomalyCard, metric_key: str) -> set:
    """卡片为某科目引用到的年度集合。"""
    out = set()
    for f in card.fact_basis:
        raw = (f.metric_key or "").strip()
        key = _KEY_ALIASES.get(raw, raw) or _CN_TO_KEY.get(
            str(f.metric_name or "").strip(), "")
        if key == metric_key and f.period is not None:
            text = str(f.period).strip()
            if text.isdigit():
                out.add(int(text))
    return out


def _target_period(card: AnomalyCard, company) -> Optional[int]:
    """确定复算年度：取卡片 periods 与公司年度的交集中的最大年（最新期间）。"""
    cand = []
    for p in card.periods:
        text = str(p).strip()
        if text.isdigit() and int(text) in company.years:
            cand.append(int(text))
    if not cand:
        for f in card.fact_basis:
            text = str(f.period or "").strip()
            if text.isdigit() and int(text) in company.years:
                cand.append(int(text))
    return max(cand) if cand else None


def evaluate_card_d2(card: AnomalyCard, company, index: int = 0) -> D2Result:
    """对单张卡片做方案 §5.2 五步严格 D2 校验。"""
    st = card.signal_type.value if hasattr(card.signal_type, "value") else str(card.signal_type)
    res = D2Result(card_index=index, signal_type=st)
    calc = getattr(card, "calculation", None)
    fid = (getattr(calc, "formula_id", None) or "").strip() if calc else ""
    rep = getattr(calc, "reported_result", None) if calc else None
    res.formula_id = fid or None
    res.reported = rep

    # other 类不进入主指标（方案 §3.4）
    if st == "other":
        res.status = "not_applicable"
        res.issues.append("other 类不计入 D2")
        return res
    if calc is None or rep is None:
        res.status = "not_applicable"
        res.issues.append("未提供 calculation.reported_result")
        return res

    reg = _registry()

    # 步骤 1：formula_id 必须来自注册表
    res.checks["formula_known"] = fid in reg
    if not res.checks["formula_known"]:
        res.status = "fail"
        res.issues.append(f"formula_id 不在注册表：{fid or '(空)'}")
        return res
    spec = reg[fid]

    # 科目在当前数据 schema 中不存在（如减值损失）→ 不适用，不计入分母
    if not spec.get("available", True):
        res.status = "not_applicable"
        res.issues.append(f"{fid} 所需科目在当前数据中不存在")
        return res

    # 步骤 2：formula_id 与 signal_type 是否匹配
    allowed = spec.get("signal_types") or []
    res.checks["signal_match"] = st in allowed
    if not res.checks["signal_match"]:
        res.status = "fail"
        res.issues.append(f"公式 {fid} 不适用于 {st}（允许：{allowed}）")
        return res

    # 步骤 3：操作数科目（分子/分母）是否齐备
    required = set(spec["inputs"])
    cited = _cited_metric_keys(card)
    missing = required - cited
    res.checks["operands_ok"] = not missing
    if missing:
        res.status = "fail"
        res.issues.append(f"fact_basis 缺少操作数科目：{sorted(missing)}")
        return res

    # 步骤 4：期间是否满足 period_mode
    t = _target_period(card, company)
    res.period_used = t
    if t is None:
        res.checks["periods_ok"] = False
        res.status = "fail"
        res.issues.append("无法确定有效复算年度")
        return res
    mode = spec.get("period_mode", "single")
    periods_ok = True
    if mode == "pair":
        i = company.years.index(t)
        if i == 0:
            periods_ok = False
            res.issues.append("同比类公式引用了首年，无上年可比")
        else:
            prev = company.years[i - 1]
            for key in spec["inputs"]:
                cited_years = _cited_periods(card, key)
                if not {t, prev} <= cited_years:
                    periods_ok = False
                    res.issues.append(
                        f"同比类公式需引用 {prev} 与 {t} 两年的 {key}，实际引用 {sorted(cited_years)}")
    else:
        for key in spec["inputs"]:
            if t not in _cited_periods(card, key):
                periods_ok = False
                res.issues.append(f"单期公式需引用 {t} 年的 {key}")
    res.checks["periods_ok"] = periods_ok
    if not periods_ok:
        res.status = "fail"
        return res

    # 步骤 5：用注册表安全函数 + 公司原始数据复算并比对
    expected = recompute(fid, company, t)
    res.expected = expected
    if expected is None:
        res.checks["arithmetic_ok"] = None
        res.status = "not_applicable"
        res.issues.append("复算不可用（缺科目/除零/首年无同比）")
        return res

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= max(ABS_TOL, REL_TOL * abs(b))

    # 是否允许尝试百分数单位归一（门槛见文件头部常量注释）
    scale_allowed = abs(rep) >= SCALE_MIN_REPORTED and abs(expected) >= SCALE_MIN_EXPECTED

    if close(rep, expected):
        res.checks["arithmetic_ok"] = True
    elif scale_allowed and close(rep / 100.0, expected):
        # 同一公式的百分数单位写法，做单位归一并留痕
        res.checks["arithmetic_ok"] = True
        res.scale_adjusted = True
    else:
        res.checks["arithmetic_ok"] = False
        res.issues.append(f"算术不符：上报 {rep}，复算 {expected}")
        if not scale_allowed and close(rep / 100.0, expected):
            # 明确记录「归一后数值接近但不予采信」，避免以后有人误判为漏判
            res.issues.append(
                "÷100 后数值接近，但不满足百分数归一门槛"
                f"（|上报|={abs(rep):.4g} 需≥{SCALE_MIN_REPORTED}，"
                f"|复算|={abs(expected):.4g} 需≥{SCALE_MIN_EXPECTED}），按算术错误计")

    res.status = "pass" if res.checks["arithmetic_ok"] else "fail"
    return res


def evaluate_d2(cards: List[AnomalyCard], company=None) -> Dict:
    """汇总 D2：严格通过率 + 逐步骤通过率，均给出分子分母（方案 §6.4）。"""
    results = [evaluate_card_d2(c, company, i) for i, c in enumerate(cards)]
    scored = [r for r in results if r.status in ("pass", "fail")]
    hits = sum(1 for r in scored if r.status == "pass")
    step_hits = {k: sum(1 for r in scored if r.checks.get(k) is True) for k in D2_CHECKS}
    step_total = {k: sum(1 for r in scored if r.checks.get(k) is not None) for k in D2_CHECKS}
    return {
        "d2_hits": hits,
        "d2_total": len(scored),
        "d2_rate": (hits / len(scored)) if scored else None,
        "d2_not_applicable": sum(1 for r in results if r.status == "not_applicable"),
        "d2_scale_adjusted": sum(1 for r in scored if r.scale_adjusted),
        "d2_step_hits": step_hits,
        "d2_step_total": step_total,
        "d2_step_rates": {
            k: (step_hits[k] / step_total[k]) if step_total[k] else None for k in D2_CHECKS
        },
        "details": [
            {
                "card_index": r.card_index, "signal_type": r.signal_type,
                "formula_id": r.formula_id, "status": r.status, "checks": r.checks,
                "period_used": r.period_used, "expected": r.expected,
                "reported": r.reported, "scale_adjusted": r.scale_adjusted,
                "issues": r.issues,
            }
            for r in results
        ],
    }
