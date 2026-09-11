"""真实公开样本评测骨架（Phase 3 规划版）。

⚠ 本脚本当前仅完成**窗口规划与状态上报**，**不调用 Hy3**，**不输出 D4/D5
主结论**。仅在具备人工金标准后，下列限制方可解除：

- 不报召回率 / 精确率 / 漏报率（MRhigh）：缺金标准时这些数字无意义。
- 不调 Hy3 扫描：缺 API Key 时调用会失败，且无金标准时也无法判定输出。
- 不报 Hy3-as-Judge 结果：同上。

功能（已实现）：
- 读取 `data/derived/real_financials_2021_2025.csv`；
- 按公司生成滑动 3 年窗口（2021-2023, 2022-2024, 2023-2025），每家公司
  至多 3 个窗口；
- 对每个窗口生成 plan JSON（窗口字段清单、缺失字段告警、单位口径、
  期望的扫描步骤）写入 `results/real_eval/plan.json`；
- 打印 `pending` 状态与所需的前置条件（人工金标准 / Hy3 Key）。

用法：
  python -m eval.run_real_eval --list              # 列出当前可识别的窗口与缺失字段
  python -m eval.run_real_eval --plan              # 写 plan.json 并打印 pending 状态
  python -m eval.run_real_eval --company 宁德时代   # 仅规划指定公司

后续启用条件（不在本脚本内）：
1. 人工金标准：以「连续 3 年窗口 + 异常卡片预期」为单位组织标注；
2. Hy3 接入：填写 `.env`（HY3_BASE_URL / HY3_API_KEY / HY3_MODEL）；
3. 在 `eval/run_eval.py` 已有的 D1/D2/D3/D7/D8 规则侧基础上扩展真实样本
   评测流程，并在 plan 中标注「gold_standard_available=true」后启用主结论。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data/derived/real_financials_2021_2025.csv"
OUT_DIR = ROOT / "results/real_eval"
PLAN_PATH = OUT_DIR / "plan.json"

# 必备字段；任一窗口若这些字段有空值则该窗口降级为「不可用」并记入 plan。
REQUIRED_FIELDS = [
    "revenue", "cogs", "net_profit", "cfo",
    "accounts_receivable", "inventory", "goodwill",
    "current_assets", "current_liabilities", "short_borrow",
    "cash", "equity", "nonrecurring",
]

# 滑动窗口定义：(窗口名, 起年, 终年)
WINDOWS = [
    ("2021-2023", 2021, 2023),
    ("2022-2024", 2022, 2024),
    ("2023-2025", 2023, 2025),
]


@dataclass
class WindowPlan:
    company: str
    stock_code: str
    window: str
    years: list[int]
    fields: dict  # 字段 -> {"value": str, "source": str, "unit": "元"}
    missing_fields: list[str] = field(default_factory=list)
    status: str = "pending"   # pending / partial / ready
    prerequisites: list[str] = field(default_factory=list)
    notes: str = ""


def load_csv():
    """读取结构化财务 CSV，返回 {company: {year: row}}。"""
    if not CSV_PATH.exists():
        raise SystemExit(f"未找到 {CSV_PATH}，请先运行 scripts/extract_catl_financials.py")
    rows_by_company: dict[str, dict[int, dict]] = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            co = row["company"].strip()
            if not co:
                continue
            try:
                year = int(row["year"])
            except ValueError:
                continue
            rows_by_company.setdefault(co, {})[year] = row
    return rows_by_company


def build_window_plan(company: str, by_year: dict[int, dict], window_name: str,
                      start: int, end: int) -> WindowPlan:
    years = list(range(start, end + 1))
    fields: dict = {}
    missing: list[str] = []
    src_pages: list[str] = []
    for k in REQUIRED_FIELDS:
        # 优先用窗口中年份最末一年（最贴近当前时点）
        chosen = None
        chosen_year = None
        for y in reversed(years):
            if y in by_year and by_year[y].get(k):
                chosen = by_year[y][k]
                chosen_year = y
                break
        if chosen is None:
            missing.append(k)
        else:
            fields[k] = {
                "value": chosen,
                "year": chosen_year,
                "unit": "元",
            }
    if by_year.get(years[-1]):
        src = by_year[years[-1]].get("source_table_or_page", "")
        if src and src not in src_pages:
            src_pages.append(src)

    if not missing:
        status = "ready"
    elif len(missing) < len(REQUIRED_FIELDS) // 2:
        status = "partial"
    else:
        status = "pending"

    prereqs = []
    if status != "ready":
        prereqs.append(f"补齐缺失字段: {', '.join(missing) or '(无)'}")
    prereqs.append("建立该窗口的人工金标准（异常卡片预期清单）")
    prereqs.append("确认 .env 已配置 HY3_BASE_URL / HY3_API_KEY / HY3_MODEL")

    head = by_year.get(years[-1]) or (by_year.get(years[0]) or {})
    return WindowPlan(
        company=company,
        stock_code=head.get("stock_code", ""),
        window=window_name,
        years=years,
        fields=fields,
        missing_fields=missing,
        status=status,
        prerequisites=prereqs,
        notes=("缺人工金标准前，不输出 D4 召回率 / D5 精确率 / MRhigh；"
               "本骨架只完成窗口规划与字段完整性检查。"),
    )


def list_windows(only_company: str | None = None) -> list[WindowPlan]:
    data = load_csv()
    plans: list[WindowPlan] = []
    for company, by_year in data.items():
        if only_company and company != only_company:
            continue
        # 至少要有 3 个连续年份才生成窗口
        years_available = sorted(by_year.keys())
        for wname, s, e in WINDOWS:
            if not all(y in years_available for y in range(s, e + 1)):
                continue
            plans.append(build_window_plan(company, by_year, wname, s, e))
    return plans


def write_plan(plans: list[WindowPlan]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "pending",
        "phase": "Phase 3 真实公开样本评测（骨架）",
        "csv_source": str(CSV_PATH.relative_to(ROOT)),
        "note": "缺人工金标准前不输出 D4/D5 主结论；本文件仅描述窗口与缺失字段。",
        "windows": [asdict(p) for p in plans],
    }
    with open(PLAN_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="真实公开样本评测骨架（Phase 3 规划版）")
    ap.add_argument("--list", action="store_true", help="列出窗口与字段缺失情况")
    ap.add_argument("--plan", action="store_true", help="写 plan.json 并打印 pending 状态")
    ap.add_argument("--company", type=str, default=None, help="仅规划指定公司")
    args = ap.parse_args()

    plans = list_windows(only_company=args.company)
    if not plans:
        print(f"无窗口可规划。请先在 {CSV_PATH.relative_to(ROOT)} 录入至少一家公司 3 年数据。")
        return

    print(f"识别到 {len(plans)} 个窗口（来自 {len({p.company for p in plans})} 家公司）。\n")
    for p in plans:
        miss = f"缺 {len(p.missing_fields)} 字段" if p.missing_fields else "字段齐全"
        print(f"  {p.company} {p.window}  [{p.status.upper()}]  {miss}")

    if args.list and not args.plan:
        print("\n（仅列表模式，未写 plan.json）")
        return

    write_plan(plans)
    print(f"\n已写 {PLAN_PATH.relative_to(ROOT)}")
    print("\n当前为 pending 状态。解除限制需满足：")
    print("  1) 全部窗口字段齐全或标注缺失字段的合理处理方式；")
    print("  2) 建立对应窗口的人工金标准；")
    print("  3) 配置 .env 后接入 Hy3 扫描。")
    print("  4) 在本脚本中按 plan 调用 app.scan 与 eval.run_eval 现有规则路径，"
          "并将 D4/D5 限人工金标准可用时才允许输出。")


if __name__ == "__main__":
    main()
