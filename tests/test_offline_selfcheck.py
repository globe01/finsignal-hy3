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


def _counts(cases):
    out = {}
    for c in cases:
        out[c["meta"].get("category", c["kind"])] = out.get(c["meta"].get("category", c["kind"]), 0) + 1
    return out


# ---------------------------------------------------------------- 数据集构成
def test_dataset_composition():
    cases = build_dataset()
    kinds = [c["kind"] for c in cases]
    # 规模要求：≥40 个合成评测窗口（需求二）
    assert len(cases) >= 40, f"样本数 {len(cases)} 不足 40"
    cats = _counts(cases)
    # 类别覆盖（需求三列出的关键类型）
    assert cats.get("negative", 0) >= 4
    assert cats.get("injected", 0) >= 10
    assert cats.get("boundary", 0) >= 1
    assert cats.get("long_text", 0) >= 1
    assert cats.get("jargon", 0) >= 1
    assert cats.get("year_shift", 0) >= 1
    for c in cases:
        if c["kind"] == KIND_INJECTED:
            assert c["base"] is not None and c["injection"] is not None
            assert c["injection"].finalized is True
        else:
            assert c["injection"] is None
    # 低/中/高三档严重度都要有覆盖（需求三）
    sev = {c["meta"].get("severity") for c in cases if c["kind"] == KIND_INJECTED}
    assert {"low", "medium", "high"}.issubset(sev), f"缺少严重度档位：{sev}"


def test_all_injections_valid():
    for c in build_dataset():
        if c["kind"] != KIND_INJECTED:
            continue
        im = c["injection"]
        # 有效性：注入必须真的触发目标信号且恒等式成立；否则金标准会被系统性抬高。
        # 注：periods_match_injection 为 False 只是"规则只在某些年份检出"的信息性标记，
        # 金标准仍以注入年份为准，不影响样本有效性，不在此断言。
        assert im.valid is True, (c["meta"], im.invalid_reasons)
        assert im.target_triggered is True


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
    # D3 严格 10 字段（source_record_id/file/row/column/statement/metric_key/
    # metric_name/period/value/unit）全部 100%
    assert len(agg["D3_field_rates"]) == 10
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
    n = len(build_dataset())
    assert agg["n_cases"] == n
    assert agg["n_cases_scored"] == n
    assert agg["n_cases_excluded"] == 0
    assert agg["excluded_cases"] == []
    assert agg["contaminated_negatives"] == 0
    assert agg["n_errors"] == 0
    assert agg["n_other_cards"] == 0


def test_offline_injection_validity_block(offline_run):
    v = offline_run["aggregate"]["injection_validity"]
    n_injected = sum(1 for c in build_dataset() if c["kind"] == KIND_INJECTED)
    assert v["n_injected"] == n_injected
    assert v["n_invalid"] == 0
    assert v["n_identity_violation"] == 0
    assert v["n_target_not_triggered"] == 0
    # periods_match_injection 为信息性标记（规则只在某些年份检出属正常），仅确认被统计


def test_offline_period_mismatch_is_reported(offline_run):
    # 期间错配应被如实统计并留痕，而非被隐藏
    v = offline_run["aggregate"]["injection_validity"]
    assert "n_period_mismatch" in v
    assert v["n_period_mismatch"] >= 0


def test_offline_layer_counts_separate_sources(offline_run):
    lc = offline_run["aggregate"]["layer_counts"]
    n_injected = sum(1 for c in build_dataset() if c["kind"] == KIND_INJECTED)
    # 每层来源互不混用：注入样本各自贡献其注入目标（injected_ground_truth），
    # 真实/专家层当前为空（无真实样本、无人工标注）。
    assert lc["injected_ground_truth"] == n_injected
    assert lc["expert_confirmed"] == 0
    assert lc["composite_signal"] >= 1   # 至少存在注入传导触发的复合信号
    # 注入样本必须各自携带独立的注入目标金标准项（来自 InjectionMeta，非 rules 反推）：
    # 该 item 的 layer 应为 injected_ground_truth。允许同一注入样本同时存在
    # rule_triggered 的「基底自带信号」与 composite 传导信号——它们都来自合成数据上的
    # 规则 Oracle，是合法的金标准补充，不应被当成「混入规则层」。
    for c in offline_run["cases"]:
        if c["meta"]["kind"] != KIND_INJECTED:
            continue
        own_target = [it for it in c["ground_truth"]["items"]
                      if it.get("injected_ground_truth")]
        assert len(own_target) >= 1, (
            f"注入样本 {c['meta'].get('name')} 的金标准缺少来自 InjectionMeta 的注入目标")
        assert all(it["layer"] == "injected_ground_truth" for it in own_target)


def test_offline_run_config_recorded(offline_run):
    cfg = offline_run["aggregate"]["run_config"]
    assert cfg["offline"] is True
    assert cfg["temperature"] == 0
    assert cfg["period_mode"] == "overlap"
    assert cfg["n_cases"] == len(build_dataset())


def test_sensitivity_views_present(offline_run):
    agg = offline_run["aggregate"]
    for key in ("target_only", "exact_period"):
        assert agg[key]["aggregation"] == "micro"
        assert agg[key]["MRhigh"]["value"] == 0.0
    # 完美模型下严格期间匹配也应满分（证明偏差统计口径没写反）
    assert agg["exact_period"]["R"]["value"] == 1.0


def test_negative_controls_produce_no_cards(offline_run):
    negatives = [c for c in offline_run["cases"] if c["meta"]["kind"] == KIND_NEGATIVE]
    assert len(negatives) >= 4
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
    # limit 取足够大，确保包含带 high 金标准的注入样本（完美模型下 MRhigh 应为 0）
    out = run(offline=True, limit=20, period_mode="exact", write=False)
    assert out["aggregate"]["run_config"]["period_mode"] == "exact"
    assert out["aggregate"]["MRhigh"]["value"] == 0.0
