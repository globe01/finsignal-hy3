#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3 评估方法有效性验证（discriminative validation）—— 不调用 Hy3。

目的：验证「评测器能否稳定区分 good > medium > bad / adversarial」的离线模型输出。
输入：
  - data/derived/real_eval_samples.jsonl       （gold：expected_calculations / gold_facts / coverage / sample_status）
  - data/derived/real_eval_outputs_fixture.jsonl（待评测的离线模型输出，每条关联 sample_id，quality ∈ good/medium/bad/adversarial）
输出：
  - results/real_eval/discriminative_validation.csv  （逐条分数 + 典型扣分原因）
  - 控制台打印分数表 + 各档平均分 + 排序校验结论

评分维度（对应任务要求 6）：
  D1 事实数字一致性  fact       (0.40)  输出引用的数值是否与 gold_facts / expected_calculations 一致
  D2 N/A 处理       na         (0.20)  是否把 N/A 指标补 0 / 臆测（应明确标注 N/A，不补 0）
  D3 关键维度覆盖    coverage   (0.20)  是否覆盖收入趋势/盈利/现金流/营运资金/短期偿债/商誉/非经常性损益
  D4 无来源结论      sourcing   (0.10)  是否存在无锚定来源的风险结论（如买卖建议/造假认定）
  D5 结构清晰        structure  (0.10)  是否使用标题/分节，便于核查
总分 = 加权和（0-100）。期望排序：good > medium > bad、good > adversarial。
"""
import json
import re
import csv
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "data" / "derived" / "real_eval_samples.jsonl"
FIXTURE_PATH = ROOT / "data" / "derived" / "real_eval_outputs_fixture.jsonl"
OUT_CSV = ROOT / "results" / "real_eval" / "discriminative_validation.csv"
# 可提交副本：results/ 按仓库约定不入库，故同时写一份到 data/derived/ 以便入库复核
OUT_CSV_COMMIT = ROOT / "data" / "derived" / "discriminative_validation.csv"

# ---------- 数值解析 ---------- #
_NUM_PATTERN = r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_NUM_RE = re.compile(_NUM_PATTERN)
_RAT_RE = re.compile(rf"(?P<num>{_NUM_PATTERN})\s*(?P<pct>%?)")


def _to_float(tok: str) -> float:
    return float(tok.replace(",", ""))


def parse_amounts(text: str):
    """提取所有带 亿/万/万亿 后缀的金额，换算为「元」绝对值。"""
    out = []
    for m in re.finditer(rf"(?P<num>{_NUM_PATTERN})\s*(?P<unit>万亿|亿元|亿|万元|万)", text):
        val = _to_float(m.group("num"))
        unit = m.group("unit")
        if unit == "万亿":
            out.append(val * 1e12)
        elif unit in ("亿元", "亿"):
            out.append(val * 1e8)
        elif unit in ("万元", "万"):
            out.append(val * 1e4)
    return out


def _looks_like_year(tok: str, window: str, end_pos: int) -> bool:
    """判断数字是否更像年份而不是比率。"""
    stripped = tok.replace(",", "")
    if not stripped.isdigit():
        return False
    try:
        year = int(stripped)
    except ValueError:
        return False
    if not 1900 <= year <= 2100:
        return False
    tail = window[end_pos:end_pos + 3]
    return True if not tail or tail.startswith(("年", "-", "/", "至", "、", "，", ",", " ")) else False


def _has_amount_unit(window: str, end_pos: int) -> bool:
    """比率解析时剔除金额绝对额，避免把 12.65 亿元当成 12.65 倍。"""
    tail = window[end_pos:end_pos + 4].strip()
    return tail.startswith(("万亿", "亿元", "亿", "万元", "万", "元", "千元"))


def parse_ratios_near(keyword: str, text: str):
    """在 keyword 之后近邻文本内提取候选比率。

    真实模型输出常把金额折算成「亿元」、同时列出年份；这些数字如果被当成比率，
    会造成 PARTIAL 样本的 false positive。这里仅保留百分比或合理量级的小数/倍数。
    """
    pos = text.find(keyword)
    if pos < 0:
        return []
    window = text[pos: pos + 160]
    values = []
    for m in _RAT_RE.finditer(window):
        tok = m.group("num").replace(",", "").strip()
        try:
            val = float(tok)
        except ValueError:
            continue
        if _looks_like_year(tok, window, m.end()) or _has_amount_unit(window, m.end()):
            continue
        if m.group("pct"):
            val = val / 100.0
        values.append(val)
    return values


def parse_ratio_near(keyword: str, text: str):
    """返回 keyword 附近首个非年份候选比率；兼容 N/A 赋值检测。"""
    vals = parse_ratios_near(keyword, text)
    return vals[0] if vals else None


# ---------- 维度/短语定义 ---------- #
DIMENSIONS = {
    "收入趋势": ["收入趋势", "营收趋势", "收入由", "营收由", "营业收入由", "收入"],
    "盈利能力": ["盈利能力", "毛利率", "净利率", "盈利"],
    "现金流质量": ["现金流质量", "经营现金流", "现金含量", "现金流净额"],
    "营运资金压力": ["营运资金", "应收账款", "存货", "周转"],
    "短期偿债压力": ["短期偿债", "短期借款", "流动比率", "速动比率"],
    "商誉压力": ["商誉"],
    "非经常性损益": ["非经常性损益", "扣非", "非经"],
}

GROUNDING = ["基于合并报表主表", "基于给定的结构化", "基于给定", "基于上述",
             "依据给定", "上述结构化数据", "口径与 expected_calculations", "与 expected_calculations 一致"]
UNSOURCED_HARD_RISK = ["建议买入", "建议卖出", "买入", "卖出", "目标价", "强烈推荐", "强烈建议买入"]
UNSOURCED_SOFT_RISK = ["存在造假风险", "财务造假", "虚增", "粉饰", "操纵", "舞弊"]
NEGATIONS = ["不", "不能", "无法", "不可", "不应", "不构成", "不足以", "未", "没有", "不得"]

NA_ACK = ["N/A", "未列示", "未披露", "无法计算", "不适用", "缺失", "不可得", "不补"]


def has_phrase_near(keyword, phrases, text, before=25, after=70):
    pos = text.find(keyword)
    if pos < 0:
        return False
    window = text[max(0, pos - before): pos + after]
    return any(p in window for p in phrases)


def _has_negation_before(text: str, pos: int, width: int = 12) -> bool:
    window = text[max(0, pos - width):pos]
    return any(n in window for n in NEGATIONS)


def _risk_level(text: str) -> str | None:
    """返回 hard / soft / None。否定式免责声明不算风险结论。"""
    for phrase in UNSOURCED_HARD_RISK:
        pos = text.find(phrase)
        if pos >= 0 and not _has_negation_before(text, pos):
            return "hard"
    for phrase in UNSOURCED_SOFT_RISK:
        pos = text.find(phrase)
        if pos >= 0 and not _has_negation_before(text, pos):
            return "soft"
    return None


def _income_direction_flags(text: str) -> tuple[bool, bool]:
    """只在收入/营收所在分句内判断方向，避免被毛利率回升等后文污染。"""
    clauses = re.split(r"[。；;\n]", text)
    up = down = False
    for clause in clauses:
        if not re.search(r"收入|营收|营业收入", clause):
            continue
        if re.search(r"下降|下滑|负增长|收缩|缩减|减少|降至|降低", clause):
            down = True
        if re.search(r"增长|上升|提升|扩大|增加|增至|攀升", clause):
            up = True
    return up, down


# ---------- 单条评分 ---------- #
def score_fact(s, text):
    """返回 (correct, wrong, deductions)。"""
    gf = s["gold_facts"]
    ec = s["expected_calculations"]
    correct, wrong = 0, 0
    deductions = []

    specs = [
        ("毛利率", ["毛利率"], gf["latest_profitability"]["gross_margin"]),
        ("净利率", ["净利率"], gf["latest_profitability"]["net_margin"]),
        ("经营现金流质量", ["经营现金流净额/净利润", "经营现金流净额"], gf["latest_cashflow_quality"]["cfo_to_net_profit"]),
        ("流动比率", ["流动比率"], gf["latest_balance_sheet_pressure"]["current_ratio"]),
        ("应收占营收", ["应收占营收", "应收账款占营收"], ec["ar_to_revenue"]["value"]),
    ]
    for label, kws, gold in specs:
        vals = []
        for kw in kws:
            vs = parse_ratios_near(kw, text)
            if vs:
                vals = vs
                break
        if not vals:
            continue  # 该指标未在输出中引用，不计入
        if any(abs(val - gold) <= 0.15 * max(abs(gold), 1e-9) for val in vals):
            correct += 1
        else:
            wrong += 1
            shown = vals[0]
            deductions.append(f"事实错误：{label} 引用 {shown:.4f} 与 gold {gold:.4f} 偏差超 15%")

    # 收入（金额口径）
    end_rev = gf["revenue_trend"]["end_revenue"]
    start_rev = gf["revenue_trend"]["start_revenue"]
    amts = parse_amounts(text)
    rev_ok = any(abs(a - end_rev) <= 0.03 * abs(end_rev) for a in amts) or \
             any(abs(a - start_rev) <= 0.03 * abs(start_rev) for a in amts)
    if amts:
        if rev_ok:
            correct += 1
        else:
            wrong += 1
            deductions.append("事实错误：收入金额与 gold 不一致")

    # 收入趋势方向
    direction = gf["revenue_trend"]["direction"]
    income_up, income_down = _income_direction_flags(text)
    if direction == "up" and income_down and not income_up:
        wrong += 1
        deductions.append("趋势误读：gold 收入向上但输出称下降/下滑")
    elif direction == "down" and income_up and not income_down:
        wrong += 1
        deductions.append("趋势误读：gold 收入向下但输出称上升/增长")
    else:
        correct += 1  # 方向正确（或无方向表述）

    fact_score = (correct / (correct + wrong) * 100.0) if (correct + wrong) > 0 else 0.0
    return fact_score, deductions


def score_na(s, text):
    """N/A 指标是否被补 0 / 臆测。无 N/A 指标则返回 100。"""
    na_metrics = s["coverage"]["na_metric_names"]
    if not na_metrics:
        return 100.0, []
    correct = wrong = neutral = 0
    deductions = []
    mapping = {
        "goodwill_to_equity": ["商誉"],
        "short_borrow_to_cash": ["短期借款"],
    }
    for m in na_metrics:
        for kw in mapping.get(m, [m]):
            if kw in text:
                ack = has_phrase_near(kw, NA_ACK, text, before=60, after=240)
                val = parse_ratio_near(kw, text)
                if ack:
                    correct += 1
                elif val is not None:
                    wrong += 1
                    deductions.append(f"N/A 当 0/臆测：{m} 原表缺失却被赋值为 {val:.4f}")
                else:
                    neutral += 1
                break
        else:
            neutral += 1
    raw = (correct - wrong - 0.5 * neutral) / max(1, correct + wrong + neutral)
    return max(0.0, min(100.0, 50 + 50 * raw)), deductions


def score_coverage(text):
    covered = 0
    for _dim, kws in DIMENSIONS.items():
        if any(kw in text for kw in kws):
            covered += 1
    return covered / len(DIMENSIONS) * 100.0


def score_sourcing(text):
    grounded = any(p in text for p in GROUNDING)
    risk = _risk_level(text)
    if risk == "hard" and not grounded:
        return 50.0, ["无来源结论：含买卖建议/造假认定等强结论且无合并报表主表锚定"]
    if risk == "hard" and grounded:
        return 85.0, ["无来源结论（弱）：含强结论但有数据锚定，扣分较轻"]
    if risk == "soft" and not grounded:
        return 75.0, ["边界措辞偏强：含造假/粉饰等风险词但缺少充分主表锚定"]
    if risk == "soft" and grounded:
        return 90.0, ["边界措辞偏强：含造假/粉饰等风险词，需人工复核语义强度"]
    if not grounded:
        return 80.0, ["未显式锚定合并报表主表口径（轻微扣分）"]
    return 100.0, []


def score_structure(text):
    headings = sum(1 for ln in text.splitlines() if ln.strip().startswith("#"))
    bullets = sum(1 for ln in text.splitlines() if ln.strip().startswith((">", "-", "*")))
    score = 50.0 + headings * 8.0 + (5.0 if bullets else 0.0)
    return max(0.0, min(100.0, score)), []


WEIGHTS = {"fact": 0.40, "na": 0.20, "coverage": 0.20, "sourcing": 0.10, "structure": 0.10}


def evaluate_one(s, text):
    fact, d_fact = score_fact(s, text)
    na, d_na = score_na(s, text)
    cov = score_coverage(text)
    src, d_src = score_sourcing(text)
    struct, d_struct = score_structure(text)
    total = (WEIGHTS["fact"] * fact + WEIGHTS["na"] * na +
             WEIGHTS["coverage"] * cov + WEIGHTS["sourcing"] * src +
             WEIGHTS["structure"] * struct)
    deductions = d_fact + d_na + d_src + d_struct
    return {
        "fact": round(fact, 1), "na": round(na, 1), "coverage": round(cov, 1),
        "sourcing": round(src, 1), "structure": round(struct, 1),
        "total": round(total, 1), "deductions": deductions,
    }


def load_jsonl(path):
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        o = json.loads(line)
        out.setdefault(o["sample_id"], []).append(o)
    return out


def main():
    samples = {s["sample_id"]: s for s in (
        json.loads(l) for l in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if l.strip())}
    fixtures = load_jsonl(FIXTURE_PATH)

    rows = []
    for sid, outs in fixtures.items():
        s = samples.get(sid)
        if s is None:
            print(f"[WARN] fixture sample_id {sid} 在 gold 中缺失，跳过", file=sys.stderr)
            continue
        for o in outs:
            res = evaluate_one(s, o["output"])
            rows.append({
                "sample_id": sid, "company": o["company"], "quality": o["quality"],
                "sample_status": s["sample_status"],
                "na_metrics": ";".join(s["coverage"]["na_metric_names"]) or "-",
                **res,
                "deductions": " | ".join(res["deductions"]) or "-",
            })

    # 写 CSV：同时写 results/（本地预览，gitignored）与 data/derived/（可提交副本）
    fields = ["sample_id", "company", "quality", "sample_status", "na_metrics",
              "fact", "na", "coverage", "sourcing", "structure", "total", "deductions"]
    for out_path in (OUT_CSV, OUT_CSV_COMMIT):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r)

    # 控制台分数表
    print("=" * 100)
    print("Phase 3 评估方法有效性验证 —— 逐条分数（总分 = 0.4·fact + 0.2·na + 0.2·cov + 0.1·src + 0.1·struct）")
    print("=" * 100)
    hdr = f"{'sample_id':22} {'quality':11} {'status':8} {'fact':>5} {'na':>5} {'cov':>5} {'src':>5} {'str':>5} {'TOTAL':>6}"
    print(hdr)
    print("-" * 100)
    by_q = defaultdict(list)
    for r in rows:
        by_q[r["quality"]].append(r["total"])
        print(f"{r['sample_id']:22} {r['quality']:11} {r['sample_status']:8} "
              f"{r['fact']:>5} {r['na']:>5} {r['coverage']:>5} {r['sourcing']:>5} {r['structure']:>5} {r['total']:>6}")

    print("-" * 100)
    avg = {q: sum(v) / len(v) for q, v in by_q.items()}
    print("各档平均分：")
    for q in ["good", "medium", "bad", "adversarial"]:
        print(f"  {q:11} n={len(by_q[q]):2}  avg={avg.get(q, 0):.2f}")
    print("-" * 100)

    # 排序校验
    g, m, b, adv = avg.get("good", 0), avg.get("medium", 0), avg.get("bad", 0), avg.get("adversarial", 0)
    checks = []
    checks.append(("good > medium", g > m))
    checks.append(("good > bad", g > b))
    checks.append(("good > adversarial", g > adv))
    checks.append(("medium > bad", m > b))
    checks.append(("medium > adversarial", m > adv))
    all_pass = all(ok for _, ok in checks)
    print("排序校验：")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({g:.2f}/{m:.2f}/{b:.2f}/{adv:.2f})")
    print("=" * 100)
    print(f"结论：{'全部排序假设成立 [PASS]' if all_pass else '存在未通过项 [FAIL]'}")
    print(f"输出已写入：{OUT_CSV}（本地预览，gitignored）")
    print(f"可提交副本：{OUT_CSV_COMMIT}（data/derived/，可入库复核）")
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
