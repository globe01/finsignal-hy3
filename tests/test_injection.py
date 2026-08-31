"""注入引擎：恒等式守恒 + 元数据完整性 + 注入确实生效（方案 §5.4）。

这些断言直接对应本轮修复的核心：
- 注入必须保持三条会计恒等式；
- 每个主动改动的单元格都要带可回表的 source_record_id；
- 注入声明的年份必须在数据上真的成立（曾出现「第 2 年被自己抹平」的 bug）；
- 强度越大严重度不得下降。
"""
from __future__ import annotations

import pytest

from eval.ground_truth import finalize_injection
from eval.rules import severity_weight
from generator.base import build_record_index, make_clean_company
from generator.inject import INJECTABLE, ROLE_PRIMARY, inject
from generator.invariants import check_identities

TYPES = sorted(INJECTABLE)
STRENGTHS = (1.0, 2.0)


def _make(signal_type: str, strength: float):
    base = make_clean_company(name=f"注入_{signal_type}_{strength}")
    injected, meta = inject(base, signal_type, strength=strength)
    finalize_injection(base, injected, meta)
    return base, injected, meta


@pytest.mark.parametrize("signal_type", TYPES)
@pytest.mark.parametrize("strength", STRENGTHS)
def test_identities_hold_after_injection(signal_type, strength):
    """注入后三表必须仍然勾稽一致，否则样本无效（方案 §5.4）。"""
    _, injected, meta = _make(signal_type, strength)
    assert check_identities(injected) == []
    assert meta.identity_ok is True
    assert meta.identity_violations == []


@pytest.mark.parametrize("signal_type", TYPES)
@pytest.mark.parametrize("strength", STRENGTHS)
def test_injection_actually_triggers_target(signal_type, strength):
    """注入必须真的让目标信号成立；否则金标准会制造必然漏报。"""
    _, _, meta = _make(signal_type, strength)
    assert meta.finalized is True
    assert meta.target_triggered is True, meta.invalid_reasons
    assert meta.valid is True, meta.invalid_reasons
    assert meta.expected_signal_type == signal_type


@pytest.mark.parametrize("signal_type", TYPES)
@pytest.mark.parametrize("strength", STRENGTHS)
def test_injected_periods_match_rule_periods(signal_type, strength):
    """回归：注入声明的年份必须与规则复算年份一致。

    历史 bug —— 增速类注入第 2 年基于「原始」上年值计算，而上年值已被本次注入抬高，
    导致第 2 年背离被自己抹平（实测塌到 −0.09pp），规则只在第 1 年触发。
    """
    _, _, meta = _make(signal_type, strength)
    assert set(meta.target_periods_rule) == set(meta.expected_periods), (
        f"注入年份 {meta.expected_periods} 与规则触发年份 {meta.target_periods_rule} 不一致"
    )
    assert meta.periods_match_injection is True
    assert len(meta.expected_periods) == 2  # 连续两年注入


@pytest.mark.parametrize("signal_type", TYPES)
def test_severity_monotonic_in_strength(signal_type):
    """强度更大 → 严重度标签不得更轻（方案 §5.5 可复现严重度）。"""
    _, _, m1 = _make(signal_type, 1.0)
    _, _, m2 = _make(signal_type, 2.0)
    assert severity_weight(m2.target_severity) >= severity_weight(m1.target_severity)


@pytest.mark.parametrize("signal_type", TYPES)
def test_primary_edits_are_traceable(signal_type):
    """每个主动改动的单元格都必须能回表（方案 §5.3 单元格级证据）。"""
    _, injected, meta = _make(signal_type, 2.0)
    idx = build_record_index(injected)
    primary = meta.primary_edits()
    assert primary, "注入必须至少记录一个 primary 改动"
    for e in primary:
        assert e.role == ROLE_PRIMARY
        assert e.source_record_id, f"{e.metric_key} 缺 source_record_id"
        assert e.source_record_id in idx, f"{e.source_record_id} 无法回表"
        assert e.value_before != e.value_after
        assert e.delta == pytest.approx(e.value_after - e.value_before)
        # 改后值必须与注入后数据一致（元数据不能与数据脱节）
        assert idx[e.source_record_id].value == pytest.approx(e.value_after, rel=1e-9)


@pytest.mark.parametrize("signal_type", TYPES)
def test_meta_records_intent_before_any_rule_recompute(signal_type):
    """注入意图字段必须在不依赖规则复算的情况下就已确定（金标准独立性）。"""
    base = make_clean_company(name="意图检查")
    _, meta = inject(base, signal_type, strength=1.0)   # 故意不 finalize
    assert meta.signal_type == signal_type
    assert meta.expected_signal_type == signal_type
    assert meta.injection_start_year == base.years[-2]
    assert meta.injected_years == [str(y) for y in base.years[-2:]]
    assert meta.expected_periods == meta.injected_years
    assert meta.edits, "注入必须记录单元格改动"
    assert meta.finalized is False          # 未 finalize 时不得声称已知触发结果
    assert meta.target_triggered is False
    assert meta.post_triggered == []


def test_unknown_injection_type_rejected(clean_company):
    with pytest.raises(ValueError):
        inject(clean_company, "not_an_injectable_signal", strength=1.0)


def test_injection_does_not_mutate_base():
    """注入必须作用在副本上，基底不能被就地改写（否则基线对比失真）。"""
    base = make_clean_company(name="不可变基底")
    before = {t: base.balance["accounts_receivable"][t] for t in base.years}
    inject(base, "receivables_revenue_divergence", strength=2.0)
    after = {t: base.balance["accounts_receivable"][t] for t in base.years}
    assert before == after


def test_derived_edits_recorded():
    """reconcile 造成的派生变化必须留档，便于解释「为什么这个数也变了」。"""
    _, _, meta = _make("receivables_revenue_divergence", 2.0)
    assert meta.derived_edits, "应记录 reconcile 派生改动（货币资金/总资产/权益等）"
    keys = {e.metric_key for e in meta.derived_edits}
    assert {"total_assets", "equity"} & keys
