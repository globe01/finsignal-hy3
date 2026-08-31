"""规则侧 Oracle：从原始三表复算八类信号，作为金标准 G（方案 §4.2、§6.1 D4）。

- 每个信号计算标准化度量 x 与触发判定；
- 触发后用 severity.yaml 的 θ/d/σ 给出可复现严重度 s=d(x-θ)/σ；
- 分母为零/负数/缺失等返回 not_applicable，不强行判定（方案 §3.3）。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

import yaml

from generator.base import Company, _growth

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")


def _load_severity() -> dict:
    with open(os.path.join(_CONFIG, "severity.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg


_SEV = _load_severity()
_SCALE = _SEV["scale"]
_W = _SEV["weights"]


def severity_label(s: float) -> str:
    if s < 0:
        return "negative"
    if s < _SCALE["low_max"]:
        return "low"
    if s < _SCALE["medium_max"]:
        return "medium"
    return "high"


def _severity_of(signal_type: str, x: float) -> str:
    spec = _SEV["signals"][signal_type]
    s = spec["d"] * (x - spec["theta"]) / spec["sigma"]
    return severity_label(s)


# ---------- 各信号的度量 x 与触发判定 ----------
def _cashflow(company: Company):
    y, I, CF = company.years, company.income, company.cashflow
    ratios = []
    for t in y:
        np_ = I["net_profit"][t]
        ratios.append(CF["cfo"][t] / np_ if np_ > 0 else None)
    best, pair = None, None
    for i in range(1, len(y)):
        a, b = ratios[i - 1], ratios[i]
        if a is not None and b is not None and a < 0.5 and b < 0.5:
            m = min(a, b)
            if best is None or m < best:
                best, pair = m, [y[i - 1], y[i]]
    if best is None:
        return None, False, []
    return best, True, pair


def _growth_gap(company: Company, num_key: str, den_key: str):
    y, I, B = company.years, company.income, company.balance
    num = B if num_key in B else I
    den = I if den_key in I else B
    res = []
    for t in y[1:]:
        g = _growth(num[num_key], y, t) - _growth(den[den_key], y, t)
        if g >= 0.20:
            res.append((t, g))
    if not res:
        return None, False, []
    x = max(g for _, g in res)
    return x, True, [t for t, _ in res]


def _gross_net_gap(company: Company):
    y, I = company.years, company.income
    res = []
    for i in range(1, len(y)):
        t, p = y[i], y[i - 1]
        gm_t = (I["revenue"][t] - I["cogs"][t]) / I["revenue"][t] if I["revenue"][t] else 0
        gm_p = (I["revenue"][p] - I["cogs"][p]) / I["revenue"][p] if I["revenue"][p] else 0
        nm_t = I["net_profit"][t] / I["revenue"][t] if I["revenue"][t] else 0
        nm_p = I["net_profit"][p] / I["revenue"][p] if I["revenue"][p] else 0
        gm_c, nm_c = gm_t - gm_p, nm_t - nm_p
        gap = gm_c - nm_c
        if nm_c < 0 and gap >= 0.02:
            res.append((t, gap))
    if not res:
        return None, False, []
    x = max(gap for _, gap in res)
    return x, True, [t for t, _ in res]


def _ratio_signal(company: Company, num_key: str, den_key: str, theta: float):
    y = company.years
    I, B = company.income, company.balance
    num = B if num_key in B else I
    den = B if den_key in B else I
    res = []
    for t in y:
        d = den[den_key][t]
        if d == 0:
            continue
        v = num[num_key][t] / d
        if v >= theta:
            res.append((t, v))
    if not res:
        return None, False, []
    x = max(v for _, v in res)
    return x, True, [t for t, _ in res]


def _short_term(company: Company):
    y, B = company.years, company.balance
    res = []
    for t in y:
        ca, cl = B["current_assets"][t], B["current_liabilities"][t]
        cr = ca / cl if cl else 0
        sd = B["short_borrow"][t] / B["cash"][t] if B["cash"][t] else 1e9
        if cr < 1 and sd > 1:
            res.append((t, sd))
    if not res:
        return None, False, []
    x = max(sd for _, sd in res)
    return x, True, [t for t, _ in res]


_DISPATCH = {
    "cashflow_profit_divergence": lambda c: _cashflow(c),
    "receivables_revenue_divergence": lambda c: _growth_gap(c, "accounts_receivable", "revenue"),
    "inventory_cost_divergence": lambda c: _growth_gap(c, "inventory", "cogs"),
    "gross_net_margin_divergence": lambda c: _gross_net_gap(c),
    "nonrecurring_profit_dependence": lambda c: _ratio_signal(c, "nonrecurring", "net_profit", 0.30),
    "goodwill_net_assets_pressure": lambda c: _ratio_signal(c, "goodwill", "equity", 0.20),
    "short_term_solvency_pressure": lambda c: _short_term(c),
    "impairment_loss_surge": lambda c: (None, False, []),  # 合成数据无减值科目 → 不适用
}


def compute_signal(company: Company, signal_type: str) -> Optional[dict]:
    """返回单信号的判定；未触发或不适返回 None。"""
    if signal_type not in _DISPATCH:
        return None
    x, triggered, periods = _DISPATCH[signal_type](company)
    if not triggered:
        return None
    return {
        "signal_type": signal_type,
        "measure_x": x,
        "severity": _severity_of(signal_type, x),
        "periods": [str(p) for p in periods],
    }


def compute_all_signals(company: Company) -> List[dict]:
    """金标准 G：公司实际触发的全部目标异常。"""
    out = []
    for st in _DISPATCH:
        r = compute_signal(company, st)
        if r:
            out.append(r)
    return out


def severity_weight(label: str) -> int:
    return _W.get(label, 1)
