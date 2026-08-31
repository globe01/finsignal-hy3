"""Schema 容错（方案 §5.1）：非法枚举回退、数值字符串归一、未知字段忽略。

目的：模型偶发的格式抖动不应该让整例解析失败（那会把「格式问题」记成「漏报」）。
但回退必须是**可见的**：非法 signal_type 回退到 other，而 other 不计入主指标。
"""
from __future__ import annotations

import pytest

from app.schema import (
    SIGNAL_TYPES,
    AnomalyCard,
    Calculation,
    FactBasis,
    ScanOutput,
    Severity,
    SignalType,
)


def test_illegal_signal_type_falls_back_to_other():
    card = AnomalyCard(signal_type="应收账款异常")
    assert card.signal_type == SignalType.other


def test_legal_signal_types_preserved():
    for st in SIGNAL_TYPES:
        assert AnomalyCard(signal_type=st).signal_type.value == st


def test_illegal_severity_falls_back_to_medium():
    assert AnomalyCard(severity="critical").severity == Severity.medium
    assert AnomalyCard(severity=None).severity == Severity.medium


def test_value_string_coercion():
    assert FactBasis(value="1,100,000").value == pytest.approx(1100000.0)
    assert FactBasis(value="12.3%").value == pytest.approx(12.3)
    assert FactBasis(value="250 元").value == pytest.approx(250.0)
    assert FactBasis(value="不适用").value is None
    assert FactBasis(value=None).value is None
    assert FactBasis(value=True).value is None       # 布尔不得被当成 1


def test_reported_result_coercion():
    assert Calculation(reported_result="0.37").reported_result == pytest.approx(0.37)
    assert Calculation(reported_result="37%").reported_result == pytest.approx(37.0)
    assert Calculation(reported_result="约三成").reported_result is None


def test_extra_fields_are_ignored():
    card = AnomalyCard(signal_type="other", 置信度="高", extra_field=[1, 2, 3])
    assert card.signal_type == SignalType.other
    assert not hasattr(card, "置信度")


def test_missing_optional_evidence_fields_do_not_break_parsing():
    card = AnomalyCard(signal_type="goodwill_net_assets_pressure",
                       fact_basis=[{"metric_name": "商誉", "period": "2024", "value": 100}])
    assert len(card.fact_basis) == 1
    assert card.fact_basis[0].source_record_id is None
    assert card.calculation is None


def test_scan_output_defaults_to_empty_cards():
    assert ScanOutput().cards == []
    assert ScanOutput(**{"cards": []}).cards == []


def test_scan_output_parses_nested_payload():
    payload = {
        "cards": [{
            "signal_type": "cashflow_profit_divergence",
            "signal_name": "现金流与净利润背离",
            "severity": "high",
            "periods": ["2023", "2024"],
            "fact_basis": [{"metric_key": "cfo", "metric_name": "经营活动现金流量净额",
                            "period": "2024", "value": "30", "source_record_id": "CF_R02_2024"}],
            "calculation": {"formula_id": "cfo_over_netprofit", "reported_result": "0.3"},
            "conclusion_boundary": "不能据此认定财务造假。",
            "无关字段": 1,
        }]
    }
    out = ScanOutput(**payload)
    assert out.cards[0].signal_type == SignalType.cashflow_profit_divergence
    assert out.cards[0].fact_basis[0].value == pytest.approx(30.0)
    assert out.cards[0].calculation.reported_result == pytest.approx(0.3)


def test_periods_accept_string_years():
    card = AnomalyCard(signal_type="other", periods=["2023", "2024"])
    assert card.periods == ["2023", "2024"]
