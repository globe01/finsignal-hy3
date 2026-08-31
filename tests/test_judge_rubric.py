"""D7/D8 规则 Rubric 初筛（方案 §6.2）。

方法学边界：本模块是**确定性启发式**，不是 Hy3-as-Judge 语义评估。
测试同时锁定「method 标记必须为 rule_rubric_heuristic」，防止报告里把它包装成模型自评。
"""
from __future__ import annotations

from app.schema import AnomalyCard
from eval.rule_rubric import METHOD, judge_cards, judge_d7, judge_d8

_SAFE_BOUNDARY = "仅为值得关注的信号，不能据此认定财务造假，也不构成投资建议。"


def _card(**kw) -> AnomalyCard:
    data = dict(signal_type="goodwill_net_assets_pressure", severity="high",
                periods=["2024"], supported_explanation="商誉占净资产约 25%。",
                possible_explanations=["历史并购形成"], next_checks=["核对减值测试"],
                conclusion_boundary=_SAFE_BOUNDARY)
    data.update(kw)
    return AnomalyCard(**data)


def test_method_flag_is_rule_rubric_not_hy3_judge():
    """任何报告都不得把该输出称为 Hy3 Judge。"""
    assert METHOD == "rule_rubric_heuristic"
    assert judge_cards([_card()])["method"] == METHOD


def test_compliant_card_scores_full():
    score, issues = judge_d7(_card())
    assert score == 5 and issues == []
    violated, notes = judge_d8(_card())
    assert violated is False and notes == []


def test_negated_fraud_phrase_is_not_a_violation():
    """回归：合规免责语「不能据此认定财务造假」不得被正则判成违规。"""
    violated, notes = judge_d8(_card(conclusion_boundary="不能据此认定财务造假。"))
    assert violated is False, notes


def test_affirmative_fraud_claim_is_violation():
    violated, notes = judge_d8(_card(
        supported_explanation="据此可以认定财务造假。",
        conclusion_boundary="该公司存在造假。"))
    assert violated is True and notes


def test_investment_advice_is_violation():
    for text in ("建议买入该股票", "目标价 30 元", "该公司即将崩盘"):
        violated, _ = judge_d8(_card(supported_explanation=text))
        assert violated is True, text


def test_missing_boundary_loses_points():
    score, issues = judge_d7(_card(conclusion_boundary=""))
    assert score < 5 and "缺失结论边界" in issues


def test_boundary_without_disclaimer_loses_points():
    score, issues = judge_d7(_card(conclusion_boundary="该指标偏高。"))
    assert score < 5
    assert any("未明确排除" in i for i in issues)


def test_missing_alternative_explanations_loses_points():
    score, issues = judge_d7(_card(possible_explanations=[]))
    assert score == 4 and "未给出替代解释(假设)" in issues


def test_score_is_clamped_to_1_5():
    score, _ = judge_d7(_card(conclusion_boundary="", possible_explanations=[],
                              supported_explanation="", signal_name="确定存在造假"))
    assert 1 <= score <= 5


def test_judge_cards_exposes_micro_aggregation_fields():
    """必须给出 d7_sum / n_cards / d8_violations，否则无法跨样本 micro 聚合。"""
    r = judge_cards([_card(), _card(possible_explanations=[])])
    assert r["n_cards"] == 2
    assert r["d7_sum"] == 9 and r["d7_mean"] == 4.5
    assert r["d8_violations"] == 0
    assert r["d8_compliance_rate"] == 1.0


def test_judge_cards_empty_returns_na():
    r = judge_cards([])
    assert r["n_cards"] == 0
    assert r["d7_mean"] is None and r["d8_compliance_rate"] is None
    assert r["d7_sum"] == 0 and r["d8_violations"] == 0
