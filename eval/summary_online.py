"""在线评测脱敏汇总（提交前收口，任务二）。

读取本地 ``results/online_local/`` 下的真实在线评测产物（gitignored，不入库），
生成两份**可提交、可复核**的脱敏汇总，供 GitHub 用户查看 Hy3 真实性能：

- ``results/online_summary.json``：各指标 均值/最小/最大 + 逐轮 num/den + 运行配置。
- ``results/online_runs_summary.csv``：metric, run, value, num, den 宽表。

**脱敏原则**：仅包含指标数值与运行配置（runs / period_mode / temperature / n_cases），
不写入任何 API Key、endpoint、模型原始输出或公司身份；原始产物仍在 ``results/online_local/``
（本地保留、不入库）。详见 ``docs/report.md`` §5。

用法：
  python -m eval.summary_online --in-dir results/online_local \\
      --out-json results/online_summary.json --out-csv results/online_runs_summary.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from typing import Dict, List, Optional

# 稳定性文件中的指标键 -> 各 run 聚合报告里的取值路径（用于抽取 num/den）。
_STABILITY_TO_REPORT = {
    "MRhigh": ["MRhigh"],
    "P": ["P"],
    "R": ["R"],
    "Rw": ["Rw"],
    "D1": ["D1_value_accuracy"],
    "D2": ["D2_formula_correctness"],
    "D3": ["D3_strict_traceability"],
    "D6": ["D6_severity_agreement"],
    "D7": ["D7_rule_rubric_mean"],
    "D8": ["D8_compliance"],
    "D7_hy3": ["D7_hy3_judge_mean"],
    "D8_hy3": ["D8_compliance_hy3_judge"],
}


def _node(report: Dict, path: List[str]) -> Optional[Dict]:
    node = report
    for p in path:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node if isinstance(node, dict) else None


def summarize(in_dir: str, out_json: str, out_csv: str) -> Dict:
    with open(os.path.join(in_dir, "stability.json"), encoding="utf-8") as f:
        stab = json.load(f)
    period_mode = stab.get("period_mode")
    runs = int(stab.get("runs", 0))
    stability = stab.get("stability", {})

    report1_path = os.path.join(in_dir, "report_run1.json")
    n_cases = None
    if os.path.exists(report1_path):
        with open(report1_path, encoding="utf-8") as f:
            n_cases = json.load(f).get("n_cases")

    # 逐轮 num/den（按稳定性指标键对齐到各 run 聚合报告路径）
    per_run: Dict[str, List[Dict]] = {}
    for metric, path in _STABILITY_TO_REPORT.items():
        per_run[metric] = []
        for r in range(1, runs + 1):
            rp = os.path.join(in_dir, f"report_run{r}.json")
            entry = {"run": r, "value": None, "num": None, "den": None}
            if os.path.exists(rp):
                with open(rp, encoding="utf-8") as f:
                    node = _node(json.load(f), path)
                if node is not None:
                    entry["value"] = node.get("value")
                    entry["num"] = node.get("num")
                    entry["den"] = node.get("den")
            per_run[metric].append(entry)

    # 稳定性汇总（均值/最小/最大）
    summary: Dict[str, object] = {}
    for metric, s in stability.items():
        if isinstance(s, dict):
            summary[metric] = {
                "mean": s.get("mean"), "min": s.get("min"),
                "max": s.get("max"), "runs": s.get("runs"),
            }
        else:
            summary[metric] = s  # D7_hy3 / D8_hy3 为 null 时原样保留

    out_obj = {
        "generated_from": "results/online_local（gitignored，本地留档；本文件为其脱敏汇总）",
        "source": "Hy3 真实在线评测（腾讯云 TokenHub，OpenAI 兼容 Chat Completions）",
        "disclaimer": ("仅为研究方法学讨论，不构成投资/审计/法律意见；数据均为合成（synthetic）。"
                       "原始模型输出不入库；详见 docs/report.md §5。"),
        "period_mode": period_mode,
        "temperature": 0,
        "n_cases": n_cases,
        "summary": summary,
        "per_run": per_run,
    }
    os.makedirs(os.path.dirname(out_json) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out_obj, f, ensure_ascii=False, indent=2)

    # CSV 宽表
    os.makedirs(os.path.dirname(out_csv) or ".", exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "run", "value", "num", "den"])
        for metric, lst in per_run.items():
            for e in lst:
                w.writerow([metric, e["run"], e["value"], e["num"], e["den"]])

    return out_obj


def main() -> None:
    ap = argparse.ArgumentParser(description="生成在线评测脱敏汇总（可提交）")
    ap.add_argument("--in-dir", default="results/online_local",
                    help="本地在线评测产物目录（含 report_runN.json 与 stability.json）")
    ap.add_argument("--out-json", default="results/online_summary.json")
    ap.add_argument("--out-csv", default="results/online_runs_summary.csv")
    args = ap.parse_args()
    out = summarize(args.in_dir, args.out_json, args.out_csv)
    print(f"已生成：{args.out_json}（{len(out['summary'])} 指标）、{args.out_csv}")


if __name__ == "__main__":
    main()
