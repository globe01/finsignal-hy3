"""真实样本信号级标注待办表导出测试。"""

import json

from app.schema import SIGNAL_TYPES
from eval.validity.export_real_signal_todo import TARGET_SIGNALS, build_rows, write_csv


def test_export_real_signal_todo_expands_all_target_signals(tmp_path):
    outputs_path = tmp_path / "hy3_real_outputs.jsonl"
    outputs = [
        {"sample_id": "s1", "company": "公司A", "sample_status": "READY"},
        {"sample_id": "s2", "company": "公司B", "sample_status": "PARTIAL"},
    ]
    outputs_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in outputs),
        encoding="utf-8",
    )

    rows = build_rows(outputs_path, annotators=["A", "B"])

    assert TARGET_SIGNALS == [s for s in SIGNAL_TYPES if s != "other"]
    assert len(rows) == 2 * len(TARGET_SIGNALS) * 2
    assert {row["sample_id"] for row in rows} == {"s1", "s2"}
    assert {row["annotator"] for row in rows} == {"A", "B"}
    assert all(row["human_gold_present"] == "" for row in rows)
    assert all(row["model_detected"] == "" for row in rows)


def test_export_real_signal_todo_writes_csv(tmp_path):
    out_path = tmp_path / "real_signal_todo.csv"
    rows = [{
        "sample_id": "s1", "company": "公司A", "sample_status": "READY",
        "signal_type": "cashflow_profit_divergence", "annotator": "A", "round": 1,
        "human_gold_present": "", "human_gold_severity": "", "model_detected": "",
        "period": "", "note": "",
    }]

    write_csv(rows, out_path)

    text = out_path.read_text(encoding="utf-8")
    assert "sample_id,company,sample_status,signal_type" in text
    assert "s1,公司A,READY,cashflow_profit_divergence" in text
