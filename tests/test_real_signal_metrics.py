"""真实样本信号级 D4/D5 指标计算测试。"""

from eval.validity.real_signal_metrics import compute_metrics


def _row(sample_id, signal_type, annotator, gold, detected, severity="medium"):
    return {
        "sample_id": sample_id,
        "signal_type": signal_type,
        "annotator": annotator,
        "human_gold_present": gold,
        "human_gold_severity": severity,
        "model_detected": detected,
    }


def test_real_signal_metrics_pending_when_unfilled():
    rows = [_row("s1", "cashflow_profit_divergence", "A", "", "")]

    result = compute_metrics(rows)

    assert result["status"] == "pending"
    assert result["metrics"] == {}


def test_real_signal_metrics_computes_micro_metrics():
    rows = [
        _row("s1", "a", "A", "yes", "yes", "high"),
        _row("s1", "a", "B", "yes", "yes", "high"),
        _row("s1", "b", "A", "yes", "no", "high"),
        _row("s1", "b", "B", "yes", "no", "high"),
        _row("s1", "c", "A", "no", "yes"),
        _row("s1", "c", "B", "no", "yes"),
    ]

    result = compute_metrics(rows)

    assert result["status"] == "ok"
    assert result["metrics"]["TP"] == 1
    assert result["metrics"]["FP"] == 1
    assert result["metrics"]["FN"] == 1
    assert result["metrics"]["MRhigh"] == {"value": 0.5, "num": 1, "den": 2}
    assert result["metrics"]["P"] == {"value": 0.5, "num": 1, "den": 2}
    assert result["metrics"]["R"] == {"value": 0.5, "num": 1, "den": 2}

