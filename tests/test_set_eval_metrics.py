"""集合级指标（方案 §6.3、§6.4）：TP/FP/FN、期间匹配、micro 聚合、N/A 语义。

本轮修复的核心断言：
- 主指标 MRhigh 必须 micro 聚合（分子分母跨样本相加），逐样本平均只能作参考；
- 无 high 金标准时 MRhigh 必须是 N/A（None），不能记 0；
- 所有比率必须同时输出分子与分母；
- 期间偏差不得被双重惩罚（默认重叠匹配），exact 仅作敏感性分析。
"""
from __future__ import annotations

import pytest

from eval.set_eval import (
    PERIOD_MATCH_EXACT,
    PERIOD_MATCH_OVERLAP,
    compute_metrics,
    match,
    summarize,
)
from tests.conftest import gold

A = "receivables_revenue_divergence"
B = "goodwill_net_assets_pressure"
C = "cashflow_profit_divergence"


# ---------------------------------------------------------------- 匹配
def test_exact_period_match_is_tp():
    g = [gold(A, ["2023", "2024"])]
    m = [gold(A, ["2023", "2024"])]
    r = match(g, m)
    assert len(r["tp"]) == 1 and not r["fp"] and not r["fn"]
    assert r["period_exact_tp"] == 1 and r["period_shifted_tp"] == 0


def test_overlap_gives_credit_but_marks_shift():
    """金标准 2023-2024、模型只标 2024 → 记 TP 但标记期间偏移。"""
    g = [gold(A, ["2023", "2024"])]
    m = [gold(A, ["2024"])]
    r = match(g, m, period_mode=PERIOD_MATCH_OVERLAP)
    assert len(r["tp"]) == 1 and not r["fp"] and not r["fn"]
    assert r["period_shifted_tp"] == 1


def test_exact_mode_double_penalizes_period_shift():
    """严格模式下同一偏差同时计 FP 与 FN —— 这正是默认不用它的原因。"""
    g = [gold(A, ["2023", "2024"])]
    m = [gold(A, ["2024"])]
    r = match(g, m, period_mode=PERIOD_MATCH_EXACT)
    assert not r["tp"] and len(r["fp"]) == 1 and len(r["fn"]) == 1
    assert r["fn"][0]["fn_mode"] == "period_disjoint"


def test_disjoint_periods_are_fn_with_mode():
    g = [gold(A, ["2023"])]
    m = [gold(A, ["2021"])]
    r = match(g, m)
    assert len(r["fn"]) == 1 and r["fn"][0]["fn_mode"] == "period_disjoint"
    assert len(r["fp"]) == 1


def test_missed_signal_type_mode():
    g = [gold(A, ["2024"])]
    r = match(g, [])
    assert r["fn"][0]["fn_mode"] == "missed_signal_type"


def test_one_to_one_greedy_no_double_counting():
    """一条模型卡片不能同时满足两条金标准。"""
    g = [gold(A, ["2023"]), gold(A, ["2024"])]
    m = [gold(A, ["2023", "2024"])]
    r = match(g, m)
    assert len(r["tp"]) == 1 and len(r["fn"]) == 1 and not r["fp"]


def test_missing_periods_fall_back_to_signal_type():
    g = [gold(A, [])]
    m = [gold(A, ["2024"])]
    assert len(match(g, m)["tp"]) == 1


def test_unknown_period_mode_raises():
    with pytest.raises(ValueError):
        match([], [], period_mode="fuzzy")


# ---------------------------------------------------------------- 单样本指标
def test_metrics_report_numerator_and_denominator():
    g = [gold(A, ["2024"], "high"), gold(B, ["2024"], "medium")]
    m = [gold(A, ["2024"], "high")]
    r = compute_metrics(g, m)
    assert r["TP"] == 1 and r["FP"] == 0 and r["FN"] == 1
    assert r["num_den"]["R"] == {"value": 0.5, "num": 1, "den": 2}
    assert r["num_den"]["P"] == {"value": 1.0, "num": 1, "den": 1}
    # 加权召回：high=3 命中，medium=2 漏掉 → 3/5
    assert r["num_den"]["Rw"] == {"value": pytest.approx(0.6), "num": 3, "den": 5}
    assert r["num_den"]["MRhigh"] == {"value": 0.0, "num": 0, "den": 1}


def test_mrhigh_counts_only_high_gold():
    g = [gold(A, ["2024"], "high"), gold(B, ["2024"], "high"), gold(C, ["2024"], "low")]
    m = [gold(A, ["2024"], "high")]
    r = compute_metrics(g, m)
    assert r["high_total"] == 2 and r["high_missed"] == 1
    assert r["MRhigh"] == pytest.approx(0.5)


def test_mrhigh_is_na_when_no_high_gold():
    """无 high 金标准 → N/A，不得记 0（记 0 等于凭空塞满分）。"""
    r = compute_metrics([gold(A, ["2024"], "medium")], [])
    assert r["MRhigh"] is None
    assert r["num_den"]["MRhigh"]["den"] == 0


def test_empty_gold_and_empty_model_do_not_crash():
    r = compute_metrics([], [])
    assert r["P"] is None and r["R"] is None and r["F1"] is None
    assert r["MRhigh"] is None and r["Rw"] is None


def test_empty_gold_with_false_positive():
    """阴性对照上模型报了信号 → P=0，R 为 N/A（无金标准可召回）。"""
    r = compute_metrics([], [gold(A, ["2024"])])
    assert r["FP"] == 1
    assert r["P"] == 0.0
    assert r["R"] is None
    assert r["F1"] is None


def test_duplicate_model_cards_count_as_false_positive():
    g = [gold(A, ["2024"], "high")]
    m = [gold(A, ["2024"], "high"), gold(A, ["2024"], "high")]
    r = compute_metrics(g, m)
    assert r["TP"] == 1 and r["FP"] == 1
    assert r["num_den"]["P"] == {"value": 0.5, "num": 1, "den": 2}


# ---------------------------------------------------------------- micro 聚合
def test_summarize_uses_micro_not_macro():
    """核心回归：1 个 high 全漏 + 9 个 high 全中 → micro=1/10，macro=0.5。"""
    small = compute_metrics([gold(A, ["2024"], "high")], [])
    big_gold = [gold(A, [str(2000 + i)], "high") for i in range(9)]
    big = compute_metrics(big_gold, list(big_gold))

    s = summarize([small, big])
    assert s["aggregation"] == "micro"
    assert s["MRhigh"]["num"] == 1 and s["MRhigh"]["den"] == 10
    assert s["MRhigh"]["value"] == pytest.approx(0.1)
    # macro 只作参考，且必须与 micro 不同以证明两者没被写成同一条路径
    assert s["MRhigh_macro"]["value"] == pytest.approx(0.5)
    assert s["MRhigh_macro"]["n_cases"] == 2


def test_summarize_skips_na_cases_in_macro():
    """macro 平均必须跳过 N/A，而不是把 N/A 当 0 拉低漏报率。"""
    has_high = compute_metrics([gold(A, ["2024"], "high")], [])       # MRhigh=1.0
    no_high = compute_metrics([gold(B, ["2024"], "medium")], [])      # MRhigh=None
    s = summarize([has_high, no_high])
    assert s["MRhigh"]["num"] == 1 and s["MRhigh"]["den"] == 1
    assert s["MRhigh_macro"]["value"] == pytest.approx(1.0)
    assert s["MRhigh_macro"]["n_cases"] == 1


def test_summarize_micro_precision_recall():
    c1 = compute_metrics([gold(A, ["2024"], "high")], [gold(A, ["2024"], "high")])
    c2 = compute_metrics([gold(B, ["2024"], "medium")],
                         [gold(C, ["2024"], "medium")])   # 全错：1 FP + 1 FN
    s = summarize([c1, c2])
    assert (s["TP"], s["FP"], s["FN"]) == (1, 1, 1)
    assert s["P"] == {"value": 0.5, "num": 1, "den": 2}
    assert s["R"] == {"value": 0.5, "num": 1, "den": 2}
    assert s["Rw"] == {"value": pytest.approx(3 / 5), "num": 3, "den": 5}


def test_summarize_reports_excluded_cases():
    c1 = compute_metrics([gold(A, ["2024"], "high")], [gold(A, ["2024"], "high")])
    s = summarize([c1], n_cases_total=14)
    assert s["n_cases"] == 14
    assert s["n_cases_scored"] == 1
    assert s["n_cases_excluded"] == 13


def test_summarize_empty_input_is_all_na():
    s = summarize([], n_cases_total=3)
    assert s["MRhigh"]["value"] is None
    assert s["P"]["value"] is None and s["R"]["value"] is None
    assert s["n_cases_scored"] == 0 and s["n_cases_excluded"] == 3


def test_summarize_aggregates_fn_modes():
    c1 = compute_metrics([gold(A, ["2024"], "high")], [])
    c2 = compute_metrics([gold(B, ["2024"], "high")], [gold(B, ["2019"], "high")])
    s = summarize([c1, c2])
    assert s["fn_modes"] == {"missed_signal_type": 1, "period_disjoint": 1}


def test_custom_severity_weight_is_respected():
    g = [gold(A, ["2024"], "high"), gold(B, ["2024"], "low")]
    m = [gold(A, ["2024"], "high")]
    r = compute_metrics(g, m, severity_weight=lambda s: 1)
    assert r["num_den"]["Rw"] == {"value": 0.5, "num": 1, "den": 2}
