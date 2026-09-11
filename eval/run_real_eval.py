"""Phase 3 real-sample evaluation planner.

This module is intentionally offline:
- it does not call Hy3;
- it does not output D4/D5 main conclusions;
- it only reports sample coverage and metric availability.

Run `scripts/build_real_eval_samples.py` first to generate
`data/derived/real_eval_samples.jsonl`.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SAMPLE_PATH = ROOT / "data/derived/real_eval_samples.jsonl"
OUT_DIR = ROOT / "results/real_eval"
PLAN_PATH = OUT_DIR / "plan.json"


@dataclass
class RealSamplePlan:
    sample_id: str
    company: str
    stock_code: str
    window_years: list[int]
    status: str
    calculable_metrics: int
    na_metrics: int
    na_metric_names: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    note: str = (
        "Offline coverage plan only. No Hy3 call and no D4/D5 real-eval "
        "main conclusion before human gold standards are available."
    )


def load_samples() -> list[dict[str, Any]]:
    if not SAMPLE_PATH.exists():
        raise SystemExit(
            f"未找到 {SAMPLE_PATH.relative_to(ROOT)}。"
            "请先运行: .venv/bin/python scripts/build_real_eval_samples.py"
        )

    samples: list[dict[str, Any]] = []
    with open(SAMPLE_PATH, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{SAMPLE_PATH}:{lineno} JSON 解析失败: {exc}") from exc
    return samples


def to_plan(sample: dict[str, Any]) -> RealSamplePlan:
    coverage = sample.get("coverage", {})
    return RealSamplePlan(
        sample_id=sample["sample_id"],
        company=sample["company"],
        stock_code=sample["stock_code"],
        window_years=list(sample["window_years"]),
        status=sample.get("sample_status", "BLOCKED"),
        calculable_metrics=int(coverage.get("calculable_metrics", 0)),
        na_metrics=int(coverage.get("na_metrics", 0)),
        na_metric_names=list(coverage.get("na_metric_names", [])),
        missing_fields=list(sample.get("missing_fields", [])),
    )


def list_plans(only_company: str | None = None) -> list[RealSamplePlan]:
    samples = load_samples()
    plans = [to_plan(sample) for sample in samples]
    if only_company:
        plans = [plan for plan in plans if plan.company == only_company]
    return plans


def summarize(plans: list[RealSamplePlan]) -> dict[str, Any]:
    status_counts = Counter(plan.status for plan in plans)
    company_counts = Counter(plan.company for plan in plans)
    na_counts = Counter(
        metric
        for plan in plans
        for metric in plan.na_metric_names
    )
    return {
        "sample_count": len(plans),
        "company_count": len(company_counts),
        "windows_by_company": dict(sorted(company_counts.items())),
        "status_counts": dict(sorted(status_counts.items())),
        "na_metric_counts": dict(sorted(na_counts.items())),
    }


def write_plan(plans: list[RealSamplePlan]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "phase": "Phase 3 真实公开样本评测（离线覆盖计划）",
        "sample_source": str(SAMPLE_PATH.relative_to(ROOT)),
        "summary": summarize(plans),
        "limitations": [
            "不调用 Hy3。",
            "未建立人工金标准前不输出 D4/D5 真实评测主结论。",
            "字段缺失导致的指标标 N/A，不补 0、不从附注或上年数回填。",
        ],
        "windows": [asdict(plan) for plan in plans],
    }
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def render_list(plans: list[RealSamplePlan]) -> None:
    summary = summarize(plans)
    print(
        f"识别到 {summary['sample_count']} 个真实评测样本"
        f"（来自 {summary['company_count']} 家公司）。\n"
    )

    for plan in plans:
        years = f"{plan.window_years[0]}-{plan.window_years[-1]}"
        if plan.na_metrics:
            na = f"N/A {plan.na_metrics} 指标: {', '.join(plan.na_metric_names)}"
        else:
            na = "无 N/A 指标"

        if plan.missing_fields:
            missing_preview = ", ".join(plan.missing_fields[:5])
            if len(plan.missing_fields) > 5:
                missing_preview += f", ...（共 {len(plan.missing_fields)} 字段）"
            missing = f"缺失字段: {missing_preview}"
        else:
            missing = "字段齐全"

        print(
            f"  {plan.company} {years}  [{plan.status}]  "
            f"可评测 {plan.calculable_metrics}/10；{na}；{missing}"
        )

    print("\n汇总：")
    print(f"  status: {summary['status_counts']}")
    print(f"  windows_by_company: {summary['windows_by_company']}")
    print(f"  na_metric_counts: {summary['na_metric_counts']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 真实样本离线评测计划")
    parser.add_argument("--list", action="store_true", help="列出样本状态与 N/A 指标")
    parser.add_argument("--plan", action="store_true", help="写 results/real_eval/plan.json")
    parser.add_argument("--company", type=str, default=None, help="仅查看指定公司")
    args = parser.parse_args()

    plans = list_plans(only_company=args.company)
    if not plans:
        print("无匹配样本。")
        return

    render_list(plans)

    if args.plan:
        write_plan(plans)
        print(f"\n已写 {PLAN_PATH.relative_to(ROOT)}")
    elif args.list:
        print("\n（仅列表模式，未写 plan.json）")


if __name__ == "__main__":
    main()
