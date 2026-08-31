"""D2 公式与计算正确性（方案 §5.2）：五步严格校验 + 禁止 eval + 禁止多候选放水。

历史坑：为了不惩罚「同义不同式」，一度用「按 formula_id 族复算多个候选、命中任一即算对」，
等于给模型一票多投的后门，D2 会系统性虚高。正确做法是把口径固定在注册表里，
五步依次校验，任一步失败即 fail，并记录失败在哪一步。
"""
from __future__ import annotations

import os
import re

import pytest

from app.formulas import formula_ids, load_registry
from app.schema import AnomalyCard, Calculation, FactBasis
from eval.formula_eval import (ABS_TOL, D2_CHECKS, SCALE_MIN_EXPECTED, SCALE_MIN_REPORTED,
                               evaluate_card_d2, evaluate_d2, recompute)
from generator.base import build_record_index, make_clean_company, record_id_of
from generator.inject import inject

AR_SIGNAL = "receivables_revenue_divergence"
AR_FORMULA = "ar_minus_rev_growth"


@pytest.fixture
def loud_company():
    """注入后的公司：AR 背离约 0.50，量级显著非零。

    单位归一 / 数值判错这类测试必须建在「复算值显著非零」的样本上，
    否则清洁公司的复算值≈0，任何两个数值在绝对容差内都无法区分，
    测的其实是容差而不是逻辑。
    """
    company, _meta = inject(make_clean_company(), AR_SIGNAL, 2.0)
    return company


def _fact(company, statement, key, year) -> FactBasis:
    rec = build_record_index(company)[record_id_of(statement, key, year)]
    return FactBasis(fact_id=f"{key}_{year}", metric_key=rec.metric_key,
                     metric_name=rec.metric_name, period=rec.period, value=rec.value,
                     source_record_id=rec.record_id, source_file=rec.source_file,
                     source_row=rec.source_row, source_column=rec.source_column,
                     statement=rec.statement)


def _ar_card(company, *, formula_id=AR_FORMULA, signal_type=AR_SIGNAL,
             reported=None, years=(2023, 2024), keys=("accounts_receivable", "revenue")):
    facts = []
    for key in keys:
        statement = "balance" if key in company.balance else "income"
        for y in years:
            facts.append(_fact(company, statement, key, y))
    if reported is None:
        reported = recompute(AR_FORMULA, company, 2024)
    return AnomalyCard(signal_type=signal_type, severity="high", periods=["2024"],
                       fact_basis=facts,
                       calculation=Calculation(formula_id=formula_id,
                                               operand_fact_ids=[f.fact_id for f in facts],
                                               reported_result=reported))


# ---------------------------------------------------------------- 正例
def test_correct_card_passes_all_five_steps(clean_company):
    r = evaluate_card_d2(_ar_card(clean_company), clean_company)
    assert r.status == "pass", r.issues
    assert all(r.checks.get(k) is True for k in D2_CHECKS), r.checks
    assert r.period_used == 2024
    assert r.scale_adjusted is False


def test_percentage_scale_is_normalized_not_rewarded_blindly(loud_company):
    """0.50 写成 50 属于同一公式的单位写法 → 归一后通过并留痕。"""
    expected = recompute(AR_FORMULA, loud_company, 2024)
    assert abs(expected) >= SCALE_MIN_EXPECTED       # 前提：复算值显著非零
    r = evaluate_card_d2(_ar_card(loud_company, reported=expected * 100), loud_company)
    assert r.status == "pass", r.issues
    assert r.scale_adjusted is True


# ---------------------------------------------------------------- 五步逐一失败
def test_step1_unknown_formula_id_fails(clean_company):
    """自造公式名（如历史示例里的 growth_delta）必须第 1 步就失败。"""
    r = evaluate_card_d2(_ar_card(clean_company, formula_id="growth_delta"), clean_company)
    assert r.status == "fail"
    assert r.checks["formula_known"] is False
    assert "formula_id 不在注册表" in r.issues[0]


def test_step1_empty_formula_id_fails(clean_company):
    card = _ar_card(clean_company)
    card.calculation.formula_id = ""
    r = evaluate_card_d2(card, clean_company)
    assert r.status == "fail" and r.checks["formula_known"] is False


def test_step2_formula_signal_mismatch_fails(clean_company):
    """公式与 signal_type 不匹配 → 不得因为算术恰好对上而放过。"""
    r = evaluate_card_d2(_ar_card(clean_company, formula_id="goodwill_over_equity"),
                         clean_company)
    assert r.status == "fail"
    assert r.checks["signal_match"] is False


def test_step3_missing_operand_fails(clean_company):
    """只引用了应收账款、没引用营业收入 → 操作数不齐备。"""
    r = evaluate_card_d2(_ar_card(clean_company, keys=("accounts_receivable",)), clean_company)
    assert r.status == "fail"
    assert r.checks["operands_ok"] is False
    assert any("缺少操作数科目" in i for i in r.issues)


def test_step4_pair_formula_needs_two_years(clean_company):
    """同比类公式只引用当年 → 期间校验失败。"""
    r = evaluate_card_d2(_ar_card(clean_company, years=(2024,)), clean_company)
    assert r.status == "fail"
    assert r.checks["periods_ok"] is False


def test_step4_first_year_has_no_comparable(clean_company):
    card = _ar_card(clean_company, years=(2022,))
    card.periods = ["2022"]
    r = evaluate_card_d2(card, clean_company)
    assert r.status == "fail"
    assert r.checks["periods_ok"] is False


def test_step5_arithmetic_error_fails(loud_company):
    expected = recompute(AR_FORMULA, loud_company, 2024)
    r = evaluate_card_d2(_ar_card(loud_company, reported=expected + 0.5), loud_company)
    assert r.status == "fail"
    assert r.checks["arithmetic_ok"] is False
    assert any("算术不符" in i for i in r.issues)


def test_wrong_number_cannot_hit_any_other_candidate(loud_company):
    """回归：换一个别的公式的数值上报，不得被「多候选命中任一」放过。"""
    expected = recompute(AR_FORMULA, loud_company, 2024)
    other = recompute("current_ratio", loud_company, 2024)
    assert other is not None
    assert abs(other - expected) > max(ABS_TOL, 0.05 * abs(expected))   # 两值确实可区分
    r = evaluate_card_d2(_ar_card(loud_company, reported=other), loud_company)
    assert r.status == "fail"
    assert r.checks["arithmetic_ok"] is False


# ---------------------------------------------------------------- 单位归一后门（回归）
def _abs_close(a: float, b: float) -> bool:
    """复刻评估器的容差判定，用于断言「旧逻辑本会判对」。"""
    return abs(a - b) <= max(ABS_TOL, 0.05 * abs(b))


def test_scale_path_cannot_swallow_wrong_value_near_zero(clean_company):
    """回归：复算值≈0 时，÷100 曾能把明显错值压进容差判对（单位归一=第二次机会）。

    构造 rep=0.30、复算≈-0.0006：rep/100=0.003 落在绝对容差内，旧逻辑判 pass。
    新逻辑靠「上报值量级需像百分数」的门槛拦住它，且必须留下拦截痕迹。
    """
    expected = recompute(AR_FORMULA, clean_company, 2024)
    assert abs(expected) < SCALE_MIN_EXPECTED           # 前提：复算值确实接近 0
    rep = 0.30
    assert not _abs_close(rep, expected)                # 直接比对本就不符
    assert _abs_close(rep / 100.0, expected)            # 但旧的无条件归一会判对

    r = evaluate_card_d2(_ar_card(clean_company, reported=rep), clean_company)
    assert r.status == "fail", r.issues
    assert r.scale_adjusted is False
    assert any("百分数归一门槛" in i for i in r.issues), r.issues


def test_scale_normalization_requires_percentage_magnitude(clean_company):
    """上报值量级不像百分数（|rep| < 1）时不得归一，即使 ÷100 后完全相等。"""
    expected = recompute(AR_FORMULA, clean_company, 2024)
    rep = expected * 100
    assert abs(rep) < SCALE_MIN_REPORTED
    r = evaluate_card_d2(_ar_card(clean_company, reported=rep), clean_company)
    assert r.status == "fail", r.issues
    assert r.scale_adjusted is False
    assert any("百分数归一门槛" in i for i in r.issues), r.issues


def test_absolute_tolerance_is_half_of_two_decimal_rounding():
    """绝对容差必须锁在 0.005（小数保留 2 位的半个末位），放宽即为 D2 虚高通道。"""
    assert ABS_TOL == 0.005


# ---------------------------------------------------------------- 不适用语义
def test_other_signal_type_is_not_applicable(clean_company):
    card = _ar_card(clean_company)
    card.signal_type = "other"
    r = evaluate_card_d2(card, clean_company)
    assert r.status == "not_applicable"


def test_missing_calculation_is_not_applicable(clean_company):
    card = _ar_card(clean_company)
    card.calculation = None
    r = evaluate_card_d2(card, clean_company)
    assert r.status == "not_applicable"


def test_unavailable_formula_is_not_applicable(clean_company):
    """减值类科目在当前数据里不存在 → not_applicable，不能计入 D2 分母。"""
    card = _ar_card(clean_company, formula_id="impairment_yoy_growth",
                    signal_type="impairment_loss_surge")
    r = evaluate_card_d2(card, clean_company)
    assert r.status == "not_applicable"


def test_not_applicable_excluded_from_denominator(clean_company):
    good = _ar_card(clean_company)
    na = _ar_card(clean_company)
    na.calculation = None
    agg = evaluate_d2([good, na], clean_company)
    assert agg["d2_total"] == 1 and agg["d2_hits"] == 1
    assert agg["d2_rate"] == 1.0
    assert agg["d2_not_applicable"] == 1


def test_aggregate_reports_step_rates(clean_company):
    good = _ar_card(clean_company)
    bad = _ar_card(clean_company, formula_id="growth_delta")
    agg = evaluate_d2([good, bad], clean_company)
    assert agg["d2_hits"] == 1 and agg["d2_total"] == 2
    assert agg["d2_rate"] == 0.5
    assert agg["d2_step_hits"]["formula_known"] == 1
    assert agg["d2_step_total"]["formula_known"] == 2
    assert agg["d2_step_total"]["arithmetic_ok"] == 1   # 第 1 步就失败的不进后续分母


def test_empty_cards_yield_na(clean_company):
    agg = evaluate_d2([], clean_company)
    assert agg["d2_rate"] is None and agg["d2_total"] == 0


# ---------------------------------------------------------------- 安全约束
def test_registry_is_single_source_of_truth():
    reg = load_registry()
    assert set(formula_ids()) == set(reg.keys())
    for fid, spec in reg.items():
        assert spec.get("signal_types"), f"{fid} 未声明适用 signal_type"
        assert spec.get("inputs"), f"{fid} 未声明操作数"
        assert spec.get("period_mode") in ("single", "pair"), fid
        assert len(spec["role"]) == len(spec["inputs"]), fid


def test_no_eval_or_exec_in_evaluation_code():
    """评估器绝不可 eval/exec 模型生成的表达式（方案 §5.2 安全约束）。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pattern = re.compile(r"(?<![\w.])(eval|exec)\s*\(")
    offenders = []
    for sub in ("eval", "app", "generator"):
        for dirpath, _dirs, files in os.walk(os.path.join(root, sub)):
            if "__pycache__" in dirpath:
                continue
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                with open(path, encoding="utf-8") as f:
                    for lineno, line in enumerate(f, 1):
                        if pattern.search(line):
                            offenders.append(f"{path}:{lineno}: {line.strip()}")
    assert not offenders, "发现动态执行调用：\n" + "\n".join(offenders)


def test_recompute_returns_none_on_missing_metric(clean_company):
    assert recompute("impairment_yoy_growth", clean_company, 2024) is None


def test_recompute_returns_none_for_first_year(clean_company):
    assert recompute(AR_FORMULA, clean_company, clean_company.years[0]) is None


def test_recompute_handles_zero_denominator(clean_company):
    for t in clean_company.years:
        clean_company.balance["equity"][t] = 0.0
    assert recompute("goodwill_over_equity", clean_company, 2024) is None


def test_recompute_unknown_formula_id_returns_none(clean_company):
    """未知 formula_id 必须短路返回 None，绝不尝试解析模型给的表达式。"""
    assert recompute("growth_delta", clean_company, 2024) is None
