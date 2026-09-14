"""人工一致性计算模块的自检（不提交任何伪造标注数据）。

- 空/单标注者数据必须返回 status=pending，绝不编造 Cohen's κ / Spearman 数值；
- 合成两份标注数据必须算出 kappa / spearman / 重复评估波动，且退出码为 0。
"""
from __future__ import annotations

import json
import math

import pandas as pd

from eval.validity.agreement import _json_safe, compute_agreement


def _df(rows):
    cols = ["case_id", "card_id", "signal_type", "period", "annotator", "round",
            "signal_valid", "severity_label", "d7_score", "d8_violation", "note"]
    return pd.DataFrame(rows, columns=cols)


def test_empty_is_pending():
    out = compute_agreement(_df([]))
    assert out["status"] == "pending"
    assert out["metrics"] == {}


def test_single_annotator_no_rounds_is_pending():
    df = _df([
        ["c1", "k0", "receivables_revenue_divergence", "2023", "A", 1, "yes", "high", 4.5, "no", ""],
    ])
    out = compute_agreement(df)
    assert out["status"] == "pending"


def test_two_annotators_computes_metrics():
    df = _df([
        ["c1", "k0", "receivables_revenue_divergence", "2023", "A", 1, "yes", "high", 4.5, "no", ""],
        ["c1", "k0", "receivables_revenue_divergence", "2023", "B", 1, "yes", "high", 4.0, "no", ""],
        ["c2", "k0", "inventory_cost_divergence", "2023", "A", 1, "yes", "medium", 3.5, "no", ""],
        ["c2", "k0", "inventory_cost_divergence", "2023", "B", 1, "no", "medium", 2.0, "no", ""],
        ["c3", "k0", "gross_net_margin_divergence", "2023", "A", 1, "uncertain", "low", 3.0, "no", ""],
        ["c3", "k0", "gross_net_margin_divergence", "2023", "B", 1, "yes", "low", 3.5, "no", ""],
    ])
    out = compute_agreement(df)
    assert out["status"] == "ok"
    m = out["metrics"]
    # Cohen's κ 两两应存在（A/B 在 c2 上不一致，κ 应 < 1 且非 NaN）
    k = m["cohen_kappa_pairwise"]
    assert isinstance(k, dict) and "mean" in k
    assert k["mean"] is not None and -1.0 <= k["mean"] <= 1.0
    # Spearman 应算出（A/B 的 d7 相关性）
    assert isinstance(m["spearman_d7_pairwise"], list) and len(m["spearman_d7_pairwise"]) >= 1
    assert "spearman_rho" in m["spearman_d7_pairwise"][0]


def test_repeat_eval_fluctuation():
    df = _df([
        ["c1", "k0", "receivables_revenue_divergence", "2023", "A", 1, "yes", "high", 4.5, "no", ""],
        ["c1", "k0", "receivables_revenue_divergence", "2023", "A", 2, "yes", "high", 4.2, "no", ""],
        ["c1", "k0", "receivables_revenue_divergence", "2023", "B", 1, "yes", "high", 4.0, "no", ""],
    ])
    out = compute_agreement(df)
    assert out["status"] == "ok"
    flu = out["metrics"]["repeat_eval_fluctuation"]
    assert isinstance(flu, dict) and flu["n_repeated_cells"] == 1
    assert flu["max_std"] is not None and flu["max_std"] > 0


def test_json_safe_converts_undefined_metrics_to_null():
    payload = _json_safe({"undefined": math.nan, "nested": [math.inf, 1.0]})
    assert payload == {"undefined": None, "nested": [None, 1.0]}
    json.dumps(payload, allow_nan=False)
