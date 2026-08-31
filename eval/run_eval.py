"""评测编排（对应方案 §6、§7、§13 eval/run_eval.py）。

流程：构造样本集（清洁/注入/阴性/阈下）→ 调 Hy3 盲扫（并发<=5）→ 规则 Oracle 出金标准 →
D1~D8 评估 → 输出严重异常漏报率等核心指标。

用法：
  python -m eval.run_eval --offline      # 离线自检（用金标准当“完美模型”，校验指标数学）
  python -m eval.run_eval --limit 3       # 仅跑 3 个样本的真实 Hy3 扫描（冒烟）
  python -m eval.run_eval                 # 全量（默认 14 个样本）
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

from app.schema import AnomalyCard, ScanOutput
from app.scan import scan_text
from eval.fact_eval import aggregate_facts
from eval.formula_eval import evaluate_d2
from eval.judge import judge_cards
from eval.rules import compute_all_signals, severity_weight
from eval.set_eval import compute_metrics, summarize
from generator.base import company_to_text, make_clean_company
from generator.inject import inject
from generator.negative import make_boundary_control, make_negative_control

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "results", "raw")
TABLES = os.path.join(ROOT, "results", "tables")
_SEM = threading.Semaphore(5)  # 混元默认并发上限（方案 §5.1）


def build_dataset() -> List[Dict]:
    cases: List[Dict] = []
    for i in range(2):
        c = make_negative_control(seed=i, name=f"阴性对照{i+1}")
        cases.append({"company": c, "meta": {"kind": "negative", "inject": None}})
    for st in ["receivables_revenue_divergence", "inventory_cost_divergence",
               "goodwill_net_assets_pressure", "gross_net_margin_divergence",
               "cashflow_profit_divergence"]:
        for s in (1.0, 2.0):
            base = make_clean_company(name=f"注入_{st}_{s}")
            c, meta = inject(base, st, strength=s)
            cases.append({"company": c, "meta": {"kind": "injected", "inject": st, "strength": s}})
    for st in ("receivables_revenue_divergence", "goodwill_net_assets_pressure"):
        c = make_boundary_control(st)
        cases.append({"company": c, "meta": {"kind": "boundary", "inject": st}})
    return cases


def _scan_one(case: Dict) -> ScanOutput:
    c = case["company"]
    with _SEM:
        # 评测默认 temperature=0 以保证可复现（方案 §6.4：多次运行取均值）
        return scan_text(company_to_text(c), company=c.name,
                         years="-".join(str(y) for y in c.years),
                         temperature=0)


def _perfect_cards(gold: List[dict]) -> List[AnomalyCard]:
    """离线自检：把金标准当“完美模型输出”构造卡片，校验指标数学。"""
    cards = []
    for g in gold:
        cards.append(AnomalyCard(
            signal_type=g["signal_type"], severity=g["severity"],
            periods=g["periods"],
            fact_basis=[], possible_explanations=["（离线自检占位）"],
            conclusion_boundary="仅用于离线校验指标，不能据此认定造假。",
        ))
    return cards


def evaluate_case(case: Dict, cards: List[AnomalyCard]) -> Dict:
    company = case["company"]
    gold = compute_all_signals(company)
    model_enum = [
        {"signal_type": c.signal_type.value, "periods": c.periods, "severity": c.severity.value}
        for c in cards if c.signal_type.value != "other"
    ]
    metrics = compute_metrics(gold, model_enum, severity_weight)
    facts = aggregate_facts(cards, company)
    judge = judge_cards(cards)
    d2 = evaluate_d2(cards, company)
    # D6 严重度排序（代理指标）：模型检出的信号中，其严重度与金标准一致的比例
    gold_sev = {g["signal_type"]: g["severity"] for g in gold}
    sev_hits = sev_total = 0
    for m in model_enum:
        if m["signal_type"] in gold_sev:
            sev_total += 1
            if m["severity"] == gold_sev[m["signal_type"]]:
                sev_hits += 1
    d6_rate = (sev_hits / sev_total) if sev_total else None
    return {
        "meta": case["meta"], "gold": gold, "model": model_enum,
        "metrics": metrics, "facts": facts, "judge": judge, "d2": d2,
        "d6_severity_rate": d6_rate, "n_cards": len(cards),
        "cards_full": [c.model_dump() for c in cards],  # 留存原文供人工复核(方案 §13)
    }


def run(offline: bool, limit: int | None) -> Dict:
    cases = build_dataset()
    if limit:
        cases = cases[:limit]
    results: List[Dict] = []
    if offline:
        for case in cases:
            gold = compute_all_signals(case["company"])
            cards = _perfect_cards(gold)
            results.append(evaluate_case(case, cards))
    else:
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(_scan_one, c): c for c in cases}
            for f in as_completed(futs):
                case = futs[f]
                try:
                    out = f.result()
                    results.append(evaluate_case(case, out.cards))
                except Exception as e:  # noqa: BLE001
                    results.append({"meta": case["meta"], "error": str(e)})
    agg = summarize([r["metrics"] for r in results if "metrics" in r])
    # D7/D8 维度均值（方案 §6.1 八维评估），跳过无卡片样本
    d7 = [r["judge"]["d7_mean"] for r in results
          if "judge" in r and r["judge"].get("d7_mean") is not None]
    d8 = [r["judge"]["d8_compliance_rate"] for r in results
          if "judge" in r and r["judge"].get("d8_compliance_rate") is not None]
    agg["mean_D7"] = (sum(d7) / len(d7)) if d7 else None
    agg["mean_D8_compliance"] = (sum(d8) / len(d8)) if d8 else None
    # D2 公式正确性 / D6 严重度排序 均值
    d2 = [r["d2"]["d2_rate"] for r in results if r.get("d2", {}).get("d2_rate") is not None]
    d6 = [r["d6_severity_rate"] for r in results if r.get("d6_severity_rate") is not None]
    agg["mean_D2"] = (sum(d2) / len(d2)) if d2 else None
    agg["mean_D6"] = (sum(d6) / len(d6)) if d6 else None
    _write_results(results, agg)
    return {"cases": results, "aggregate": agg}


def _write_results(results: List[Dict], agg: Dict) -> None:
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(TABLES, exist_ok=True)
    with open(os.path.join(RAW, "cases.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(TABLES, "report.json"), "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2)


def _print_report(agg: Dict) -> None:
    print("\n==== 聚合报告（方案 §6.4 主指标 + 支撑指标）====")
    print(f"样本数: {agg['n_cases']}")
    mr = agg["mean_MRhigh"]
    print(f"严重异常漏报率 MRhigh : {('%.1f%%' % (mr*100)) if mr is not None else 'N/A'}"
          f"  (mean over cases with high gold)")
    p = agg["mean_P"]; r = agg["mean_R"]; rw = agg["mean_Rw"]
    print(f"精确率 P : {('%.1f%%' % (p*100)) if p is not None else 'N/A'}")
    print(f"召回率 R : {('%.1f%%' % (r*100)) if r is not None else 'N/A'}")
    print(f"加权召回 Rw : {('%.1f%%' % (rw*100)) if rw is not None else 'N/A'}")
    d7 = agg.get("mean_D7"); d8 = agg.get("mean_D8_compliance")
    d2 = agg.get("mean_D2"); d6 = agg.get("mean_D6")
    print(f"解释质量 D7 (1~5) : {('%.2f' % d7) if d7 is not None else 'N/A'}")
    print(f"安全合规 D8 (合规率) : {('%.1f%%' % (d8*100)) if d8 is not None else 'N/A'}")
    print(f"公式正确 D2 : {('%.1f%%' % (d2*100)) if d2 is not None else 'N/A'}")
    print(f"严重度一致 D6 : {('%.1f%%' % (d6*100)) if d6 is not None else 'N/A'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="离线自检，不调 Hy3")
    ap.add_argument("--limit", type=int, default=None, help="仅跑前 N 个样本")
    args = ap.parse_args()
    out = run(offline=args.offline, limit=args.limit)
    _print_report(out["aggregate"])


if __name__ == "__main__":
    main()
