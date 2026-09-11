"""导出双人盲评标注待办表。

从一次在线评测的 ``cases*.json`` 中抽取匿名 case/card 清单，并为 A/B 两名标注者
各生成一份空白标注行。输出不包含 ground_truth、injection、metrics 或模型自报严重度，
可直接交给标注者按 ``docs/annotation_guide.md`` 填写。

用法：
  python -m eval.validity.export_annotation_todo \
      --cases results/online_local/cases_run1.json \
      --out eval/validity/annotations_todo.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from eval.validity.agreement import _template_rows


FIELDS = [
    "case_id", "card_id", "signal_type", "period", "annotator", "round",
    "signal_valid", "severity_label", "d7_score", "d8_violation", "note",
]


def build_rows(cases_path: Path, annotators: list[str], round_id: int = 1) -> list[dict]:
    with cases_path.open(encoding="utf-8") as f:
        cases = json.load(f)
    base_rows = _template_rows(cases)
    rows: list[dict] = []
    for base in base_rows:
        for annotator in annotators:
            row = {k: base.get(k, "") for k in FIELDS}
            row["annotator"] = annotator
            row["round"] = round_id
            rows.append(row)
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 A/B 双人盲评标注待办表")
    parser.add_argument("--cases", required=True, help="在线评测 cases JSON 路径")
    parser.add_argument("--out", default="eval/validity/annotations_todo.csv",
                        help="输出 CSV 路径")
    parser.add_argument("--annotators", default="A,B", help="逗号分隔的标注者代号")
    parser.add_argument("--round", type=int, default=1, help="标注轮次")
    args = parser.parse_args()

    annotators = [a.strip() for a in args.annotators.split(",") if a.strip()]
    if len(annotators) < 2:
        raise SystemExit("至少需要 2 名标注者，例如 --annotators A,B")

    rows = build_rows(Path(args.cases), annotators=annotators, round_id=args.round)
    write_csv(rows, Path(args.out))
    print(f"wrote {len(rows)} rows to {args.out} ({len(rows) // len(annotators)} cards x {len(annotators)} annotators)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

