"""导出真实样本 D4/D5 信号级人工标注待办表。

该表用于让标注者同时判断：
- human_gold_present：给定结构化财务数据中该信号是否真实存在；
- model_detected：Hy3 输出是否识别出该信号。

两列都由人工填写，脚本不自动推断，避免把规则结果冒充真实人工金标准。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from app.schema import SIGNAL_TYPES


ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUTS = ROOT / "data" / "derived" / "hy3_real_outputs.jsonl"
DEFAULT_OUT = ROOT / "eval" / "validity" / "real_signal_todo.csv"
TARGET_SIGNALS = [s for s in SIGNAL_TYPES if s != "other"]
FIELDS = [
    "sample_id", "company", "sample_status", "signal_type", "annotator", "round",
    "human_gold_present", "human_gold_severity", "model_detected", "period", "note",
]


def load_outputs(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def build_rows(outputs_path: Path, annotators: list[str], round_id: int = 1) -> list[dict]:
    outputs = load_outputs(outputs_path)
    rows: list[dict] = []
    for out in outputs:
        for signal_type in TARGET_SIGNALS:
            for annotator in annotators:
                rows.append({
                    "sample_id": out["sample_id"],
                    "company": out.get("company", ""),
                    "sample_status": out.get("sample_status", ""),
                    "signal_type": signal_type,
                    "annotator": annotator,
                    "round": round_id,
                    "human_gold_present": "",
                    "human_gold_severity": "",
                    "model_detected": "",
                    "period": "",
                    "note": "",
                })
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出真实样本 D4/D5 信号级人工标注待办表")
    parser.add_argument("--outputs", default=str(DEFAULT_OUTPUTS), help="hy3_real_outputs.jsonl 路径")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 CSV 路径")
    parser.add_argument("--annotators", default="A,B", help="逗号分隔的标注者代号")
    parser.add_argument("--round", type=int, default=1, help="标注轮次")
    args = parser.parse_args()

    annotators = [a.strip() for a in args.annotators.split(",") if a.strip()]
    if len(annotators) < 2:
        raise SystemExit("至少需要 2 名标注者，例如 --annotators A,B")
    rows = build_rows(Path(args.outputs), annotators=annotators, round_id=args.round)
    write_csv(rows, Path(args.out))
    n_samples = len({row["sample_id"] for row in rows})
    print(f"wrote {len(rows)} rows to {args.out} ({n_samples} samples x {len(TARGET_SIGNALS)} signals x {len(annotators)} annotators)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

