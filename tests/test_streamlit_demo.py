"""Streamlit Demo 主流程测试（对应交付要求「五、最小可用 Demo」的健壮性）。

验证点：
- 含 calculation（且 formula_id 命中注册表、走到 D2 步骤 4）的卡片在 company=None 下
  **不崩溃**（修复前会因访问 company.years 抛 AttributeError）。
- Demo 校验面板的纯计算逻辑 `_compute_checks` 对任意卡片组合都可安全运行。
- 无结构化 Company 时 D2 进入 structure_only 模式并给出「公式结构检查」计数。

不依赖真实 API / .env：只测本地校验逻辑，Hy3 调用由 scan_text 触发、不在本测试内。
"""
from __future__ import annotations

from app.schema import AnomalyCard, Calculation, FactBasis, ScanOutput, Severity, SignalType
from app.streamlit_app import _compute_checks


def _card_with_calculation() -> AnomalyCard:
    """一张会走到 D2 步骤 4 的卡片：cashflow 信号 + 合法 formula + 操作数齐备。"""
    return AnomalyCard(
        signal_type=SignalType.cashflow_profit_divergence,
        severity=Severity.high,
        periods=["2023"],
        fact_basis=[
            FactBasis(metric_key="cfo", metric_name="经营现金流", period="2023",
                      value=100.0, unit="万元"),
            FactBasis(metric_key="net_profit", metric_name="净利润", period="2023",
                      value=50.0, unit="万元"),
        ],
        calculation=Calculation(
            formula_id="cfo_over_netprofit",
            reported_result=2.0,
            readable="经营现金流 / 净利润 = 100 / 50 = 2.0",
        ),
    )


def _card_without_calculation() -> AnomalyCard:
    return AnomalyCard(
        signal_type=SignalType.receivables_revenue_divergence,
        severity=Severity.medium,
        periods=["2023"],
        fact_basis=[
            FactBasis(metric_key="accounts_receivable", period="2023", value=80.0, unit="万元"),
            FactBasis(metric_key="revenue", period="2023", value=200.0, unit="万元"),
        ],
    )


def _card_other() -> AnomalyCard:
    return AnomalyCard(
        signal_type=SignalType.other,
        severity=Severity.low,
        calculation=Calculation(formula_id="cfo_over_netprofit", reported_result=1.0),
    )


def test_demo_main_flow_with_calculation_card_no_crash():
    """含 calculation 的卡片不能让 Demo 主流程崩溃（回归此前 company.years 报错）。"""
    out = ScanOutput(cards=[_card_with_calculation()])
    checks = _compute_checks(out)  # 若崩溃会抛出 AttributeError，测试失败
    assert checks["d2"]["d2_mode"] == "structure_only"


def test_demo_d2_structure_only_counts_valid_card():
    """structure_only 模式应把命中前 3 步的卡片计入公式结构通过率。"""
    out = ScanOutput(cards=[_card_with_calculation()])
    d2 = _compute_checks(out)["d2"]
    assert d2["d2_mode"] == "structure_only"
    assert d2["d2_struct_total"] == 1
    # formula_known / signal_match / operands_ok 三者皆 True
    assert d2["d2_struct_pass"] == 1
    # 算术复算未执行，不计入 pass/fail 分母
    assert d2["d2_total"] == 0


def test_demo_main_flow_mixed_cards_no_crash():
    """混合卡片（含 calc / 不含 calc / other）整体不崩溃。"""
    out = ScanOutput(cards=[
        _card_with_calculation(),
        _card_without_calculation(),
        _card_other(),
    ])
    checks = _compute_checks(out)
    # 三张全部到达过步骤 1（有 formula_id 或 other 不计），不崩即可
    assert checks["d2"]["d2_mode"] == "structure_only"
    # 结构通过数 = 仅含 calc 且前 3 步通过的卡片（1 张）
    assert checks["d2"]["d2_struct_pass"] == 1
    assert checks["d1"]["total"] == 4  # 2 + 2 + 0 张 fact
    assert checks["judge"]["n_cards"] == 3


def test_demo_empty_output_no_crash():
    """空结果（阴性）也不崩溃。"""
    out = ScanOutput(cards=[])
    checks = _compute_checks(out)
    assert checks["judge"]["n_cards"] == 0
    assert checks["d2"]["d2_struct_total"] == 0
