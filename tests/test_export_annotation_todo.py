"""双人盲评待办表导出测试。"""

import json

from eval.validity.export_annotation_todo import build_rows, write_csv


def test_export_annotation_todo_expands_rows_without_severity_leak(tmp_path):
    cases = [
        {
            "cards_full": [
                {"signal_type": "cashflow_profit_divergence", "periods": ["2023", "2024"], "severity": "high"},
                {"signal_type": "goodwill_net_assets_pressure", "periods": ["2024"], "severity": "medium"},
            ],
            "ground_truth": {"gold": [{"signal_type": "x"}]},
        }
    ]
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")

    rows = build_rows(cases_path, annotators=["A", "B"])

    assert len(rows) == 4
    assert {row["annotator"] for row in rows} == {"A", "B"}
    assert {row["card_id"] for row in rows} == {"card_000", "card_001"}
    assert all(row["severity_label"] == "" for row in rows)
    assert all("ground_truth" not in row for row in rows)


def test_export_annotation_todo_writes_csv(tmp_path):
    out_path = tmp_path / "annotations_todo.csv"
    rows = [{
        "case_id": "case_000", "card_id": "card_000", "signal_type": "other",
        "period": "2024", "annotator": "A", "round": 1, "signal_valid": "",
        "severity_label": "", "d7_score": "", "d8_violation": "", "note": "",
    }]

    write_csv(rows, out_path)

    text = out_path.read_text(encoding="utf-8")
    assert "case_id,card_id,signal_type" in text
    assert "case_000,card_000,other" in text

