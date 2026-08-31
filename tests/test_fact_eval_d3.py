"""D1 数值准确性 与 D3 严格证据可追溯（方案 §5.3、§6.1）。

关键断言：D3 必须卡到记录级——只有 source_record_id 回表成功且 8 个字段全对才算通过。
「科目名和年份存在」这种降级定位只能算 D1 命中，不得计入 D3。
"""
from __future__ import annotations

from app.schema import AnomalyCard, FactBasis
from eval.fact_eval import D3_FIELDS, aggregate_facts, eval_facts


def _fact_from_record(rec, **override) -> FactBasis:
    data = dict(
        fact_id=f"{rec.metric_key}_{rec.period}",
        metric_key=rec.metric_key, metric_name=rec.metric_name,
        period=rec.period, value=rec.value, unit=rec.unit,
        source_record_id=rec.record_id, source_file=rec.source_file,
        source_row=rec.source_row, source_column=rec.source_column,
        statement=rec.statement,
    )
    data.update(override)
    return FactBasis(**data)


def _card(facts) -> AnomalyCard:
    return AnomalyCard(signal_type="receivables_revenue_divergence",
                       severity="high", periods=["2024"], fact_basis=facts)


def _rec(record_index, rid):
    return record_index[rid]


def test_full_evidence_chain_passes_d1_and_d3(clean_company, record_index):
    rec = _rec(record_index, "BS_R03_2024")          # 应收账款 2024
    r = eval_facts(_card([_fact_from_record(rec)]), clean_company)
    assert r["d1_hits"] == 1
    assert r["d3_strict_hits"] == 1
    assert r["resolved_by_record_id"] == 1
    assert all(r["field_hits"][f] == 1 for f in D3_FIELDS)


def test_missing_source_record_id_is_not_strictly_traceable(clean_company, record_index):
    """只给科目名+年份 → 可降级定位（D1 可命中），但 D3 严格口径必须判不通过。"""
    rec = _rec(record_index, "BS_R03_2024")
    fact = _fact_from_record(rec, source_record_id=None)
    r = eval_facts(_card([fact]), clean_company)
    assert r["d1_hits"] == 1                      # 数值仍可核对
    assert r["d3_strict_hits"] == 0               # 但证据链不完整
    assert r["resolved_by_fallback"] == 1
    assert r["resolved_by_record_id"] == 0


def test_fabricated_record_id_falls_back_not_pass(clean_company, record_index):
    """模型自造 ID（如 bs_2024）不得计入严格可追溯。"""
    rec = _rec(record_index, "BS_R03_2024")
    fact = _fact_from_record(rec, source_record_id="bs_2024")
    r = eval_facts(_card([fact]), clean_company)
    assert r["d3_strict_hits"] == 0
    assert r["resolved_by_fallback"] == 1
    assert r["checks"][0]["field_ok"]["source_record_id"] is False


def test_wrong_value_fails_d1_and_d3(clean_company, record_index):
    rec = _rec(record_index, "BS_R03_2024")
    r = eval_facts(_card([_fact_from_record(rec, value=rec.value * 2)]), clean_company)
    assert r["d1_hits"] == 0
    assert r["d3_strict_hits"] == 0
    assert r["checks"][0]["field_ok"]["value"] is False


def test_value_within_one_percent_tolerance_passes(clean_company, record_index):
    """方案 §6.1：相对误差 <=1% 视为命中（容忍四舍五入）。"""
    rec = _rec(record_index, "BS_R03_2024")
    r = eval_facts(_card([_fact_from_record(rec, value=rec.value * 1.005)]), clean_company)
    assert r["d1_hits"] == 1
    assert r["d3_strict_hits"] == 1


def test_wrong_row_or_file_fails_d3_only(clean_company, record_index):
    rec = _rec(record_index, "BS_R03_2024")
    r = eval_facts(_card([_fact_from_record(rec, source_row=99)]), clean_company)
    assert r["d1_hits"] == 1
    assert r["d3_strict_hits"] == 0
    assert r["checks"][0]["field_ok"]["source_row"] is False

    r2 = eval_facts(_card([_fact_from_record(rec, source_file="wrong.csv")]), clean_company)
    assert r2["d3_strict_hits"] == 0
    assert r2["checks"][0]["field_ok"]["source_file"] is False


def test_statement_aliases_are_tolerated(clean_company, record_index):
    """statement 写成中文报表名或文件名仍算一致（只是命名习惯差异）。"""
    rec = _rec(record_index, "BS_R03_2024")
    for alias in ("资产负债表", "balance_sheet.csv", "BS", "Balance"):
        r = eval_facts(_card([_fact_from_record(rec, statement=alias)]), clean_company)
        assert r["d3_strict_hits"] == 1, alias


def test_row_id_style_source_row_is_tolerated(clean_company, record_index):
    rec = _rec(record_index, "BS_R03_2024")
    for raw in ("3", "BS_R03", "第3行"):
        r = eval_facts(_card([_fact_from_record(rec, source_row=raw)]), clean_company)
        assert r["d3_strict_hits"] == 1, raw


def test_metric_key_recovered_from_chinese_name(clean_company, record_index):
    rec = _rec(record_index, "BS_R03_2024")
    r = eval_facts(_card([_fact_from_record(rec, metric_key=None)]), clean_company)
    assert r["d3_strict_hits"] == 1


def test_unresolvable_fact_is_counted(clean_company):
    fact = FactBasis(metric_name="不存在的科目", period="2024", value=1.0,
                     source_record_id="XX_R99_2024")
    r = eval_facts(_card([fact]), clean_company)
    assert r["unresolved"] == 1
    assert r["d1_hits"] == 0 and r["d3_strict_hits"] == 0
    assert "无法定位源记录" in r["checks"][0]["issues"]


def test_card_with_no_facts_yields_zero_denominator(clean_company):
    r = eval_facts(_card([]), clean_company)
    assert r["n_facts"] == 0 and r["d1_total"] == 0 and r["d3_strict_total"] == 0


def test_aggregate_facts_micro_and_na(clean_company, record_index):
    rec = _rec(record_index, "BS_R03_2024")
    good = _card([_fact_from_record(rec)])
    bad = _card([_fact_from_record(rec, source_record_id=None)])
    agg = aggregate_facts([good, bad], clean_company)
    assert agg["d1_hits"] == 2 and agg["d1_total"] == 2 and agg["d1_rate"] == 1.0
    assert agg["d3_strict_hits"] == 1 and agg["d3_strict_total"] == 2
    assert agg["d3_strict_rate"] == 0.5
    assert agg["resolved_by_record_id"] == 1 and agg["resolved_by_fallback"] == 1

    empty = aggregate_facts([], clean_company)
    assert empty["d1_rate"] is None and empty["d3_strict_rate"] is None
