"""安全公式求值（对应方案 §5.2）。

评估器只调用白名单安全函数，绝不 eval 模型生成的表达式。
func 名来自 config/formula_registry.yaml；recompute() 用公司真实数据复算，供 D2 比对。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import yaml

from app.schema import AnomalyCard

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")

# 每个 metric_key 所属报表
METRIC_LOC = {
    "revenue": "income", "cogs": "income", "gross_profit": "income",
    "net_profit": "income", "nonrecurring": "income",
    "cash": "balance", "accounts_receivable": "balance", "inventory": "balance",
    "goodwill": "balance", "fixed_assets": "balance", "current_assets": "balance",
    "current_liabilities": "balance", "short_borrow": "balance",
    "non_current_liabilities": "balance", "equity": "balance", "retained": "balance",
    "total_assets": "balance",
    "cfo": "cashflow", "cfi": "cashflow", "cff": "cashflow",
}


def _registry() -> dict:
    with open(os.path.join(_CONFIG, "formula_registry.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["formulas"]


# ---- 白名单安全函数 ----
def ratio(a: float, b: float) -> Optional[float]:
    return a / b if b not in (0, 0.0) else None


def abs_ratio(a: float, b: float) -> Optional[float]:
    return (abs(a) / abs(b)) if b not in (0, 0.0) else None


def growth(series: Dict[int, float], years: List[int], t: int) -> float:
    i = years.index(t)
    if i == 0:
        return 0.0
    prev, cur = series[years[i - 1]], series[t]
    return (cur - prev) / abs(prev) if prev != 0 else 0.0


def growth_diff_pp(ar: Dict[int, float], rev: Dict[int, float],
                   years: List[int], t: int) -> float:
    """应收增速 - 营收增速（小数，0.25=25%）。"""
    return growth(ar, years, t) - growth(rev, years, t)


def margin_gap_pp(rev: Dict[int, float], cogs: Dict[int, float], netp: Dict[int, float],
                  years: List[int], t: int) -> float:
    """(毛利率_t-毛利率_{t-1}) - (净利率_t-净利率_{t-1})（小数）。"""
    i = years.index(t)
    if i == 0:
        return 0.0

    def gm_at(yr):
        r = rev[yr]
        return (rev[yr] - cogs[yr]) / r if r != 0 else 0.0

    def nm_at(yr):
        r = rev[yr]
        return netp[yr] / r if r != 0 else 0.0

    return (gm_at(t) - gm_at(years[i - 1])) - (nm_at(t) - nm_at(years[i - 1]))


def yoy_growth(series: Dict[int, float], years: List[int], t: int) -> float:
    return growth(series, years, t)


def recompute(formula_id: str, company, t: int) -> Optional[float]:
    """用公司真实数据复算某公式在年度 t 的结果（供 D2 比对）。"""
    try:
        spec = _registry()[formula_id]
    except KeyError:
        return None
    func = spec["func"]
    inp = spec["inputs"]
    I, B, CF = company.income, company.balance, company.cashflow

    def val(key):
        loc = METRIC_LOC[key]
        table = {"income": I, "balance": B, "cashflow": CF}[loc]
        return table[key][t]

    if func == "ratio":
        a, b = val(inp[0]), val(inp[1])
        return ratio(a, b)
    if func == "abs_ratio":
        a, b = val(inp[0]), val(inp[1])
        return abs_ratio(a, b)
    if func == "growth_diff_pp":
        return growth_diff_pp(B[inp[0]], I[inp[1]], company.years, t)
    if func == "margin_gap_pp":
        return margin_gap_pp(I[inp[0]], I[inp[1]], I[inp[2]], company.years, t)
    if func == "yoy_growth":
        return yoy_growth(B[inp[0]] if inp[0] in B else I.get(inp[0], CF.get(inp[0])),
                          company.years, t)
    return None


def _recompute_from_operands(fb: List[dict], formula_id: str):
    """从模型自引的 fact_basis 操作数独立复算其可能意图的若干数值（定义无关，
    只验证“它算的这个数是否等于它引用的原始数能算出的数”）。"""
    from collections import defaultdict
    by_key = defaultdict(dict)  # metric_key -> {period: value}
    for f in fb:
        mk = f.get("metric_key")
        if not mk:
            continue
        by_key[mk][str(f.get("period"))] = f.get("value")
    cands = []

    def growth(mk):
        yrs = sorted(by_key[mk].keys(), key=lambda x: int(x))
        if len(yrs) < 2:
            return None
        a, b = by_key[mk][yrs[0]], by_key[mk][yrs[1]]
        return (b - a) / abs(a) if a not in (0, 0.0) else None

    # 增速差族
    if "growth_delta" in formula_id:
        keys = [k for k in by_key if k in ("accounts_receivable", "inventory", "revenue", "cogs")]
        grew = {k: growth(k) for k in keys if growth(k) is not None}
        gv = list(grew.values())
        if len(gv) >= 2:
            cands += [gv[0] - gv[1], gv[1] - gv[0]]
        return cands

    # 现金流/净利润 比族
    cf_keys = [k for k in by_key if k in ("operating_cash_flow", "cfo")]
    if cf_keys and "net_profit" in by_key:
        for y in by_key["net_profit"]:
            for ck in cf_keys:
                if y in by_key[ck] and by_key["net_profit"][y] not in (0, 0.0):
                    cands.append(by_key[ck][y] / by_key["net_profit"][y])
    # 流动比率 / 速动族
    if "current_assets" in by_key and "current_liabilities" in by_key:
        for y in by_key["current_liabilities"]:
            if y in by_key["current_assets"] and by_key["current_liabilities"][y]:
                cands.append(by_key["current_assets"][y] / by_key["current_liabilities"][y])
    if "short_borrow" in by_key and "cash" in by_key:
        for y in by_key["cash"]:
            if y in by_key["short_borrow"] and by_key["cash"][y]:
                cands.append(by_key["short_borrow"][y] / by_key["cash"][y])
    if "goodwill" in by_key and "equity" in by_key:
        for y in by_key["equity"]:
            if y in by_key["goodwill"] and by_key["equity"][y]:
                cands.append(by_key["goodwill"][y] / by_key["equity"][y])
    # 毛利率/净利率 族
    if "net_profit" in by_key and "revenue" in by_key:
        yrs = sorted(by_key["revenue"].keys(), key=lambda x: int(x))
        if len(yrs) >= 2:
            nm0 = by_key["net_profit"][yrs[0]] / by_key["revenue"][yrs[0]]
            nm1 = by_key["net_profit"][yrs[1]] / by_key["revenue"][yrs[1]]
            cands.append(nm1 - nm0)  # 净利率变动（模型常报此项）
            if "cogs" in by_key:
                gm0 = (by_key["revenue"][yrs[0]] - by_key["cogs"][yrs[0]]) / by_key["revenue"][yrs[0]]
                gm1 = (by_key["revenue"][yrs[1]] - by_key["cogs"][yrs[1]]) / by_key["revenue"][yrs[1]]
                cands.append((gm1 - gm0) - (nm1 - nm0))  # 毛利-净利背离（规则口径）
    return cands


def evaluate_d2(cards: List[AnomalyCard], company=None, rel_tol: float = 0.10,
               abs_tol: float = 0.02) -> Dict:
    """D2 公式正确性（定义无关的内部一致性）：

    用模型自引的 fact_basis 操作数独立复算其可能意图的若干数值，只要
    calculation.reported_result 命中其中之一（在容差内），即判公式正确。
    这样既不惩罚“模型与规则采用不同但都正确的定义”，也能抓出真正的算术错误。

    返回 {d2_hits, d2_total, d2_rate}。
    """
    hits = total = 0
    for c in cards:
        calc = getattr(c, "calculation", None)
        rep = getattr(calc, "reported_result", None) if calc is not None else None
        fb = getattr(c, "fact_basis", None) or []
        if rep is None or not fb:
            continue
        total += 1
        fid = getattr(calc, "formula_id", "") or ""
        cands = _recompute_from_operands(
            [f.model_dump() if hasattr(f, "model_dump") else f for f in fb], fid)
        ok = any(abs(rep - cand) <= max(abs_tol, rel_tol * abs(cand))
                 for cand in cands if cand is not None)
        if ok:
            hits += 1
    return {"d2_hits": hits, "d2_total": total,
            "d2_rate": (hits / total) if total else None}
