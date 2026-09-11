#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3 有效性验证收尾 —— 一致性验证（consistency validation），不调用 Hy3。

复用 eval/real_sample_eval.py 的单条评分逻辑（evaluate_one），对
data/derived/real_eval_outputs_fixture.jsonl 的 32 条 fixture 重复评估 3 轮，
验证规则式评测器的工程确定性：同一输入多次运行分数应完全一致（max_delta = 0）。

输入：
  - data/derived/real_eval_samples.jsonl          （gold）
  - data/derived/real_eval_outputs_fixture.jsonl  （32 条待评测输出，关联 sample_id）
输出：
  - data/derived/consistency_validation.csv        （每行：sample_id, quality, run_id, fact, na, coverage, sourcing, structure, total）
  - 控制台汇总每个 sample_id+quality 的 total 分数 max-min 波动
"""
import sys
import json
import csv
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.real_sample_eval import evaluate_one, load_jsonl, SAMPLES_PATH, FIXTURE_PATH

OUT_CSV = ROOT / "data" / "derived" / "consistency_validation.csv"
N_ROUNDS = 3
FIELDS = ["sample_id", "quality", "run_id", "fact", "na", "coverage", "sourcing", "structure", "total"]


def load_gold():
    return {s["sample_id"]: s for s in (
        json.loads(l) for l in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if l.strip())}


def main():
    gold = load_gold()
    fixtures = load_jsonl(FIXTURE_PATH)

    rows = []
    for sid, outs in fixtures.items():
        s = gold.get(sid)
        if s is None:
            print(f"[WARN] fixture sample_id {sid} 在 gold 中缺失，跳过", file=sys.stderr)
            continue
        for o in outs:
            for run_id in range(1, N_ROUNDS + 1):
                res = evaluate_one(s, o["output"])
                rows.append({
                    "sample_id": sid,
                    "quality": o["quality"],
                    "run_id": run_id,
                    "fact": res["fact"],
                    "na": res["na"],
                    "coverage": res["coverage"],
                    "sourcing": res["sourcing"],
                    "structure": res["structure"],
                    "total": res["total"],
                })

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # 汇总每个 sample_id+quality 的 total 分数波动
    groups = defaultdict(list)
    for r in rows:
        groups[(r["sample_id"], r["quality"])].append(r["total"])
    deltas = {k: (max(v) - min(v)) for k, v in groups.items()}
    max_delta = max(deltas.values()) if deltas else 0.0

    total_fixtures = sum(len(v) for v in fixtures.values())
    print("=" * 70)
    print(f"一致性验证：评估样本数(条)={total_fixtures} 轮数={N_ROUNDS} 输出行数={len(rows)}")
    print(f"分组数 (sample_id+quality) = {len(groups)}")
    print(f"total 分数 max_delta (3 轮 max-min) = {max_delta}")
    print("=" * 70)
    print("结论：", "规则评估器确定性，3 轮分数完全一致 [PASS]" if max_delta == 0
          else f"存在波动 [FAIL] max_delta={max_delta}")
    print(f"输出已写入：{OUT_CSV}")
    return 0 if max_delta == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
