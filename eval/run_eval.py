"""评测编排（对应方案 §6、§7、§13 eval/run_eval.py）。

流程：构造样本集（清洁/注入/阴性/阈下）→ 调 Hy3 盲扫（并发<=5）→ 四层金标准（§4.2）→
D1~D8 评估 → micro 聚合输出严重异常漏报率等核心指标。

用法：
  python -m eval.run_eval --offline            # 离线自检（不需要 openai / API Key）
  python -m eval.run_eval --limit 3            # 仅跑 3 个样本的真实 Hy3 扫描（冒烟）
  python -m eval.run_eval --runs 3             # 全量跑 3 次，报告 均值/最小/最大（§6.4）
  python -m eval.run_eval --period-mode exact  # 期间严格匹配（敏感性分析）
  python -m eval.run_eval --limit 3 --hy3-judge # 额外跑 Hy3 语义评审并与规则 Rubric 对比

关键设计：
- **离线模式不导入 openai、不构造任何客户端**：app.scan / app.llm 全部延迟到真正联网时才导入，
  因此没装 openai、没配 API Key 也能跑通 `--offline`。
- **离线自检构造的"完美模型"输出带完整证据链**：fact_basis 由记录索引回填、
  calculation 由注册表安全函数复算。预期 P/R/Rw=100%、MRhigh=0%、D1/D2/D3=100%；
  任一项不足 100% 说明评估器本身有 bug，而不是模型不行（方案 §6.4 自检）。
- **注入样本的金标准来自注入元数据**，注入未生效的样本整例剔除并记录原因。
- **聚合一律 micro**（分子分母跨样本相加），macro 仅作附加参考。
"""
from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from app.formulas import load_registry
from app.schema import AnomalyCard, Calculation, FactBasis, ScanOutput
from eval.fact_eval import aggregate_facts
from eval.formula_eval import METRIC_LOC, evaluate_d2, formula_ids_for_signal, recompute
from eval.ground_truth import (
    KIND_BOUNDARY,
    KIND_INJECTED,
    KIND_NEGATIVE,
    build_ground_truth,
    finalize_injection,
)
from eval.hy3_judge import compare_judges, judge_cards_with_hy3
from eval.rule_rubric import judge_cards
from eval.set_eval import PERIOD_MATCH_OVERLAP, compute_metrics, summarize
from generator.base import build_record_index, company_to_text, make_clean_company, record_id_of
from generator.inject import inject
from generator.negative import make_boundary_control, make_negative_control

ROOT = os.path.join(os.path.dirname(__file__), "..")
RAW = os.path.join(ROOT, "results", "raw")
TABLES = os.path.join(ROOT, "results", "tables")
_SEM = threading.Semaphore(5)  # 混元默认并发上限（方案 §5.1）

INJECT_TYPES = [
    "receivables_revenue_divergence",
    "inventory_cost_divergence",
    "goodwill_net_assets_pressure",
    "gross_net_margin_divergence",
    "cashflow_profit_divergence",
]

# 三档强度梯度（方案 §7.2）：各类型选取能稳定触发 低/中/高 的严重度强度。
# 具体严重度标签由 severity.yaml 在注入后数据上复算，不在此硬编码。
SEVERITY_STRENGTHS = {
    "receivables_revenue_divergence": (0.8, 1.0, 2.0),
    "inventory_cost_divergence": (0.8, 1.0, 2.0),
    "goodwill_net_assets_pressure": (0.8, 1.0, 2.0),
    "gross_net_margin_divergence": (0.6, 0.9, 2.0),
    "cashflow_profit_divergence": (0.4, 0.8, 1.6),
}

# 长文本 / 术语堆砌对抗样本使用的「补充背景」段落。
# 注意：这些文本只供模型阅读，不参与任何数值复算；段落里**刻意不写**异常数字，
# 目的是测试模型在长文本干扰下是否仍能从结构化数据中发现异常（实测结果见在线评测）。
_LONG_NARRATIVE = (
    "公司本年度围绕主业推进产能扩张与产品结构升级，管理层认为行业景气度处于温和复苏通道。"
    "报告期内公司持续加强应收账款与存货周转管理，经营性现金流受资本开支节奏影响有所波动。"
    "公司严格执行会计政策，商誉减值测试采用收益法并结合宏观景气度进行审慎判断。"
    "展望下一阶段，公司将在保持研发投入的同时优化费用投放效率，力争实现高质量增长。"
    "董事会就利润分配、关联交易及内部控制等事项进行了审议，相关事项均履行了合规程序。"
)

_JARGON_NARRATIVE = (
    "本报告基于 EBITDA 同比环比、经营性现金流净额与净利润的背离度、商誉占净资产比等衍生指标，"
    "结合行业景气度下行周期中的营运资本周转天数、应收账款账龄结构及信用减值损失计提充分性，"
    "运用杜邦分析拆解 ROE 变动，并参照可比公司 EV/EBITDA 与 PEG 进行相对估值交叉验证；"
    "同时关注自由现金流贴现模型（DCF）中 WACC 与永续增长率的敏感性，"
    "以及商誉减值测试中税前折现率与税后现金流口径的一致性，避免口径错配导致的指标失真。"
)


def _with_narrative(company, text: str) -> Company:
    company.narrative = text
    return company


# ---------------------------------------------------------------- 样本集
def build_dataset() -> List[Dict]:
    """构造 ≥40 个**合成（synthetic）**评测窗口（方案 §8.2）。

    类别覆盖（需求清单）：
    - 正常阴性样本（清洁公司，金标准为空）
    - 低/中/高三档异常（5 类可注入 × 3 强度）
    - 临界阈值样本（5 类近阈下边界）
    - 长文本但漏报关键异常（清洁/注入 + 长 MD&A 背景）
    - 专业术语堆砌（清洁/注入 + 术语背景）
    - 数字正确但年份错置（不同日历年份锚点的注入窗口，检验期间归属）
    - 公式分母错误 / 重复报告 / 表述造假：见 eval/validity 对抗夹具（derived，非真实模型输出）

    注入样本同时保留注入前基底（base）与注入元数据（injection），
    使金标准可以完全由注入侧独立给出，并在注入后重跑规则做有效性核验。
    """
    cases: List[Dict] = []

    # 1) 正常阴性样本：8 个清洁公司（不同基底，避免同源重复）
    for i in range(8):
        c = make_negative_control(seed=i, name=f"阴性对照{i+1}")
        cases.append({"company": c, "kind": KIND_NEGATIVE, "base": None, "injection": None,
                      "meta": {"kind": KIND_NEGATIVE, "inject": None, "category": "negative",
                               "name": c.name}})

    # 2) 低/中/高三档异常：5 类 × 各 3 档强度（按类型选取能稳定触发低/中/高的强度）
    for st in INJECT_TYPES:
        for s in SEVERITY_STRENGTHS.get(st, (0.8, 1.0, 2.0)):
            base = make_clean_company(name=f"注入_{st}_{s}")
            c, im = inject(base, st, strength=s)
            finalize_injection(base, c, im)
            cases.append({"company": c, "kind": KIND_INJECTED, "base": base, "injection": im,
                          "meta": {"kind": KIND_INJECTED, "inject": st, "strength": s,
                                   "severity": im.target_severity, "category": "injected",
                                   "name": c.name}})

    # 3) 临界阈值样本：5 类近阈下边界
    for st in INJECT_TYPES:
        c = make_boundary_control(st)
        cases.append({"company": c, "kind": KIND_BOUNDARY, "base": None, "injection": None,
                      "meta": {"kind": KIND_BOUNDARY, "inject": st, "category": "boundary",
                               "name": c.name}})

    # 4) 长文本但漏报关键异常：2 清洁 + 3 注入（带长 MD&A 背景）
    for i in range(2):
        c = make_negative_control(seed=100 + i, name=f"长文本阴性{i+1}")
        cases.append({"company": _with_narrative(c, _LONG_NARRATIVE), "kind": KIND_NEGATIVE,
                      "base": None, "injection": None,
                      "meta": {"kind": KIND_NEGATIVE, "inject": None, "category": "long_text",
                               "name": c.name}})
    for st in ("receivables_revenue_divergence", "goodwill_net_assets_pressure",
               "cashflow_profit_divergence"):
        base = make_clean_company(name=f"长文本注入_{st}")
        c, im = inject(base, st, strength=1.0)
        finalize_injection(base, c, im)
        cases.append({"company": _with_narrative(c, _LONG_NARRATIVE), "kind": KIND_INJECTED,
                      "base": base, "injection": im,
                      "meta": {"kind": KIND_INJECTED, "inject": st, "strength": 1.0,
                               "severity": im.target_severity, "category": "long_text",
                               "name": c.name}})

    # 5) 专业术语堆砌：2 清洁 + 2 注入（带术语背景）
    for i in range(2):
        c = make_negative_control(seed=200 + i, name=f"术语堆砌阴性{i+1}")
        cases.append({"company": _with_narrative(c, _JARGON_NARRATIVE), "kind": KIND_NEGATIVE,
                      "base": None, "injection": None,
                      "meta": {"kind": KIND_NEGATIVE, "inject": None, "category": "jargon",
                               "name": c.name}})
    for st in ("inventory_cost_divergence", "gross_net_margin_divergence"):
        base = make_clean_company(name=f"术语堆砌注入_{st}")
        c, im = inject(base, st, strength=1.0)
        finalize_injection(base, c, im)
        cases.append({"company": _with_narrative(c, _JARGON_NARRATIVE), "kind": KIND_INJECTED,
                      "base": base, "injection": im,
                      "meta": {"kind": KIND_INJECTED, "inject": st, "strength": 1.0,
                               "severity": im.target_severity, "category": "jargon",
                               "name": c.name}})

    # 6) 数字正确但年份错置：不同日历年份锚点的注入窗口，检验期间归属
    for years in ([2020, 2021, 2022], [2021, 2022, 2023], [2022, 2023, 2024]):
        st = "receivables_revenue_divergence"
        base = make_clean_company(name=f"年份偏移_{years[0]}", years=list(years))
        c, im = inject(base, st, strength=1.0)
        finalize_injection(base, c, im)
        cases.append({"company": c, "kind": KIND_INJECTED, "base": base, "injection": im,
                      "meta": {"kind": KIND_INJECTED, "inject": st, "strength": 1.0,
                               "severity": im.target_severity, "category": "year_shift",
                               "years": list(years), "name": c.name}})

    return cases


# ---------------------------------------------------------------- 在线扫描
def _scan_one(case: Dict) -> ScanOutput:
    """联网扫描。app.scan 在此处延迟导入，保证 --offline 不依赖 openai。"""
    from app.scan import scan_text  # noqa: PLC0415 —— 故意延迟导入

    c = case["company"]
    with _SEM:
        # 评测默认 temperature=0 以保证可复现（方案 §6.4：多次运行取均值）
        return scan_text(company_to_text(c), company=c.name,
                         years="-".join(str(y) for y in c.years),
                         temperature=0)


# ---------------------------------------------------------------- 离线完美模型
_BOUNDARY_TEXT = "仅为值得关注的信号，不能据此认定财务造假，也不构成投资建议。"


def _perfect_cards(gold: List[dict], company) -> List[AnomalyCard]:
    """离线自检：把金标准构造成"完美模型输出"，含可回表的完整证据链。

    fact_basis 直接取自记录索引（source_record_id/行列/数值全部真实），
    calculation 用注册表安全函数复算。预期 D1/D2/D3 全部 100%；
    若不足 100%，说明评估器实现有问题，可在不花 API 额度的情况下暴露出来。
    """
    record_idx = build_record_index(company)
    reg = load_registry()
    cards: List[AnomalyCard] = []
    for g in gold:
        periods = [str(p) for p in g.get("periods", [])]
        years = [int(p) for p in periods if str(p).isdigit() and int(p) in company.years]
        t = max(years) if years else company.years[-1]

        fid: Optional[str] = None
        expected: Optional[float] = None
        for cand in formula_ids_for_signal(g["signal_type"]):
            v = recompute(cand, company, t)
            if v is not None:
                fid, expected = cand, v
                break

        facts: List[FactBasis] = []
        calc: Optional[Calculation] = None
        if fid:
            spec = reg[fid]
            need_years = [t]
            if spec.get("period_mode") == "pair":
                i = company.years.index(t)
                if i > 0:
                    need_years = [company.years[i - 1], t]
            for key in spec["inputs"]:
                loc = METRIC_LOC.get(key)
                if loc is None:
                    continue
                for yr in need_years:
                    rid = record_id_of(loc, key, yr)
                    rec = record_idx.get(rid or "")
                    if rec is None:
                        continue
                    facts.append(FactBasis(
                        fact_id=f"{key}_{yr}", metric_key=rec.metric_key,
                        metric_name=rec.metric_name, period=rec.period, value=rec.value,
                        unit=rec.unit, source_record_id=rec.record_id,
                        source_file=rec.source_file, source_row=rec.source_row,
                        source_column=rec.source_column, statement=rec.statement,
                    ))
            calc = Calculation(
                formula_id=fid,
                operand_fact_ids=[f.fact_id for f in facts if f.fact_id],
                reported_result=expected,
                readable="离线自检：由公式注册表安全函数复算",
            )
        cards.append(AnomalyCard(
            signal_type=g["signal_type"], severity=g["severity"], periods=periods,
            fact_basis=facts, calculation=calc,
            supported_explanation="离线自检占位：本卡片由金标准构造，用于校验指标数学与证据核验链路。",
            possible_explanations=["离线自检占位假设，不代表真实业务解释"],
            next_checks=["离线自检占位核查项"],
            conclusion_boundary=_BOUNDARY_TEXT,
        ))
    return cards


# ---------------------------------------------------------------- 单样本评估
def evaluate_case(case: Dict, cards: List[AnomalyCard],
                  period_mode: str = PERIOD_MATCH_OVERLAP,
                  hy3_judge: bool = False) -> Dict:
    company = case["company"]
    gt = build_ground_truth(company, case["kind"], meta=case.get("injection"),
                            base_company=case.get("base"))
    gold = gt["gold"]
    model_enum = [
        {"signal_type": c.signal_type.value, "periods": c.periods, "severity": c.severity.value}
        for c in cards if c.signal_type.value != "other"
    ]
    n_other = sum(1 for c in cards if c.signal_type.value == "other")

    metrics = compute_metrics(gold, model_enum, period_mode=period_mode)
    # 目标-only 口径：剔除复合信号后的金标准，用于双报（方案 §5.4）
    metrics_target_only = compute_metrics(
        gt["gold_target_only"], model_enum, period_mode=period_mode)
    metrics_exact = compute_metrics(gold, model_enum, period_mode="exact")

    facts = aggregate_facts(cards, company)
    judge = judge_cards(cards)
    d2 = evaluate_d2(cards, company)

    # Hy3-as-Judge 语义评审（可选，每卡 1 次调用）。与规则 Rubric 并列上报，
    # 不覆盖 judge 字段 —— 两条路径都不是 D7/D8 真值（见 eval/hy3_judge.py 顶部说明）。
    hy3_judge_result = None
    judge_agreement = None
    if hy3_judge and cards:
        hy3_judge_result = judge_cards_with_hy3(cards, company_to_text(company))
        judge_agreement = compare_judges(judge, hy3_judge_result)

    # D6 严重度一致性（代理指标）：配对成功的信号中严重度标签一致的比例
    gold_sev = {(g["signal_type"]): g["severity"] for g in gold}
    sev_hits = sev_total = 0
    for m in model_enum:
        if m["signal_type"] in gold_sev:
            sev_total += 1
            if m["severity"] == gold_sev[m["signal_type"]]:
                sev_hits += 1

    return {
        "meta": case["meta"],
        "case_scored": gt["case_scored"],
        "ground_truth": gt,
        "gold": gold,
        "model": model_enum,
        "n_other_cards": n_other,
        "metrics": metrics,
        "metrics_target_only": metrics_target_only,
        "metrics_exact_period": metrics_exact,
        "facts": facts,
        "judge": judge,                       # 规则 Rubric（确定性）
        "hy3_judge": hy3_judge_result,        # Hy3 语义评审（可选）
        "judge_agreement": judge_agreement,   # 两条路径一致性，非准确性
        "d2": d2,
        "d6_hits": sev_hits,
        "d6_total": sev_total,
        "d6_severity_rate": (sev_hits / sev_total) if sev_total else None,
        "n_cards": len(cards),
        "cards_full": [c.model_dump() for c in cards],  # 留存原文供人工复核(方案 §13)
        "injection": case["injection"].to_dict() if case.get("injection") else None,
    }


# ---------------------------------------------------------------- 聚合
def _ratio(num, den):
    return {"value": (num / den) if den else None, "num": num, "den": den}


def _mean(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return (sum(vals) / len(vals)) if vals else None


def _agg_agreement(items: List[Dict]) -> Dict:
    """跨样本汇总两条评审路径的一致性。D7 按配对数加权，D8 直接累加 2×2 计数。"""
    d7s = [i["d7"] for i in items if i.get("d7")]
    n7 = sum(d["n_pairs"] for d in d7s)
    d8s = [i["d8"] for i in items if i.get("d8")]
    keys = ("both_violation", "rule_only", "hy3_only", "neither")
    cells = {k: sum(d[k] for d in d8s) for k in keys}
    n8 = sum(cells.values())
    return {
        "note": "一致性指标，非准确性；D7/D8 真值需人工标注",
        "d7": {
            "n_pairs": n7,
            # 按各样本配对数加权，避免只有 1 张卡的样本与 5 张卡的样本等权
            "mean_abs_diff": (sum(d["mean_abs_diff"] * d["n_pairs"] for d in d7s) / n7)
            if n7 else None,
            "mean_signed_diff": (sum(d["mean_signed_diff"] * d["n_pairs"] for d in d7s) / n7)
            if n7 else None,
            "exact_agreement_rate": (
                sum(d["exact_agreement_rate"] * d["n_pairs"] for d in d7s) / n7)
            if n7 else None,
        } if d7s else None,
        "d8": {
            **cells, "n_pairs": n8,
            "agreement_rate": ((cells["both_violation"] + cells["neither"]) / n8)
            if n8 else None,
        } if d8s else None,
    }


def aggregate(results: List[Dict]) -> Dict:
    """micro 聚合全部维度；仅 case_scored=True 的样本进入集合级主指标。"""
    ok = [r for r in results if "metrics" in r]
    scored = [r for r in ok if r.get("case_scored")]

    agg = summarize([r["metrics"] for r in scored], n_cases_total=len(ok))
    agg["target_only"] = summarize([r["metrics_target_only"] for r in scored],
                                   n_cases_total=len(ok))
    agg["exact_period"] = summarize([r["metrics_exact_period"] for r in scored],
                                    n_cases_total=len(ok))
    agg["n_errors"] = sum(1 for r in results if "error" in r)
    agg["excluded_cases"] = [
        {"name": r["meta"].get("name"), "kind": r["meta"].get("kind"),
         "reasons": r["ground_truth"]["flags"].get("invalid_reasons")
         or r["ground_truth"]["notes"]}
        for r in ok if not r.get("case_scored")
    ]

    # D1 / D3：按 fact 条数 micro 聚合（全部样本，不受 case_scored 限制）
    d1_h = sum(r["facts"]["d1_hits"] for r in ok)
    d1_t = sum(r["facts"]["d1_total"] for r in ok)
    d3_h = sum(r["facts"]["d3_strict_hits"] for r in ok)
    d3_t = sum(r["facts"]["d3_strict_total"] for r in ok)
    agg["D1_value_accuracy"] = _ratio(d1_h, d1_t)
    agg["D3_strict_traceability"] = _ratio(d3_h, d3_t)
    agg["D3_resolution"] = {
        "by_source_record_id": sum(r["facts"]["resolved_by_record_id"] for r in ok),
        "by_fallback": sum(r["facts"]["resolved_by_fallback"] for r in ok),
        "unresolved": sum(r["facts"]["unresolved"] for r in ok),
    }
    agg["D3_field_rates"] = {
        f: _ratio(sum(r["facts"]["d3_field_hits"][f] for r in ok), d3_t)
        for f in (ok[0]["facts"]["d3_field_hits"] if ok else {})
    }

    # D2：按卡片 micro 聚合
    d2_h = sum(r["d2"]["d2_hits"] for r in ok)
    d2_t = sum(r["d2"]["d2_total"] for r in ok)
    agg["D2_formula_correctness"] = _ratio(d2_h, d2_t)
    agg["D2_not_applicable"] = sum(r["d2"]["d2_not_applicable"] for r in ok)
    agg["D2_scale_adjusted"] = sum(r["d2"]["d2_scale_adjusted"] for r in ok)
    agg["D2_step_rates"] = {}
    if ok:
        for step in ok[0]["d2"]["d2_step_hits"]:
            agg["D2_step_rates"][step] = _ratio(
                sum(r["d2"]["d2_step_hits"][step] for r in ok),
                sum(r["d2"]["d2_step_total"][step] for r in ok))

    # D6
    agg["D6_severity_agreement"] = _ratio(sum(r["d6_hits"] for r in ok),
                                          sum(r["d6_total"] for r in ok))

    # D7 / D8：规则 Rubric（非 Hy3 Judge），按卡片 micro
    n_cards = sum(r["judge"]["n_cards"] for r in ok)
    d7_sum = sum(r["judge"]["d7_sum"] for r in ok)
    d8_v = sum(r["judge"]["d8_violations"] for r in ok)
    agg["D7_rule_rubric_mean"] = {
        "value": (d7_sum / n_cards) if n_cards else None, "num": d7_sum, "den": n_cards,
        "method": "rule_rubric_heuristic",
    }
    agg["D8_compliance"] = {
        "value": (1 - d8_v / n_cards) if n_cards else None,
        "violations": d8_v, "den": n_cards, "method": "rule_rubric_heuristic",
    }

    # D7/D8：Hy3-as-Judge 语义评审（若本轮启用），与规则路径并列而非替代
    hj = [r["hy3_judge"] for r in ok if r.get("hy3_judge")]
    if hj:
        h_scored = sum(x["n_scored"] for x in hj)
        h_d7_sum = sum(x["d7_sum"] for x in hj)
        h_judged = sum(x["n_cards"] - x["n_errors"] for x in hj)
        h_viol = sum(x["d8_violations"] for x in hj)
        agg["D7_hy3_judge_mean"] = {
            "value": (h_d7_sum / h_scored) if h_scored else None,
            "num": h_d7_sum, "den": h_scored, "method": "hy3_as_judge_semantic",
            "subdim_means": {
                k: _mean([x["d7_subdim_means"][k] for x in hj
                          if x["d7_subdim_means"].get(k) is not None])
                for k in (hj[0]["d7_subdim_means"] if hj else {})
            },
            "n_errors": sum(x["n_errors"] for x in hj),
            "n_unverifiable_deductions": sum(x["n_unverifiable_deductions"] for x in hj),
        }
        agg["D8_compliance_hy3_judge"] = {
            "value": (1 - h_viol / h_judged) if h_judged else None,
            "violations": h_viol, "den": h_judged, "method": "hy3_as_judge_semantic",
            "flag_counts": {
                k: sum(x["flag_counts"].get(k, 0) for x in hj)
                for k in (hj[0]["flag_counts"] if hj else {})
            },
        }
        agg["judge_agreement"] = _agg_agreement(
            [r["judge_agreement"] for r in ok if r.get("judge_agreement")])

    agg["n_cards_total"] = n_cards
    agg["n_other_cards"] = sum(r["n_other_cards"] for r in ok)
    injected = [r for r in ok if r["meta"]["kind"] == KIND_INJECTED]
    agg["injection_validity"] = {
        "n_injected": len(injected),
        "n_invalid": sum(1 for r in injected if not r["case_scored"]),
        "n_identity_violation": sum(
            1 for r in injected if not r["ground_truth"]["flags"].get("identity_ok", True)),
        "n_target_not_triggered": sum(
            1 for r in injected if not r["ground_truth"]["flags"].get("target_triggered", True)),
        "n_period_mismatch": sum(
            1 for r in injected
            if r["ground_truth"]["flags"].get("periods_match_injection") is False),
    }
    agg["layer_counts"] = {}
    for r in ok:
        for k, v in r["ground_truth"]["layer_counts"].items():
            agg["layer_counts"][k] = agg["layer_counts"].get(k, 0) + v
    agg["contaminated_negatives"] = sum(
        1 for r in ok if r["ground_truth"]["flags"].get("contaminated"))
    return agg


# ---------------------------------------------------------------- 主流程
def run(offline: bool, limit: Optional[int] = None,
        period_mode: str = PERIOD_MATCH_OVERLAP, write: bool = True,
        tag: str = "", hy3_judge: bool = False) -> Dict:
    # 离线模式禁用 Hy3 Judge：它必须联网，否则会破坏 --offline 的零依赖承诺
    if offline and hy3_judge:
        raise ValueError("--hy3-judge 需要联网，不能与 --offline 同时使用")
    cases = build_dataset()
    if limit:
        cases = cases[:limit]
    results: List[Dict] = []
    if offline:
        for case in cases:
            gt = build_ground_truth(case["company"], case["kind"],
                                    meta=case.get("injection"),
                                    base_company=case.get("base"))
            cards = _perfect_cards(gt["gold"], case["company"])
            results.append(evaluate_case(case, cards, period_mode=period_mode))
    else:
        with ThreadPoolExecutor(max_workers=5) as ex:
            futs = {ex.submit(_scan_one, c): c for c in cases}
            for f in as_completed(futs):
                case = futs[f]
                try:
                    out = f.result()
                    results.append(evaluate_case(case, out.cards, period_mode=period_mode,
                                                 hy3_judge=hy3_judge))
                except Exception as e:  # noqa: BLE001
                    results.append({"meta": case["meta"], "error": str(e)})
    agg = aggregate(results)
    agg["run_config"] = {
        "offline": offline, "limit": limit, "period_mode": period_mode,
        "temperature": 0, "n_cases": len(cases), "tag": tag,
        "hy3_judge": hy3_judge,
    }
    if write:
        _write_results(results, agg, tag=tag)
    return {"cases": results, "aggregate": agg}


def run_multi(offline: bool, limit: Optional[int], period_mode: str, runs: int,
              hy3_judge: bool = False) -> Dict:
    """跑多次并给出 均值 / 最小 / 最大（方案 §6.4：在线模型即便 temperature=0 仍有波动）。"""
    all_runs = []
    for i in range(runs):
        out = run(offline=offline, limit=limit, period_mode=period_mode,
                  write=True, tag=f"run{i+1}", hy3_judge=hy3_judge)
        all_runs.append(out["aggregate"])

    def _stat(path: List[str]):
        vals = []
        for a in all_runs:
            node = a
            for p in path:
                node = (node or {}).get(p) if isinstance(node, dict) else None
            if isinstance(node, (int, float)):
                vals.append(node)
        if not vals:
            return None
        return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals),
                "runs": len(vals), "values": vals}

    stability = {
        "MRhigh": _stat(["MRhigh", "value"]),
        "P": _stat(["P", "value"]),
        "R": _stat(["R", "value"]),
        "Rw": _stat(["Rw", "value"]),
        "D1": _stat(["D1_value_accuracy", "value"]),
        "D2": _stat(["D2_formula_correctness", "value"]),
        "D3": _stat(["D3_strict_traceability", "value"]),
        "D6": _stat(["D6_severity_agreement", "value"]),
        "D7": _stat(["D7_rule_rubric_mean", "value"]),
        "D8": _stat(["D8_compliance", "value"]),
        "D7_hy3": _stat(["D7_hy3_judge_mean", "value"]),
        "D8_hy3": _stat(["D8_compliance_hy3_judge", "value"]),
    }
    os.makedirs(TABLES, exist_ok=True)
    with open(os.path.join(TABLES, "stability.json"), "w", encoding="utf-8") as f:
        json.dump({"runs": runs, "period_mode": period_mode, "stability": stability},
                  f, ensure_ascii=False, indent=2)
    return {"runs": all_runs, "stability": stability}


def _write_results(results: List[Dict], agg: Dict, tag: str = "") -> None:
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(TABLES, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    with open(os.path.join(RAW, f"cases{suffix}.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(TABLES, f"report{suffix}.json"), "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, indent=2, default=str)


def _pct(node) -> str:
    if not isinstance(node, dict) or node.get("value") is None:
        return "N/A"
    num, den = node.get("num"), node.get("den")
    tail = f" ({num}/{den})" if isinstance(num, (int, float)) and den else ""
    return f"{node['value']*100:.1f}%{tail}"


def _print_report(agg: Dict) -> None:
    print("\n==== 聚合报告（micro 口径，方案 §6.4 报告分子/分母）====")
    print(f"样本数: {agg['n_cases']}  计入主指标: {agg['n_cases_scored']}  "
          f"剔除: {agg['n_cases_excluded']}  调用失败: {agg.get('n_errors', 0)}")
    print(f"卡片总数: {agg.get('n_cards_total')}  其中 other(不计主指标): {agg.get('n_other_cards')}")
    print(f"期间匹配模式: {agg['run_config']['period_mode']}")
    print("-" * 60)
    print(f"[主指标] 严重异常漏报率 MRhigh : {_pct(agg['MRhigh'])}")
    mac = agg.get("MRhigh_macro") or {}
    mv = mac.get("value")
    print(f"         MRhigh(macro 参考)   : "
          f"{('%.1f%%' % (mv*100)) if mv is not None else 'N/A'}"
          f"  (逐样本平均, n={mac.get('n_cases')})")
    print(f"精确率 P     : {_pct(agg['P'])}")
    print(f"召回率 R     : {_pct(agg['R'])}")
    print(f"加权召回 Rw  : {_pct(agg['Rw'])}")
    print(f"TP/FP/FN     : {agg['TP']}/{agg['FP']}/{agg['FN']}   金标准条数: {agg['n_gold']}")
    print(f"期间精确匹配 : {agg['period_exact_tp']}  期间偏移匹配: {agg['period_shifted_tp']}")
    print(f"漏报失败模式 : {agg['fn_modes']}")
    print("-" * 60)
    print(f"D1 数值准确    : {_pct(agg['D1_value_accuracy'])}")
    print(f"D2 公式正确    : {_pct(agg['D2_formula_correctness'])}"
          f"  不适用 {agg['D2_not_applicable']} 条, 单位归一 {agg['D2_scale_adjusted']} 条")
    print(f"D3 严格可追溯  : {_pct(agg['D3_strict_traceability'])}  定位方式 {agg['D3_resolution']}")
    print(f"D6 严重度一致  : {_pct(agg['D6_severity_agreement'])}")
    d7 = agg["D7_rule_rubric_mean"]
    print(f"D7 规则Rubric  : {('%.2f' % d7['value']) if d7['value'] is not None else 'N/A'} / 5"
          f"  (启发式规则，非 Hy3-as-Judge)")
    print(f"D8 合规率      : {_pct(agg['D8_compliance'])}")
    if agg.get("D7_hy3_judge_mean"):
        h7 = agg["D7_hy3_judge_mean"]
        print("-" * 60)
        print(f"D7 Hy3语义评审 : "
              f"{('%.2f' % h7['value']) if h7['value'] is not None else 'N/A'} / 5"
              f"  (评审 {h7['den']} 张, 失败 {h7['n_errors']} 张)")
        for k, v in (h7.get("subdim_means") or {}).items():
            print(f"    · {k:26s}: {('%.2f' % v) if v is not None else 'N/A'}")
        print(f"D8 Hy3语义合规 : {_pct(agg['D8_compliance_hy3_judge'])}"
              f"  违规分布 {agg['D8_compliance_hy3_judge']['flag_counts']}")
        ja = agg.get("judge_agreement") or {}
        if ja.get("d7"):
            d = ja["d7"]
            print(f"两路一致性 D7  : 平均绝对差 {d['mean_abs_diff']:.2f}, "
                  f"有向差(Hy3-规则) {d['mean_signed_diff']:+.2f}, "
                  f"±0.5内一致率 {d['exact_agreement_rate']*100:.1f}% (n={d['n_pairs']})")
        if ja.get("d8"):
            d = ja["d8"]
            print(f"两路一致性 D8  : 一致率 {(d['agreement_rate'] or 0)*100:.1f}%  "
                  f"仅规则判违规 {d['rule_only']}, 仅Hy3判违规 {d['hy3_only']} "
                  f"(n={d['n_pairs']})")
        print("  ⚠️ 上述为两条路径的一致性，非准确性；D7/D8 真值需人工标注")
    print("-" * 60)
    print(f"金标准分层计数 : {agg['layer_counts']}")
    print(f"注入有效性     : {agg['injection_validity']['n_injected']} 个注入样本, "
          f"{agg['injection_validity']['n_invalid']} 个无效被剔除")
    print(f"阴性对照污染数 : {agg['contaminated_negatives']}")
    if agg["excluded_cases"]:
        print("被剔除样本：")
        for e in agg["excluded_cases"]:
            print(f"  - {e['name']} [{e['kind']}] {e['reasons']}")
    to = agg.get("target_only", {})
    ep = agg.get("exact_period", {})
    print("-" * 60)
    print(f"[敏感性] 仅注入目标口径 MRhigh : {_pct(to.get('MRhigh', {}))}, R={_pct(to.get('R', {}))}")
    print(f"[敏感性] 期间严格匹配   MRhigh : {_pct(ep.get('MRhigh', {}))}, R={_pct(ep.get('R', {}))}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="离线自检，不调 Hy3、不导入 openai")
    ap.add_argument("--limit", type=int, default=None, help="仅跑前 N 个样本")
    ap.add_argument("--runs", type=int, default=1, help="重复跑 N 次并报告均值/区间")
    ap.add_argument("--period-mode", default=PERIOD_MATCH_OVERLAP,
                    choices=["overlap", "exact"], help="期间匹配口径")
    ap.add_argument("--hy3-judge", action="store_true",
                    help="额外跑 Hy3-as-Judge 语义评审（每张卡片 1 次调用，需联网）")
    args = ap.parse_args()
    if args.runs > 1:
        out = run_multi(offline=args.offline, limit=args.limit,
                        period_mode=args.period_mode, runs=args.runs,
                        hy3_judge=args.hy3_judge)
        print("\n==== 多次运行稳定性（均值 / 区间）====")
        for k, v in out["stability"].items():
            if v is None:
                print(f"{k:6s}: N/A")
            else:
                print(f"{k:6s}: mean={v['mean']:.4f}  min={v['min']:.4f}  "
                      f"max={v['max']:.4f}  runs={v['runs']}")
        return
    out = run(offline=args.offline, limit=args.limit, period_mode=args.period_mode,
              hy3_judge=args.hy3_judge)
    _print_report(out["aggregate"])


if __name__ == "__main__":
    main()
