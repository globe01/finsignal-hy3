"""D7/D8 的 Hy3-as-Judge 语义评审（方案 §5.6、§6.2 第二条路径）。

方法学红线（见 eval/hy3_judge.py 顶部）：
- 本模块是**与规则 Rubric 并列**的第二条路径，不可互相替代，也不得被误称为真值；
- 单卡评审失败不应中断整轮（errors 与 violations 严格分离）；
- parse 容错但**不补分**：拿不到的子维度记 None 并计入 n_missing，绝不默认给 5。

全部用桩 chat_json，离线可跑、零 API 额度。
"""
from __future__ import annotations

from app.schema import AnomalyCard
from eval.hy3_judge import (
    D7_SUBDIMS,
    D8_FLAGS,
    METHOD,
    compare_judges,
    judge_cards_with_hy3,
    parse_judge_reply,
)

_SAFE = "仅为值得关注的信号，不能据此认定财务造假，也不构成投资建议。"


def _card(**kw) -> AnomalyCard:
    data = dict(
        signal_type="receivables_revenue_divergence", severity="high",
        periods=["2024"],
        fact_basis=[],
        calculation=None,
        supported_explanation="应收账款增速远高于营收增速，存在提前确认收入嫌疑。",
        possible_explanations=["客户信用政策放宽", "关联方资金走账"],
        next_checks=["核对前五大客户账龄附注"],
        conclusion_boundary=_SAFE,
    )
    data.update(kw)
    return AnomalyCard(**data)


# ---------------------------------------------------------------- 方法学标记
def test_method_flag_is_hy3_as_judge():
    """与规则 Rubric 的 rule_rubric_heuristic 区分开。"""
    assert METHOD == "hy3_as_judge_semantic"


# ---------------------------------------------------------------- 解析容错
def test_parse_full_reply():
    raw = {
        "scores": {k: 4 for k in D7_SUBDIMS},
        "deductions": [{"subdim": "alternative_explanations",
                        "reason": "替代解释笼统", "evidence": "客户信用政策放宽"}],
        "compliance": {k: False for k in D8_FLAGS},
        "compliance_evidence": [],
    }
    r = parse_judge_reply(raw)
    assert r["d7_mean"] == 4.0
    assert r["n_missing_subdims"] == 0
    assert r["violated"] is False
    assert r["n_unverifiable_deductions"] == 0


def test_parse_missing_subdim_marks_none_not_full_score():
    """容错但不补分：缺失 evidence_grounding 应记 None，d7_mean 按有效项算。"""
    raw = {
        "scores": {"alternative_explanations": 3, "actionable_checks": 5,
                   "boundary_calibration": 5},  # 缺 evidence_grounding
        "compliance": {k: False for k in D8_FLAGS},
    }
    r = parse_judge_reply(raw)
    assert r["scores"]["evidence_grounding"] is None
    assert r["n_missing_subdims"] == 1
    # 3 个有效项均值 = (3+5+5)/3 = 4.33，不是默认 5
    assert r["d7_mean"] is not None and abs(r["d7_mean"] - 13 / 3) < 1e-9


def test_parse_clamps_out_of_range_scores():
    raw = {"scores": {k: 9 for k in D7_SUBDIMS}, "compliance": {k: False for k in D8_FLAGS}}
    r = parse_judge_reply(raw)
    assert all(v == 5 for v in r["scores"].values())
    raw2 = {"scores": {k: -3 for k in D7_SUBDIMS}, "compliance": {k: False for k in D8_FLAGS}}
    assert all(v == 1 for v in parse_judge_reply(raw2)["scores"].values())


def test_parse_detects_unverifiable_deduction():
    """无原文引用的扣分必须被标出，便于人工抽查 Judge 是否编造理由。"""
    raw = {
        "scores": {k: 3 for k in D7_SUBDIMS},
        "deductions": [{"subdim": "evidence_grounding", "reason": "解释空洞", "evidence": ""}],
        "compliance": {k: False for k in D8_FLAGS},
    }
    r = parse_judge_reply(raw)
    assert r["n_unverifiable_deductions"] == 1


def test_parse_compliance_violation():
    raw = {"scores": {k: 5 for k in D7_SUBDIMS},
           "compliance": {**{k: False for k in D8_FLAGS}, "implicit_accusation": True},
           "compliance_evidence": [{"flag": "implicit_accusation",
                                    "evidence": "管理层刻意转移利润"}]}
    r = parse_judge_reply(raw)
    assert r["violated"] is True
    assert "implicit_accusation" in r["violated_flags"]


# ---------------------------------------------------------------- 桩驱动整链路
def _stub_chat(valid: bool, score: int = 4, violation: bool = False):
    def _fake(messages):
        if not valid:
            raise RuntimeError("模拟评审失败")
        return {
            "scores": {k: score for k in D7_SUBDIMS},
            "deductions": [],
            "compliance": {**{k: False for k in D8_FLAGS},
                           "implicit_accusation": violation},
            "compliance_evidence": [],
        }
    return _fake


def test_judge_cards_with_stub_micro_aggregates():
    cards = [_card(), _card(conclusion_boundary="")]
    r = judge_cards_with_hy3(cards, "（财务数据占位）", chat_json=_stub_chat(True, 4))
    assert r["method"] == METHOD
    assert r["n_cards"] == 2 and r["n_scored"] == 2 and r["n_errors"] == 0
    assert r["d7_mean"] == 4.0
    assert r["d7_sum"] == 8.0
    assert r["d8_violations"] == 0
    assert r["d8_compliance_rate"] == 1.0


def test_judge_one_card_error_isolated_not_fatal():
    """单卡评审失败计入 n_errors，不当成合规，也不中断其余卡片。"""
    cards = [_card(), _card(signal_type="other")]
    r = judge_cards_with_hy3(cards, "x", chat_json=_stub_chat(False))
    assert r["n_cards"] == 2
    assert r["n_errors"] == 2
    assert r["d7_mean"] is None           # 无成功评分
    # violated 为 None 的卡不进分母，合规率应为 None 而非 100%
    assert r["d8_compliance_rate"] is None


def test_judge_violation_via_stub():
    cards = [_card()]
    r = judge_cards_with_hy3(cards, "x", chat_json=_stub_chat(True, 3, violation=True))
    assert r["d8_violations"] == 1
    assert r["d8_compliance_rate"] == 0.0
    assert r["flag_counts"]["implicit_accusation"] == 1


def test_judge_empty_returns_na():
    r = judge_cards_with_hy3([], "x", chat_json=_stub_chat(True))
    assert r["n_cards"] == 0
    assert r["d7_mean"] is None and r["d8_compliance_rate"] is None
    assert r["n_scored"] == 0 and r["n_errors"] == 0


# ---------------------------------------------------------------- 两条路径一致性
def _rule_d7_result(scores):
    return {"d7_scores": scores,
            "d8_per_card": [False] * len(scores)}


def _hy3_result(means, viols):
    per = [{"d7_mean": m, "violated": v} for m, v in zip(means, viols)]
    return {"per_card": per, "flag_counts": {k: 0 for k in D8_FLAGS}}


def test_compare_judges_d7_metrics():
    rule = _rule_d7_result([5, 4, 3])
    hy3 = _hy3_result([4, 4, 4], [False, False, False])
    out = compare_judges(rule, hy3)
    assert out["d7"]["n_pairs"] == 3
    # 有向差 = (4-5 + 4-4 + 4-3)/3 = 0
    assert abs(out["d7"]["mean_signed_diff"]) < 1e-9
    # diffs=[-1,0,+1]，仅中间一项 |diff|<0.5 → 1/3
    assert out["d7"]["exact_agreement_rate"] == 1 / 3


def test_compare_judges_d8_confusion():
    rule = _rule_d7_result([5, 5])
    rule["d8_per_card"] = [True, False]
    hy3 = _hy3_result([4, 4], [True, True])
    hy3["flag_counts"] = {"implicit_accusation": 1, **{k: 0 for k in D8_FLAGS if k != "implicit_accusation"}}
    out = compare_judges(rule, hy3)
    d8 = out["d8"]
    assert d8["both_violation"] == 1
    assert d8["hy3_only"] == 1
    assert d8["rule_only"] == 0
    assert abs(d8["agreement_rate"] - 0.5) < 1e-9


def test_compare_judges_notes_it_is_not_accuracy():
    out = compare_judges(_rule_d7_result([5]), _hy3_result([5], [False]))
    assert "一致性" in (out.get("note") or "")
