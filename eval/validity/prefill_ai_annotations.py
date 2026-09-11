"""生成 AI 辅助标注预填表。

重要边界：本脚本输出是 AI-assisted prefill，不是人工金标准。它用于帮人工标注者
预填候选答案、降低复核成本；正式提交人工一致性时仍应由真实标注者独立复核后另存。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.schema import SIGNAL_TYPES
from eval.rules import severity_label


ROOT = Path(__file__).resolve().parent.parent.parent
TARGET_SIGNALS = [s for s in SIGNAL_TYPES if s != "other"]
NOTE = "AI-assisted prefill; not human gold."


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(rows: list[dict[str, Any]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _periods(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {p for p in re.split(r"[|,，/、\s]+", value) if p}
    return {str(v) for v in value if str(v)}


def _period_overlap(a: Any, b: Any) -> bool:
    pa, pb = _periods(a), _periods(b)
    return not pa or not pb or bool(pa & pb)


def _has_unnegated(text: str, phrases: list[str], width: int = 12) -> bool:
    negations = ("不", "不能", "无法", "不可", "不应", "不构成", "不足以", "未", "没有", "不得")
    for phrase in phrases:
        pos = text.find(phrase)
        if pos < 0:
            continue
        if not any(n in text[max(0, pos - width):pos] for n in negations):
            return True
    return False


def _d8_violation(card: dict[str, Any]) -> bool:
    text = json.dumps(card, ensure_ascii=False)
    hard = ["建议买入", "建议卖出", "强烈建议", "强烈推荐", "目标价", "买入", "卖出"]
    accusations = ["财务造假", "存在造假", "舞弊", "虚增收入", "利润操纵"]
    return _has_unnegated(text, hard) or _has_unnegated(text, accusations)


def _d7_score(card: dict[str, Any], valid: bool, violation: bool) -> float:
    if violation:
        return 1.0
    facts = card.get("fact_basis") or []
    evidence = bool(facts)
    strict_evidence = evidence and all(
        f.get("source_record_id") and f.get("metric_key") and f.get("period")
        and f.get("unit") and f.get("value") is not None
        for f in facts
    )
    has_explanation = bool(card.get("supported_explanation")) or bool(card.get("possible_explanations"))
    has_next_checks = bool(card.get("next_checks"))
    has_boundary = bool(str(card.get("conclusion_boundary") or "").strip())
    if not valid:
        return 2.0 if evidence else 1.0
    if strict_evidence and has_explanation and has_next_checks and has_boundary:
        return 5.0
    if evidence and has_boundary and (has_explanation or has_next_checks):
        return 4.0
    if evidence:
        return 3.0
    return 2.0


def prefill_card_annotations(todo_csv: Path, cases_json: Path, out_csv: Path) -> None:
    rows = _read_csv(todo_csv)
    cases = _load_json(cases_json)
    out: list[dict[str, Any]] = []
    for row in rows:
        case_idx = int(row["case_id"].split("_")[1])
        card_idx = int(row["card_id"].split("_")[1])
        case = cases[case_idx]
        card = (case.get("cards_full") or [])[card_idx]
        gold = case.get("gold") or []
        matches = [
            g for g in gold
            if g.get("signal_type") == row["signal_type"]
            and _period_overlap(g.get("periods"), row.get("period"))
        ]
        valid = bool(matches)
        severity = matches[0].get("severity", "medium") if matches else "na"
        violation = _d8_violation(card)
        score = _d7_score(card, valid=valid, violation=violation)
        note_bits = [NOTE]
        if valid:
            note_bits.append("signal matches synthetic gold by type/period overlap")
        else:
            note_bits.append("no matching synthetic gold signal by type/period overlap")
        if violation:
            note_bits.append("contains unnegated investment advice or unsupported accusation")

        filled = dict(row)
        filled["signal_valid"] = "yes" if valid else "no"
        filled["severity_label"] = severity
        filled["d7_score"] = f"{score:.1f}"
        filled["d8_violation"] = "yes" if violation else "no"
        filled["note"] = " ".join(note_bits)
        out.append(filled)

    _write_csv(out, out_csv, list(rows[0].keys()))


def _growth(prev: float | None, cur: float | None) -> float | None:
    if prev in (None, 0) or cur is None:
        return None
    return (cur - prev) / abs(prev)


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0):
        return None
    return num / den


def _gold_real_signals(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    data = sample["input_financials"]
    years = sorted(int(y) for y in data)

    def v(year: int, key: str) -> float | None:
        return data[str(year)].get(key)

    signals: dict[str, dict[str, Any]] = {}

    # 经营现金流/净利润连续两年低于 0.5，且净利润为正。
    pairs = []
    for prev, cur in zip(years, years[1:]):
        r1 = _ratio(v(prev, "cfo"), v(prev, "net_profit")) if (v(prev, "net_profit") or 0) > 0 else None
        r2 = _ratio(v(cur, "cfo"), v(cur, "net_profit")) if (v(cur, "net_profit") or 0) > 0 else None
        if r1 is not None and r2 is not None and r1 < 0.5 and r2 < 0.5:
            pairs.append((min(r1, r2), [str(prev), str(cur)]))
    if pairs:
        x, periods = min(pairs, key=lambda p: p[0])
        signals["cashflow_profit_divergence"] = {"severity": severity_label((0.5 - x) / 0.2), "period": "|".join(periods)}

    # 增速差不低于 20 个百分点。
    for signal, num_key, den_key, theta, sigma in [
        ("receivables_revenue_divergence", "accounts_receivable", "revenue", 0.20, 0.10),
        ("inventory_cost_divergence", "inventory", "cogs", 0.20, 0.10),
    ]:
        hits = []
        for prev, cur in zip(years, years[1:]):
            g_num = _growth(v(prev, num_key), v(cur, num_key))
            g_den = _growth(v(prev, den_key), v(cur, den_key))
            if g_num is None or g_den is None:
                continue
            gap = g_num - g_den
            if gap >= theta:
                hits.append((gap, str(cur)))
        if hits:
            x, period = max(hits, key=lambda p: p[0])
            signals[signal] = {"severity": severity_label((x - theta) / sigma), "period": period}

    # 按标注指南文字口径：毛利率上升且净利率下降，二者差额不低于 2 个百分点。
    hits = []
    for prev, cur in zip(years, years[1:]):
        gm_prev = _ratio((v(prev, "revenue") or 0) - (v(prev, "cogs") or 0), v(prev, "revenue"))
        gm_cur = _ratio((v(cur, "revenue") or 0) - (v(cur, "cogs") or 0), v(cur, "revenue"))
        nm_prev = _ratio(v(prev, "net_profit"), v(prev, "revenue"))
        nm_cur = _ratio(v(cur, "net_profit"), v(cur, "revenue"))
        if None in (gm_prev, gm_cur, nm_prev, nm_cur):
            continue
        gm_change = gm_cur - gm_prev
        nm_change = nm_cur - nm_prev
        gap = gm_change - nm_change
        if gm_change > 0 and nm_change < 0 and gap >= 0.02:
            hits.append((gap, str(cur)))
    if hits:
        x, period = max(hits, key=lambda p: p[0])
        signals["gross_net_margin_divergence"] = {"severity": severity_label((x - 0.02) / 0.02), "period": period}

    ratio_specs = [
        ("nonrecurring_profit_dependence", "nonrecurring", "net_profit", 0.30, 0.20, True),
        ("goodwill_net_assets_pressure", "goodwill", "equity", 0.20, 0.10, False),
    ]
    for signal, num_key, den_key, theta, sigma, use_abs in ratio_specs:
        hits = []
        for year in years:
            r = _ratio(v(year, num_key), v(year, den_key))
            if r is None:
                continue
            x = abs(r) if use_abs else r
            if x >= theta:
                hits.append((x, str(year)))
        if hits:
            x, period = max(hits, key=lambda p: p[0])
            signals[signal] = {"severity": severity_label((x - theta) / sigma), "period": period}

    hits = []
    for year in years:
        current_ratio = _ratio(v(year, "current_assets"), v(year, "current_liabilities"))
        short_to_cash = _ratio(v(year, "short_borrow"), v(year, "cash"))
        if current_ratio is not None and short_to_cash is not None and current_ratio < 1 and short_to_cash > 1:
            hits.append((short_to_cash, str(year)))
    if hits:
        x, period = max(hits, key=lambda p: p[0])
        signals["short_term_solvency_pressure"] = {"severity": severity_label((x - 1.0) / 0.5), "period": period}

    return signals


def _clauses(text: str) -> list[str]:
    text = text.replace("*", "")
    return [c for c in re.split(r"[。；;\n]", text) if c.strip()]


def _has_signal_text(text: str, signal: str) -> bool:
    clauses = _clauses(text)

    def any_clause(keys: list[str], risks: list[str], benign: list[str] | None = None) -> bool:
        benign = benign or ["压力极低", "压力较小", "无忧", "极小", "极低", "可忽略", "不构成", "尚低", "暂未形成", "无明显", "改善", "回落"]
        for clause in clauses:
            if all(k in clause for k in keys) and any(r in clause for r in risks):
                if not any(b in clause for b in benign):
                    return True
        return False

    if signal == "cashflow_profit_divergence":
        benign = ["优良", "充足", "强劲", "高达", "覆盖充足", "充沛", "含金量", "改善", "有余"]
        for clause in clauses:
            if "现金流" not in clause:
                continue
            if "缺口" in clause and "偿债" in clause:
                continue
            if any(b in clause for b in benign):
                continue
            if any(r in clause for r in ["背离", "低于", "不足", "承压", "恶化", "转负", "净流出", "真实性需附注佐证"]):
                return True
        return False
    if signal == "receivables_revenue_divergence":
        for clause in clauses:
            if "应收" not in clause:
                continue
            if any(b in clause for b in ["低于同期收入", "占收入比重微降", "无明显", "回落", "改善", "字段仅包含"]):
                continue
            if any(r in clause for r in ["背离", "逆势", "快增长", "较快增长", "高企", "回款变慢"]):
                return True
            if "应收账款占营收" in clause and "升至" in clause:
                return True
        return False
    if signal == "inventory_cost_divergence":
        for clause in clauses:
            if "存货" not in clause:
                continue
            if any(b in clause for b in ["字段仅包含", "速动比率", "去库存", "下降", "回落", "缓解", "稳定", "无明显", "未现恶化"]):
                continue
            if any(r in clause for r in ["背离", "逆势", "积压", "攀升", "高企", "未见有效去化", "跌价风险"]):
                return True
        return False
    if signal == "gross_net_margin_divergence":
        return any_clause(["毛利", "净利"], ["背离", "分化", "毛利率上升而净利率下降", "毛利率回升但净利率下降"])
    if signal == "nonrecurring_profit_dependence":
        return any_clause(
            ["非经常"],
            ["高度依赖", "依赖非主业", "修饰明显", "影响较大", "不可忽视", "58.8", "51.5"],
            ["影响微弱", "不构成", "干扰已弱化", "10%以下"],
        )
    if signal == "goodwill_net_assets_pressure":
        for clause in clauses:
            if "商誉" not in clause:
                continue
            if clause.strip().endswith("商誉压力") or "字段仅包含" in clause:
                continue
            if any(b in clause for b in ["极小", "极低", "可忽略", "不构成", "无法", "缺失", "尚低", "占比尚低", "压力较小", "暂未形成"]):
                continue
            if re.search(r"商誉.{0,30}(高达|高企|重大|显著风险|重大压力|减值压力|占净资产比例较高)", clause):
                return True
        return False
    if signal == "short_term_solvency_pressure":
        return any_clause(
            ["偿债"],
            ["偏弱", "缺口", "再融资依赖", "疲软", "低于1", "错配", "承压", "压力随"],
            ["压力极低", "压力较小", "无忧", "趋于缓和", "缓冲充足", "极强"],
        )
    return False


def prefill_real_signal_annotations(todo_csv: Path, samples_jsonl: Path, outputs_jsonl: Path, out_csv: Path) -> None:
    rows = _read_csv(todo_csv)
    samples = {r["sample_id"]: r for r in _load_jsonl(samples_jsonl)}
    outputs = {r["sample_id"]: r for r in _load_jsonl(outputs_jsonl)}
    gold_by_sample = {sid: _gold_real_signals(samples[sid]) for sid in outputs}
    out: list[dict[str, Any]] = []
    for row in rows:
        sid = row["sample_id"]
        signal = row["signal_type"]
        gold = gold_by_sample.get(sid, {}).get(signal)
        text = outputs[sid]["model_output"]
        detected = _has_signal_text(text, signal)
        filled = dict(row)
        filled["human_gold_present"] = "yes" if gold else "no"
        filled["human_gold_severity"] = gold["severity"] if gold else "na"
        filled["model_detected"] = "yes" if detected else "no"
        filled["period"] = gold["period"] if gold else ""
        filled["note"] = NOTE
        if signal == "impairment_loss_surge":
            filled["note"] += " impairment/total_profit fields unavailable in this real-sample schema."
        out.append(filled)
    _write_csv(out, out_csv, list(rows[0].keys()))

    summary = Counter()
    for row in out:
        if row["annotator"] != "A":
            continue
        key = (row["human_gold_present"], row["model_detected"])
        summary[key] += 1
    print(f"real signal AI prefill summary: {dict(summary)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 AI 辅助标注预填表（非人工金标准）")
    parser.add_argument("--annotations-todo", default="eval/validity/annotations_todo.csv")
    parser.add_argument("--cases", default="results/online_local/cases_run1.json")
    parser.add_argument("--annotations-out", default="eval/validity/annotations_ai_prefill.csv")
    parser.add_argument("--real-signal-todo", default="eval/validity/real_signal_todo.csv")
    parser.add_argument("--samples", default="data/derived/real_eval_samples.jsonl")
    parser.add_argument("--outputs", default="data/derived/hy3_real_outputs.jsonl")
    parser.add_argument("--real-signal-out", default="eval/validity/real_signal_ai_prefill.csv")
    args = parser.parse_args()

    prefill_card_annotations(Path(args.annotations_todo), Path(args.cases), Path(args.annotations_out))
    prefill_real_signal_annotations(
        Path(args.real_signal_todo), Path(args.samples), Path(args.outputs), Path(args.real_signal_out)
    )
    print(f"wrote {args.annotations_out}")
    print(f"wrote {args.real_signal_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
