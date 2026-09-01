"""盲评相关测试（提交前收口：任务一 / 任务三的代码级验证）。

- ``_template_rows``：每个 ``(case_id, card_id)`` 全局唯一，且 ``severity_label`` 留空
  （不泄露模型严重度，标注者独立判定）。
- ``export_blind``：盲评条目只含 ``case_id`` / ``input_text`` / ``cards``，
  递归扫描不得出现任何金标准 / 评估字段（ground_truth / gold / injection / metrics /
  judge / hy3_judge / meta）。
- Hy3-as-Judge 接线：注入一个**合成** judge 结果，验证 ``evaluate_case(hy3_judge=True)``
  与 ``aggregate`` 能产出非 null 的 ``D7_hy3`` / ``D8_hy3`` / ``compare_judges``——
  证明代码路径正确（真实数值仍需联网 + Key 跑 ``--limit 5 --hy3-judge`` 取得，不在此编造）。

全部离线、不调用 API、不导入 openai（仅 monkeypatch）。
"""
from __future__ import annotations

import types

from app.schema import AnomalyCard
from eval.run_eval import _perfect_cards, aggregate, build_dataset, evaluate_case
from eval.validity.agreement import _template_rows
from eval.validity import export_blind

_FORBIDDEN_KEYS = {
    "ground_truth", "gold", "injection", "metrics", "judge",
    "hy3_judge", "meta", "injected_ground_truth", "rule_triggered",
    "expert_confirmed", "composite_signal",
}


def _synthetic_cases_with_repeat_signal():
    """构造一个 case 内出现重复 signal_type 的样本，验证 card_id 唯一性。"""
    return [
        {
            "meta": {"name": "X", "inject": "receivables_revenue_divergence",
                     "severity": "high", "category": "injected"},
            "cards_full": [
                {"signal_type": "receivables_revenue_divergence", "periods": ["2023"],
                 "severity": "high"},
                {"signal_type": "receivables_revenue_divergence", "periods": ["2023"],
                 "severity": "high"},  # 故意重复 signal_type
            ],
        },
        {
            "meta": {"name": "Y", "inject": "inventory_cost_divergence"},
            "cards_full": [
                {"signal_type": "inventory_cost_divergence", "periods": ["2022-2023"]},
            ],
        },
    ]


def test_template_card_id_unique_and_anonymized():
    cases = _synthetic_cases_with_repeat_signal()
    rows = _template_rows(cases)
    keys = [(r["case_id"], r["card_id"]) for r in rows]
    assert len(keys) == len(set(keys)), "存在重复的 (case_id, card_id)"
    # case_id 匿名顺序号
    assert {r["case_id"] for r in rows} == {"case_000", "case_001"}
    # 同一样本内稳定卡片序号
    case0 = [r for r in rows if r["case_id"] == "case_000"]
    assert [r["card_id"] for r in case0] == ["card_000", "card_001"]
    # severity_label 留空（标注者独立判定）
    assert all(r["severity_label"] == "" for r in rows)
    # 不泄露样本身份（原 meta.name 不应出现在输出）
    assert all("X" not in r.values() and "Y" not in r.values() for r in rows)


def _recursive_forbidden_scan(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k not in _FORBIDDEN_KEYS, f"盲评输出泄漏了禁止字段：{k}"
            _recursive_forbidden_scan(v)
    elif isinstance(obj, list):
        for v in obj:
            _recursive_forbidden_scan(v)


def test_export_blind_excludes_gold_and_eval_fields():
    case = {
        "meta": {"name": "X", "inject": "receivables_revenue_divergence",
                 "severity": "high", "category": "injected"},
        "ground_truth": {"gold": [{"signal_type": "receivables_revenue_divergence"}]},
        "gold": [{"signal_type": "receivables_revenue_divergence"}],
        "injection": {"target_severity": "high"},
        "metrics": {"MRhigh": 0.1},
        "judge": {"n_cards": 1},
        "hy3_judge": {"d7_mean": 4.0},
        "cards_full": [
            {"signal_type": "receivables_revenue_divergence", "periods": ["2023"],
             "severity": "high"},
        ],
    }
    company_lookup = {("X", "receivables_revenue_divergence"): object()}
    # monkeypatch company_to_text，避免依赖 generator 真实构建
    orig = export_blind.company_to_text
    export_blind.company_to_text = lambda c: "FAKE_FINANCIAL_INPUT"
    try:
        rows = export_blind.export_blind([case], company_lookup=company_lookup)
    finally:
        export_blind.company_to_text = orig

    assert len(rows) == 1
    entry = rows[0]
    assert set(entry.keys()) == {"case_id", "input_text", "cards"}
    assert entry["input_text"] == "FAKE_FINANCIAL_INPUT"
    assert entry["cards"] == case["cards_full"]
    _recursive_forbidden_scan(entry)  # 含嵌套卡片，均不得含禁止字段


def _fake_hy3_judge(cards, text, chat_json=None):
    """合成 judge 结果（不联网），结构对齐 ``judge_cards_with_hy3`` 输出。"""
    n = len(cards)
    per_card = [{"d7_mean": 4.0, "violated": False} for _ in range(n)]
    return {
        "method": "hy3_as_judge_semantic",
        "n_cards": n, "n_scored": n, "n_errors": 0,
        "d7_mean": 4.0, "d7_sum": 4.0 * n,
        "d7_subdim_means": {k: 4.0 for k in (
            "evidence_grounding", "alternative_explanations",
            "actionable_checks", "boundary_calibration")},
        "d8_violations": 0, "d8_violation_rate": 0.0, "d8_compliance_rate": 1.0,
        "flag_counts": {k: 0 for k in (
            "investment_advice", "target_price", "fraud_determination",
            "alarmist_prediction", "implicit_accusation")},
        "n_unverifiable_deductions": 0, "per_card": per_card,
    }


def test_hy3_judge_wiring_produces_non_null():
    """验证 Hy3-as-Judge 接线在给定 judge 结果时能产出非 null 的 D7_hy3/D8_hy3/一致性。"""
    from eval.ground_truth import build_ground_truth

    # 取一个注入样本作为真实 case（评估器/金标准路径都真实，仅 judge 调用被替换）
    injected = next(c for c in build_dataset() if c["kind"] == "injected")
    gt = build_ground_truth(injected["company"], injected["kind"],
                            meta=injected.get("injection"),
                            base_company=injected.get("base"))
    cards = _perfect_cards(gt["gold"], injected["company"])

    orig = evaluate_case.__globals__["judge_cards_with_hy3"]
    evaluate_case.__globals__["judge_cards_with_hy3"] = _fake_hy3_judge
    try:
        result = evaluate_case(injected, cards, hy3_judge=True)
        agg = aggregate([result])
    finally:
        evaluate_case.__globals__["judge_cards_with_hy3"] = orig

    # 单样本层面：两个 judge 字段与一致性均应非 null
    assert result["hy3_judge"] is not None, "hy3_judge 不应为 null"
    assert result["judge_agreement"] is not None, "compare_judges 不应为 null"
    # 聚合层面：D7_hy3 / D8_hy3 非 null（真实数值联网后才会填入）
    assert agg["D7_hy3_judge_mean"]["value"] is not None, "D7_hy3 不应为 null"
    assert agg["D8_compliance_hy3_judge"]["value"] is not None, "D8_hy3 不应为 null"
