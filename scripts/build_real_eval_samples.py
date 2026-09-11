"""Build Phase 3 real financial evaluation samples.

Input:
  data/derived/real_financials_2021_2025.csv

Output:
  data/derived/real_eval_samples.jsonl

The generated samples use 3-year windows. Missing fields are kept as missing:
no zero filling, no backfilling from notes, and no sample dropping.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
INPUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"
OUTPUT_JSONL = ROOT / "data/derived/real_eval_samples.jsonl"

WINDOWS = [
    ("2021-2023", [2021, 2022, 2023]),
    ("2022-2024", [2022, 2023, 2024]),
    ("2023-2025", [2023, 2024, 2025]),
]

FINANCIAL_FIELDS = [
    "revenue",
    "cogs",
    "net_profit",
    "cfo",
    "accounts_receivable",
    "inventory",
    "goodwill",
    "current_assets",
    "current_liabilities",
    "short_borrow",
    "cash",
    "equity",
    "nonrecurring",
]

CORE_FIELDS = [
    "revenue",
    "cogs",
    "net_profit",
    "cfo",
    "accounts_receivable",
    "inventory",
    "current_assets",
    "current_liabilities",
    "cash",
    "equity",
    "nonrecurring",
]

CALCULATION_NAMES = [
    "revenue_cagr",
    "gross_margin",
    "net_margin",
    "cfo_to_net_profit",
    "current_ratio",
    "ar_to_revenue",
    "inventory_to_revenue",
    "short_borrow_to_cash",
    "goodwill_to_equity",
    "nonrecurring_to_net_profit",
]


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def as_number(value: Decimal | None, places: int = 6) -> float | None:
    if value is None:
        return None
    quant = Decimal(1).scaleb(-places)
    return float(value.quantize(quant, rounding=ROUND_HALF_UP))


def as_money(value: Decimal | None) -> float | None:
    return as_number(value, places=2)


def ratio(num: Decimal | None, den: Decimal | None) -> Decimal | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def load_financials() -> dict[str, dict[int, dict[str, Any]]]:
    if not INPUT_CSV.exists():
        raise SystemExit(f"Missing input CSV: {INPUT_CSV.relative_to(ROOT)}")

    out: dict[str, dict[int, dict[str, Any]]] = {}
    with open(INPUT_CSV, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            company = row["company"].strip()
            year = int(row["year"])
            normalized = dict(row)
            for field in FINANCIAL_FIELDS:
                normalized[field] = parse_decimal(row.get(field))
            out.setdefault(company, {})[year] = normalized
    return out


def missing_fields(rows: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for row in rows:
        year = row["year"]
        for field in FINANCIAL_FIELDS:
            if row.get(field) is None:
                missing.append(f"{year}.{field}")
    return missing


def metric_na(name: str, missing: list[str], formula: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": "N/A",
        "value": None,
        "by_year": {},
        "missing_fields": missing,
        "formula": formula,
        "unit": "ratio",
    }


def metric_by_year(name: str, rows: list[dict[str, Any]], numerator: str, denominator: str,
                   formula: str) -> dict[str, Any]:
    missing = [
        f"{row['year']}.{field}"
        for row in rows
        for field in (numerator, denominator)
        if row.get(field) is None
    ]
    if missing:
        return metric_na(name, missing, formula)

    values = {
        str(row["year"]): as_number(ratio(row[numerator], row[denominator]))
        for row in rows
    }
    latest_year = str(rows[-1]["year"])
    return {
        "name": name,
        "status": "OK",
        "value": values[latest_year],
        "by_year": values,
        "missing_fields": [],
        "formula": formula,
        "unit": "ratio",
    }


def build_expected_calculations(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    first = rows[0]
    last = rows[-1]
    year_span = last["year"] - first["year"]

    calculations: dict[str, dict[str, Any]] = {}

    cagr_missing = [
        f"{row['year']}.revenue"
        for row in (first, last)
        if row.get("revenue") is None
    ]
    if cagr_missing or first.get("revenue") in (None, 0) or year_span <= 0:
        calculations["revenue_cagr"] = metric_na(
            "revenue_cagr",
            cagr_missing,
            "(revenue_end / revenue_start) ** (1 / years) - 1",
        )
    else:
        value = Decimal(float(last["revenue"] / first["revenue"]) ** (1 / year_span) - 1)
        calculations["revenue_cagr"] = {
            "name": "revenue_cagr",
            "status": "OK",
            "value": as_number(value),
            "by_year": {},
            "missing_fields": [],
            "formula": "(revenue_end / revenue_start) ** (1 / years) - 1",
            "unit": "ratio",
        }

    calculations["gross_margin"] = metric_by_year(
        "gross_margin", rows, "revenue", "cogs", "(revenue - cogs) / revenue"
    )
    if calculations["gross_margin"]["status"] == "OK":
        values = {
            str(row["year"]): as_number(ratio(row["revenue"] - row["cogs"], row["revenue"]))
            for row in rows
        }
        calculations["gross_margin"]["by_year"] = values
        calculations["gross_margin"]["value"] = values[str(rows[-1]["year"])]

    calculations["net_margin"] = metric_by_year(
        "net_margin", rows, "net_profit", "revenue", "net_profit / revenue"
    )
    calculations["cfo_to_net_profit"] = metric_by_year(
        "cfo_to_net_profit", rows, "cfo", "net_profit", "cfo / net_profit"
    )
    calculations["current_ratio"] = metric_by_year(
        "current_ratio", rows, "current_assets", "current_liabilities",
        "current_assets / current_liabilities",
    )
    calculations["ar_to_revenue"] = metric_by_year(
        "ar_to_revenue", rows, "accounts_receivable", "revenue",
        "accounts_receivable / revenue",
    )
    calculations["inventory_to_revenue"] = metric_by_year(
        "inventory_to_revenue", rows, "inventory", "revenue", "inventory / revenue"
    )
    calculations["short_borrow_to_cash"] = metric_by_year(
        "short_borrow_to_cash", rows, "short_borrow", "cash", "short_borrow / cash"
    )
    calculations["goodwill_to_equity"] = metric_by_year(
        "goodwill_to_equity", rows, "goodwill", "equity", "goodwill / equity"
    )
    calculations["nonrecurring_to_net_profit"] = metric_by_year(
        "nonrecurring_to_net_profit", rows, "nonrecurring", "net_profit",
        "nonrecurring / net_profit",
    )

    return calculations


def build_gold_facts(rows: list[dict[str, Any]],
                     calculations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    last = rows[-1]
    revenue_start = first["revenue"]
    revenue_end = last["revenue"]
    if revenue_start is None or revenue_end is None:
        direction = "unknown"
    elif revenue_end > revenue_start:
        direction = "up"
    elif revenue_end < revenue_start:
        direction = "down"
    else:
        direction = "flat"

    latest_year = str(last["year"])
    return {
        "revenue_trend": {
            "start_year": first["year"],
            "end_year": last["year"],
            "start_revenue": as_money(revenue_start),
            "end_revenue": as_money(revenue_end),
            "direction": direction,
            "cagr": calculations["revenue_cagr"]["value"],
        },
        "latest_profitability": {
            "year": last["year"],
            "gross_margin": calculations["gross_margin"].get("by_year", {}).get(latest_year),
            "net_margin": calculations["net_margin"].get("by_year", {}).get(latest_year),
        },
        "latest_cashflow_quality": {
            "year": last["year"],
            "cfo_to_net_profit": calculations["cfo_to_net_profit"].get("by_year", {}).get(latest_year),
        },
        "latest_balance_sheet_pressure": {
            "year": last["year"],
            "current_ratio": calculations["current_ratio"].get("by_year", {}).get(latest_year),
            "short_borrow_to_cash": calculations["short_borrow_to_cash"].get("by_year", {}).get(latest_year),
            "goodwill_to_equity": calculations["goodwill_to_equity"].get("by_year", {}).get(latest_year),
        },
    }


def sample_status(missing: list[str], calculations: dict[str, dict[str, Any]]) -> str:
    core_missing = [
        item for item in missing
        if item.split(".", 1)[1] in CORE_FIELDS
    ]
    if core_missing:
        return "BLOCKED"
    if any(metric["status"] == "N/A" for metric in calculations.values()):
        return "PARTIAL"
    return "READY"


def build_prompt(company: str, years: list[int], status: str) -> str:
    start, end = years[0], years[-1]
    return (
        f"你是一名面向财务学习者的投研分析助手。请基于给定的 {company} "
        f"{start}-{end} 年结构化年报数据，生成一段审慎的财务质量分析。"
        "请至少讨论收入趋势、盈利能力、现金流质量、营运资金压力、短期偿债压力、"
        "商誉压力和非经常性损益影响。凡输入字段缺失或标记为 N/A 的指标，不得补 0、"
        "不得臆测原因，应明确说明该指标无法从合并报表主表口径计算。"
        f"样本当前状态为 {status}。"
    )


def build_evaluation_notes(missing: list[str], calculations: dict[str, dict[str, Any]]) -> list[str]:
    notes = [
        "评测仅基于已结构化的公开年报派生字段，不使用原始 PDF 之外的补充事实。",
        "所有金额字段在 CSV 中已统一为元。",
        "字段缺失时对应指标标 N/A，不补 0，不从附注或上一年回填。",
        "暂不调用 Hy3，暂不输出 D4/D5 真实评测主结论。",
    ]
    if missing:
        notes.append(f"窗口存在真实空值字段: {', '.join(missing)}。")
    na_metrics = [name for name, item in calculations.items() if item["status"] == "N/A"]
    if na_metrics:
        notes.append(f"N/A 指标: {', '.join(na_metrics)}。")
    return notes


def serialize_financials(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        year = str(row["year"])
        out[year] = {
            field: as_money(row[field]) if row[field] is not None else None
            for field in FINANCIAL_FIELDS
        }
        out[year]["source_report"] = row["source_report"]
        out[year]["source_table_or_page"] = row["source_table_or_page"]
        out[year]["unit"] = "元"
    return out


def build_samples() -> list[dict[str, Any]]:
    financials = load_financials()
    samples: list[dict[str, Any]] = []

    for company in sorted(financials):
        by_year = financials[company]
        for window_name, years in WINDOWS:
            if not all(year in by_year for year in years):
                continue
            rows = [by_year[year] | {"year": year} for year in years]
            miss = missing_fields(rows)
            calculations = build_expected_calculations(rows)
            status = sample_status(miss, calculations)
            na_metrics = [
                name for name, value in calculations.items()
                if value["status"] == "N/A"
            ]
            sample = {
                "sample_id": f"{by_year[years[0]]['stock_code']}_{window_name}",
                "company": company,
                "stock_code": by_year[years[0]]["stock_code"],
                "window_years": years,
                "sample_status": status,
                "input_financials": serialize_financials(rows),
                "missing_fields": miss,
                "user_prompt": build_prompt(company, years, status),
                "gold_facts": build_gold_facts(rows, calculations),
                "expected_calculations": calculations,
                "evaluation_notes": build_evaluation_notes(miss, calculations),
                "coverage": {
                    "total_metrics": len(CALCULATION_NAMES),
                    "calculable_metrics": len(CALCULATION_NAMES) - len(na_metrics),
                    "na_metrics": len(na_metrics),
                    "na_metric_names": na_metrics,
                },
            }
            samples.append(sample)
    return samples


def main() -> None:
    samples = build_samples()
    OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")

    status_counts = Counter(sample["sample_status"] for sample in samples)
    company_counts = Counter(sample["company"] for sample in samples)
    na_counts = Counter(
        metric
        for sample in samples
        for metric in sample["coverage"]["na_metric_names"]
    )

    print(f"wrote {OUTPUT_JSONL.relative_to(ROOT)}")
    print(f"samples: {len(samples)}")
    print("companies:", dict(sorted(company_counts.items())))
    print("status:", dict(sorted(status_counts.items())))
    print("na_metrics:", dict(sorted(na_counts.items())))


if __name__ == "__main__":
    main()
