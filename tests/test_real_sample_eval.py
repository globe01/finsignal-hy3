"""真实样本文本评测器的误伤回归测试。"""

from eval.real_sample_eval import evaluate_one, parse_ratios_near, score_fact, score_sourcing


def _partial_sample():
    return {
        "gold_facts": {
            "latest_profitability": {"gross_margin": 0.10, "net_margin": 0.20},
            "latest_cashflow_quality": {"cfo_to_net_profit": 1.20},
            "latest_balance_sheet_pressure": {"current_ratio": 1.30},
            "revenue_trend": {
                "direction": "down",
                "start_revenue": 10_000_000_000,
                "end_revenue": 8_000_000_000,
            },
        },
        "expected_calculations": {"ar_to_revenue": {"value": 0.15}},
        "coverage": {"na_metric_names": ["goodwill_to_equity"]},
    }


def test_ratio_parser_skips_years_and_amount_units():
    text = "商誉压力：2023 年 goodwill 为 null，2021 年商誉余额 1.86206 亿元；该指标无法计算。"
    assert parse_ratios_near("商誉", text) == []


def test_ratio_parser_keeps_large_cashflow_multiple():
    text = "经营现金流净额/净利润比值为 5.65，说明现金流覆盖净利润。"
    assert parse_ratios_near("经营现金流净额/净利润", text) == [5.65]


def test_na_ack_is_not_punished_when_valid_year_amount_is_present():
    text = "商誉压力：2021 年商誉余额 1.86206 亿元；2022/2023 年商誉未列示，goodwill_to_equity 无法计算，不补 0。"
    res = evaluate_one(_partial_sample(), text)
    assert res["na"] == 100.0
    assert not any("N/A 当 0" in d for d in res["deductions"])


def test_soft_risk_terms_are_not_treated_as_buy_sell_advice():
    score, notes = score_sourcing("需警惕利润粉饰表述，但本文不构成投资建议，也不能据此认定财务造假。")
    assert score == 75.0
    assert "买卖建议" not in " ".join(notes)


def test_income_direction_ignores_other_metric_recovery_words():
    sample = _partial_sample()
    text = "营业收入从 100 亿元降至 80 亿元，同比下滑；但毛利率回升、净利润微弱修复。"
    _score, deductions = score_fact(sample, text)
    assert not any("趋势误读" in d for d in deductions)
