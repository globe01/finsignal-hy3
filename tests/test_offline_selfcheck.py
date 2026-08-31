"""离线自检：用金标准构造「完美模型」跑通全链路（方案 §6.4）。

含义：把金标准原样当成模型输出（附带真实证据链），指标数学与证据核验若正确，
必须得到 MRhigh=0%、P/R/Rw=100%、D1/D2/D3=100%。
任一项不达标说明**评估器自身有 bug**，而不是模型不行 —— 这条测试把这个判据固化下来，
以后线上跑出的任何非满分都能归因到模型行为。

该测试不联网、不导入 openai，也不需要 API Key。
"""
from __future__ import annotations

import pytest

from eval.ground_truth import KIND_BOUNDARY, KIND_INJECTED, KIND_NEGATIVE
from eval.run_eval import INJECT_TYPES, build_dataset, run


@pytest.fixture(scope="module")
def offline_run():
    return run(offline=True, write=False)


# ---------------------------------------------------------------- 数据集构成
def test_dataset_composition():
    cases = build_dataset()
    kinds = [c["kind"] for c in cases]
    assert len(cases) == 14
    assert kinds.count(KIND_NEGATIVE) == 2
    assert kinds.count(KIND_INJECTED) == len(INJECT_TYPES) * 2
    assert kinds.count(KIND_BOUNDARY) == 2
    for c in cases:
        if c["kind"] == KIND_INJECTED:
            assert c["base"] is not None and c["injection"] is not None
            assert c["injection"].finalized is True
        else:
            assert c["injection"] is None


def test_all_injections_valid():
    for c in build_dataset():
        if c["kind"] != KIND_INJECTED:
            continue
        im = c["injection"]
        assert im.valid is True, (c["meta"], im.invalid_reasons)
        assert im.target_triggered is True
        assert im.periods_match_injection is True


# ---------------------------------------------------------------- 集合级指标
def test_offline_set_metrics_are_perfect(offline_run):
    agg = offline_run["aggregate"]
    assert agg["aggregation"] == "micro"
    assert agg["MRhigh"]["value"] == 0.0, agg["MRhigh"]
    assert agg["MRhigh"]["den"] > 0, "自检必须包含 high 金标准，否则主指标退化为 N/A"
    assert agg["P"]["value"] == 1.0
    assert agg["R"]["value"] == 1.0
    assert agg["Rw"]["value"] == 1.0
    assert agg["FP"] == 0 and agg["FN"] == 0
    assert agg["fn_modes"] == {}
    assert agg["period_shifted_tp"] == 0, "完美模型应逐年精确匹配"


def test_offline_evidence_dimensions_are_perfect(offline_run):
    agg = offline_run["aggregate"]
    assert agg["D1_value_accuracy"]["value"] == 1.0
    assert agg["D2_formula_correctness"]["value"] == 1.0
    assert agg["D3_strict_traceability"]["value"] == 1.0
    # 全部靠 source_record_id 定位，不允许有降级或未定位
    assert agg["D3_resolution"]["by_fallback"] == 0
    assert agg["D3_resolution"]["unresolved"] == 0
    assert agg["D3_resolution"]["by_source_record_id"] == agg["D3_strict_traceability"]["den"]
    assert all(v["value"] == 1.0 for v in agg["D3_field_rates"].values())
    assert agg["D6_severity_agreement"]["value"] == 1.0
    assert agg["D2_scale_adjusted"] == 0


def test_offline_compliance_dimensions(offline_run):
    agg = offline_run["aggregate"]
    assert agg["D8_compliance"]["value"] == 1.0
    assert agg["D8_compliance"]["method"] == "rule_rubric_heuristic"
    assert agg["D7_rule_rubric_mean"]["method"] == "rule_rubric_heuristic"
    assert agg["D7_rule_rubric_mean"]["value"] == pytest.approx(5.0)


def test_offline_no_case_excluded_and_no_contamination(offline_run):
    agg = offline_run["aggregate"]
    assert agg["n_cases"] == 14
    assert agg["n_cases_scored"] == 14
    assert agg["n_cases_excluded"] == 0
    assert agg["excluded_cases"] == []
    assert agg["contaminated_negatives"] == 0
    assert agg["n_errors"] == 0
    assert agg["n_other_cards"] == 0


def test_offline_injection_validity_block(offline_run):
    v = offline_run["aggregate"]["injection_validity"]
    assert v["n_injected"] == len(INJECT_TYPES) * 2
    assert v["n_invalid"] == 0
    assert v["n_identity_violation"] == 0
    assert v["n_target_not_triggered"] == 0
    assert v["n_period_mismatch"] == 0


def test_offline_layer_counts_separate_sources(offline_run):
    lc = offline_run["aggregate"]["layer_counts"]
    assert lc["injected_ground_truth"] == len(INJECT_TYPES) * 2
    # 注入侧金标准不得来自规则反推
    assert lc["rule_triggered"] == 0
    assert lc["expert_confirmed"] == 0
    assert lc["composite_signal"] >= 1


def test_offline_run_config_recorded(offline_run):
    cfg = offline_run["aggregate"]["run_config"]
    assert cfg["offline"] is True
    assert cfg["temperature"] == 0
    assert cfg["period_mode"] == "overlap"
    assert cfg["n_cases"] == 14


def test_sensitivity_views_present(offline_run):
    agg = offline_run["aggregate"]
    for key in ("target_only", "exact_period"):
        assert agg[key]["aggregation"] == "micro"
        assert agg[key]["MRhigh"]["value"] == 0.0
    # 完美模型下严格期间匹配也应满分（证明偏差统计口径没写反）
    assert agg["exact_period"]["R"]["value"] == 1.0


def test_negative_controls_produce_no_cards(offline_run):
    negatives = [c for c in offline_run["cases"] if c["meta"]["kind"] == KIND_NEGATIVE]
    assert len(negatives) == 2
    for c in negatives:
        assert c["gold"] == []
        assert c["n_cards"] == 0
        assert c["metrics"]["FP"] == 0
        assert c["metrics"]["MRhigh"] is None      # 无金标准 → N/A，不得记 0


def test_limit_option_subsets_dataset():
    out = run(offline=True, limit=3, write=False)
    assert out["aggregate"]["n_cases"] == 3
    assert out["aggregate"]["run_config"]["limit"] == 3


def test_exact_period_mode_runs(offline_run):
    out = run(offline=True, limit=4, period_mode="exact", write=False)
    assert out["aggregate"]["run_config"]["period_mode"] == "exact"
    assert out["aggregate"]["MRhigh"]["value"] == 0.0
