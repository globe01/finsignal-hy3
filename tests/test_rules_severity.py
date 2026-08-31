"""规则 Oracle 与严重度公式（方案 §5.5、§3.3）。"""
from __future__ import annotations

import copy

from eval.rules import (
    compute_all_signals,
    compute_signal,
    severity_label,
    severity_weight,
)


def test_clean_company_triggers_nothing(clean_company):
    """清洁基底必须是空金标准，否则阴性对照与阈下设计全部失效。"""
    assert compute_all_signals(clean_company) == []


def test_severity_label_boundaries():
    """s<0 阴性；[0,0.5) low；[0.5,1.5) medium；>=1.5 high（方案 §5.5）。"""
    assert severity_label(-0.01) == "negative"
    assert severity_label(0.0) == "low"
    assert severity_label(0.4999) == "low"
    assert severity_label(0.5) == "medium"
    assert severity_label(1.4999) == "medium"
    assert severity_label(1.5) == "high"
    assert severity_label(99.0) == "high"


def test_severity_weights():
    assert severity_weight("high") == 3
    assert severity_weight("medium") == 2
    assert severity_weight("low") == 1
    assert severity_weight("unknown") == 1  # 未知标签保守取 1，不得抛异常


def test_impairment_not_applicable(clean_company):
    """合成数据无减值科目 → 该信号必须返回不适用，而不是伪造一个判定。"""
    assert compute_signal(clean_company, "impairment_loss_surge") is None


def test_unknown_signal_type_returns_none(clean_company):
    assert compute_signal(clean_company, "made_up_signal") is None


def test_zero_and_negative_denominator_do_not_crash(clean_company):
    """净利润为 0 / 货币资金为 0 时必须返回不适用而非除零崩溃（方案 §3.3）。"""
    c = copy.deepcopy(clean_company)
    for t in c.years:
        c.income["net_profit"][t] = 0.0
        c.balance["cash"][t] = 0.0
        c.balance["current_liabilities"][t] = 0.0
    signals = compute_all_signals(c)          # 不抛异常即通过
    assert isinstance(signals, list)
    # 净利润<=0 时现金流/净利润无意义，必须不触发
    assert all(s["signal_type"] != "cashflow_profit_divergence" for s in signals)


def test_negative_net_profit_skipped(clean_company):
    c = copy.deepcopy(clean_company)
    for t in c.years:
        c.income["net_profit"][t] = -50.0
    assert compute_signal(c, "cashflow_profit_divergence") is None


def test_missing_year_is_tolerated(clean_company):
    """首年无同比 → 增速类信号不得把首年计入触发期间。"""
    r = compute_signal(clean_company, "receivables_revenue_divergence")
    assert r is None or str(clean_company.years[0]) not in r["periods"]
