"""四层金标准（方案 §4.2）：来源分层、注入独立、无效样本剔除、真实样本需专家确认。"""
from __future__ import annotations

import copy

import pytest

from eval.ground_truth import (
    KIND_BOUNDARY,
    KIND_INJECTED,
    KIND_NEGATIVE,
    KIND_REAL,
    LAYER_COMPOSITE,
    LAYER_EXPERT,
    LAYER_INJECTED,
    LAYER_RULE,
    build_ground_truth,
    finalize_injection,
)
from generator.base import make_clean_company
from generator.inject import inject
from generator.negative import make_boundary_control, make_negative_control


def _injected(signal_type="receivables_revenue_divergence", strength=2.0):
    base = make_clean_company(name=f"注入_{signal_type}")
    c, meta = inject(base, signal_type, strength=strength)
    finalize_injection(base, c, meta)
    return base, c, meta


# ---------------------------------------------------------------- 注入样本
def test_injected_gold_comes_from_injection_metadata():
    """注入样本的金标准第一来源必须是注入元数据，而不是 rules.py 反推。"""
    base, c, meta = _injected()
    gt = build_ground_truth(c, KIND_INJECTED, meta=meta, base_company=base)

    assert gt["gold_source"] == "injection_metadata"
    target = [i for i in gt["items"] if i["injected_ground_truth"]]
    assert len(target) == 1
    item = target[0]
    assert item["layer"] == LAYER_INJECTED
    assert item["signal_type"] == meta.expected_signal_type
    # 期间取自注入声明，而非规则复算
    assert item["periods"] == list(meta.expected_periods)
    assert item["evidence_record_ids"], "注入金标准必须带被改单元格的 record_id"
    assert "注入元数据" in item["provenance"]


def test_layers_are_mutually_exclusive_in_counting():
    """同一条目不得被两层重复计数（injected 与 composite 必须分开）。"""
    base, c, meta = _injected()
    gt = build_ground_truth(c, KIND_INJECTED, meta=meta, base_company=base)
    lc = gt["layer_counts"]
    assert lc[LAYER_INJECTED] == 1
    assert lc[LAYER_INJECTED] + lc[LAYER_RULE] + lc[LAYER_COMPOSITE] == len(gt["items"])
    for it in gt["items"]:
        assert not (it["injected_ground_truth"] and it["composite_signal"])


def test_composite_signal_split_from_target():
    """复合触发项必须单独标注，并同时提供「仅目标」口径（方案 §5.4）。"""
    base, c, meta = _injected("receivables_revenue_divergence", 2.0)
    gt = build_ground_truth(c, KIND_INJECTED, meta=meta, base_company=base)
    assert meta.composite_signals, "该注入应通过冲减现金流连带触发现金流背离"
    comp = [i for i in gt["items"] if i["composite_signal"]]
    assert comp and all(i["layer"] == LAYER_COMPOSITE for i in comp)
    assert len(gt["gold_target_only"]) == 1
    assert len(gt["gold"]) == 1 + len(comp)


def test_invalid_injection_is_excluded_from_main_metrics():
    """注入未生效的样本必须整例剔除，否则任何模型都必然漏报，MRhigh 被系统性抬高。"""
    base, c, meta = _injected()
    broken = copy.deepcopy(meta)
    broken.target_triggered = False
    broken.valid = False
    broken.invalid_reasons = ["target_not_triggered"]
    gt = build_ground_truth(c, KIND_INJECTED, meta=broken, base_company=base)
    assert gt["case_scored"] is False
    assert gt["gold"] == []
    assert gt["flags"]["invalid_reasons"] == ["target_not_triggered"]
    assert any("不计入主指标" in n for n in gt["notes"])
    # 无效样本的规则触发项只留档，不得计分
    assert all(i["scored"] is False for i in gt["items"])


def test_identity_violation_marks_invalid():
    """恒等式不成立 → finalize 必须判无效。"""
    base, c, meta = _injected()
    meta.identity_ok = False
    meta.identity_violations = ["[2024] 总资产 != 负债+权益"]
    meta.finalized = False
    finalize_injection(base, c, meta)
    assert meta.valid is False
    assert "identity_violation" in meta.invalid_reasons


def test_injected_requires_meta():
    c = make_clean_company()
    with pytest.raises(ValueError):
        build_ground_truth(c, KIND_INJECTED, meta=None)


def test_unfinalized_meta_requires_base_company():
    base = make_clean_company()
    c, meta = inject(base, "goodwill_net_assets_pressure", strength=2.0)
    with pytest.raises(ValueError):
        build_ground_truth(c, KIND_INJECTED, meta=meta, base_company=None)


# ---------------------------------------------------------------- 阴性 / 阈下
def test_clean_negative_control_has_empty_gold():
    gt = build_ground_truth(make_negative_control(), KIND_NEGATIVE)
    assert gt["gold"] == []
    assert gt["case_scored"] is True
    assert gt["flags"]["contaminated"] is False


def test_contaminated_negative_is_exposed_not_hidden():
    """阴性对照被触发时必须暴露污染并把信号计入金标准，否则模型报对反算误报。"""
    c = make_clean_company(name="被污染的阴性对照")
    for t in c.years:
        c.balance["goodwill"][t] = c.balance["equity"][t] * 0.5
    gt = build_ground_truth(c, KIND_NEGATIVE)
    assert gt["flags"]["contaminated"] is True
    assert gt["flags"]["n_contaminating_signals"] >= 1
    assert gt["gold"], "污染信号在数据上成立，必须进入金标准"
    assert all(i["layer"] == LAYER_RULE for i in gt["items"])


def test_boundary_control_uses_rule_oracle():
    gt = build_ground_truth(make_boundary_control("receivables_revenue_divergence"),
                            KIND_BOUNDARY)
    assert gt["gold_source"] == "rule_oracle_on_synthetic"
    assert gt["flags"]["boundary_unexpected_high"] == []


# ---------------------------------------------------------------- 真实样本
def test_real_sample_without_expert_label_is_not_scored():
    """真实样本的规则触发只是候选；无专家确认则整例不计入主指标（方案 §4.2）。"""
    c = make_clean_company(name="真实样本")
    for t in c.years:
        c.balance["goodwill"][t] = c.balance["equity"][t] * 0.5
    gt = build_ground_truth(c, KIND_REAL)
    assert gt["gold_source"] == "expert_confirmed_only"
    assert gt["case_scored"] is False
    assert gt["gold"] == []
    assert gt["flags"]["n_candidates"] >= 1
    assert gt["flags"]["n_expert_confirmed"] == 0
    assert all("awaiting_expert_confirmation" in i["notes"] for i in gt["items"])


def test_real_sample_with_expert_confirmation_is_scored():
    c = make_clean_company(name="真实样本-已确认")
    for t in c.years:
        c.balance["goodwill"][t] = c.balance["equity"][t] * 0.5
    from eval.rules import compute_all_signals
    cand = compute_all_signals(c)
    labels = [{"signal_type": cand[0]["signal_type"], "periods": cand[0]["periods"],
               "expert_confirmed": True}]
    gt = build_ground_truth(c, KIND_REAL, expert_labels=labels)
    assert gt["case_scored"] is True
    assert len(gt["gold"]) == 1
    assert gt["layer_counts"][LAYER_EXPERT] == 1


def test_expert_rejected_candidate_is_not_scored():
    c = make_clean_company(name="真实样本-已否决")
    for t in c.years:
        c.balance["goodwill"][t] = c.balance["equity"][t] * 0.5
    from eval.rules import compute_all_signals
    cand = compute_all_signals(c)
    labels = [{"signal_type": cand[0]["signal_type"], "periods": cand[0]["periods"],
               "expert_confirmed": False}]
    gt = build_ground_truth(c, KIND_REAL, expert_labels=labels)
    assert gt["case_scored"] is False
    assert gt["gold"] == []
    assert gt["items"][0]["expert_confirmed"] is False


def test_unknown_kind_rejected(clean_company):
    with pytest.raises(ValueError):
        build_ground_truth(clean_company, "unknown_kind")
