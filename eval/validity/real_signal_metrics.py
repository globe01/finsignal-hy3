"""真实样本 D4/D5 信号级人工标注指标计算。

输入为 ``export_real_signal_todo.py`` 生成并由标注者填写后的 CSV。
当标注不足时输出 status=pending；只有存在有效人工标注时才计算 MRhigh、P、R、Rw。
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


YES = {"yes", "y", "1", "true", "是", "有", "present", "detected"}
NO = {"no", "n", "0", "false", "否", "无", "absent", "missing"}
WEIGHTS = {"low": 1, "medium": 2, "high": 3}


def _norm_bool(v: str | None) -> bool | None:
    s = str(v or "").strip().lower()
    if s in YES:
        return True
    if s in NO:
        return False
    return None


def _norm_severity(v: str | None) -> str:
    s = str(v or "").strip().lower()
    return s if s in WEIGHTS else "medium"


def _ratio(num: int, den: int) -> dict[str, Any]:
    return {"value": (num / den if den else None), "num": num, "den": den}


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _majority(values: list[bool]) -> bool | None:
    if not values:
        return None
    counts = Counter(values)
    if counts[True] == counts[False]:
        return None
    return counts[True] > counts[False]


def compute_metrics(rows: list[dict[str, str]]) -> dict[str, Any]:
    needed = {"sample_id", "signal_type", "annotator", "human_gold_present", "model_detected"}
    missing = needed - set(rows[0].keys() if rows else [])
    if missing:
        return {"status": "pending", "reason": f"缺少列: {sorted(missing)}", "metrics": {}}

    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["sample_id"], row["signal_type"])].append(row)

    items = []
    unresolved = []
    for key, group in grouped.items():
        gold_votes = [_norm_bool(r.get("human_gold_present")) for r in group]
        pred_votes = [_norm_bool(r.get("model_detected")) for r in group]
        gold = _majority([v for v in gold_votes if v is not None])
        pred = _majority([v for v in pred_votes if v is not None])
        if gold is None or pred is None:
            unresolved.append({"sample_id": key[0], "signal_type": key[1]})
            continue
        severities = [_norm_severity(r.get("human_gold_severity")) for r in group if _norm_bool(r.get("human_gold_present"))]
        severity = Counter(severities).most_common(1)[0][0] if severities else "medium"
        items.append({"sample_id": key[0], "signal_type": key[1], "gold": gold, "pred": pred, "severity": severity})

    if not items:
        return {
            "status": "pending",
            "reason": "没有可计算的人工标注行：请填写 human_gold_present 与 model_detected",
            "n_rows": len(rows),
            "unresolved": unresolved[:20],
            "metrics": {},
        }

    tp = sum(1 for i in items if i["gold"] and i["pred"])
    fp = sum(1 for i in items if not i["gold"] and i["pred"])
    fn = sum(1 for i in items if i["gold"] and not i["pred"])
    high_den = sum(1 for i in items if i["gold"] and i["severity"] == "high")
    high_miss = sum(1 for i in items if i["gold"] and i["severity"] == "high" and not i["pred"])
    rw_den = sum(WEIGHTS[i["severity"]] for i in items if i["gold"])
    rw_hit = sum(WEIGHTS[i["severity"]] for i in items if i["gold"] and i["pred"])

    return {
        "status": "ok" if not unresolved else "partial",
        "n_rows": len(rows),
        "n_items_scored": len(items),
        "n_items_unresolved": len(unresolved),
        "unresolved_preview": unresolved[:20],
        "metrics": {
            "MRhigh": _ratio(high_miss, high_den),
            "P": _ratio(tp, tp + fp),
            "R": _ratio(tp, tp + fn),
            "Rw": _ratio(rw_hit, rw_den),
            "over_inference_rate": _ratio(fp, tp + fp),
            "TP": tp,
            "FP": fp,
            "FN": fn,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="计算真实样本人工信号级 D4/D5 指标")
    parser.add_argument("--csv", default="eval/validity/real_signal_filled.csv", help="已填写标注 CSV")
    args = parser.parse_args()
    try:
        rows = load_rows(Path(args.csv))
    except FileNotFoundError:
        result = {"status": "pending", "reason": f"标注文件不存在: {args.csv}", "metrics": {}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(compute_metrics(rows), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

