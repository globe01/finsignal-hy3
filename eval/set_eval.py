"""集合级指标：D4 覆盖完整性 / D5 误报控制 / D6 重要性排序（对应方案 §6.1、§6.3、§6.4）。

主指标：严重异常漏报率 MRhigh = |G_high \\ O| / |G_high|。
支撑：严重度加权召回 Rw、精确率 P、召回 R、F1。

两处关键方法学修正：

1. **聚合必须用 micro（样本合并）而非 macro（逐样本平均）**。
   逐样本平均把「只有 1 个 high 的样本」与「有 4 个 high 的样本」等权，
   小样本的单次漏报会被放大成 100% 并拉高整体均值。主指标改为
   micro：Σ(各样本 high 漏报数) / Σ(各样本 high 总数)；macro 仅作为附加参考同时给出。
   所有比率一律输出分子与分母（方案 §6.4），分母为 0 时返回 None(N/A) 而不是 0。

2. **期间匹配默认用重叠而非严格集合相等**。
   模型把多年信号标成单年、或把窗口标宽（金标准 2023，模型 2022–2023），
   在严格 frozenset 相等下会同时计入 FP 与 FN，造成双重惩罚与虚高漏报率。
   默认按「同 signal_type + 期间有交集」做一对一贪心匹配，并单独统计
   period_shifted_tp（期间归属偏差）；exact 模式作为敏感性分析保留。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

PERIOD_MATCH_OVERLAP = "overlap"
PERIOD_MATCH_EXACT = "exact"

_DEFAULT_WEIGHT: Callable[[str], int] = lambda s: {"high": 3, "medium": 2, "low": 1}.get(s, 1)


def _periods(item: dict) -> frozenset:
    return frozenset(str(p) for p in item.get("periods", []))


def _key(signal_type: str, periods: List[str]) -> Tuple[str, frozenset]:
    """严格键（signal_type, 期间集合），保留给 exact 模式与回归测试。"""
    return (signal_type, frozenset(str(p) for p in periods))


def _ratio(num: float, den: float) -> Dict:
    """统一的比率输出：分子、分母、值；分母为 0 → value=None（N/A）。"""
    return {"value": (num / den) if den else None, "num": num, "den": den}


def match(gold: List[dict], model: List[dict],
          period_mode: str = PERIOD_MATCH_OVERLAP) -> Dict:
    """按 signal_type + 期间做一对一匹配，区分 TP / FP / FN。

    overlap 模式：同 signal_type 且期间有交集即可配对（贪心取交集最大者）。
    exact   模式：要求期间集合完全相等。
    """
    if period_mode not in (PERIOD_MATCH_OVERLAP, PERIOD_MATCH_EXACT):
        raise ValueError(f"未知期间匹配模式：{period_mode}")

    unmatched_model = list(range(len(model)))
    pairs: List[Dict] = []
    fn: List[dict] = []

    for g in gold:
        gp = _periods(g)
        best_i, best_overlap = None, 0
        for i in unmatched_model:
            m = model[i]
            if m["signal_type"] != g["signal_type"]:
                continue
            mp = _periods(m)
            if period_mode == PERIOD_MATCH_EXACT:
                if mp == gp:
                    best_i, best_overlap = i, len(gp)
                    break
                continue
            ov = len(gp & mp)
            # 金标准或模型任一未给期间时，退化为仅按 signal_type 配对
            if not gp or not mp:
                ov = max(ov, 1)
            if ov > best_overlap:
                best_i, best_overlap = i, ov
        if best_i is None:
            same_type = [model[i] for i in unmatched_model
                         if model[i]["signal_type"] == g["signal_type"]]
            fn.append({**g, "fn_mode": "period_disjoint" if same_type else "missed_signal_type"})
        else:
            unmatched_model.remove(best_i)
            m = model[best_i]
            pairs.append({
                "gold": g, "model": m,
                "period_exact": _periods(m) == _periods(g),
                "overlap": best_overlap,
            })

    fp = [model[i] for i in unmatched_model]
    return {
        "pairs": pairs,
        "tp": [p["gold"] for p in pairs],
        "fp": fp,
        "fn": fn,
        "period_mode": period_mode,
        "period_exact_tp": sum(1 for p in pairs if p["period_exact"]),
        "period_shifted_tp": sum(1 for p in pairs if not p["period_exact"]),
    }


def compute_metrics(gold: List[dict], model: List[dict],
                    severity_weight: Callable[[str], int] = _DEFAULT_WEIGHT,
                    period_mode: str = PERIOD_MATCH_OVERLAP) -> Dict:
    """计算单样本 P/R/F1/Rw/MRhigh，并输出可 micro 聚合的分子分母。

    model 仅应包含枚举内信号（other 由调用方剔除，方案 §3.4）。
    """
    m = match(gold, model, period_mode=period_mode)
    tp_n, fp_n, fn_n = len(m["tp"]), len(m["fp"]), len(m["fn"])

    P = _ratio(tp_n, tp_n + fp_n)
    R = _ratio(tp_n, len(gold))
    pv, rv = P["value"], R["value"]
    F1 = (2 * pv * rv / (pv + rv)) if (pv is not None and rv is not None and (pv + rv)) else None

    w_all = sum(severity_weight(g["severity"]) for g in gold)
    w_tp = sum(severity_weight(g["severity"]) for g in m["tp"])
    Rw = _ratio(w_tp, w_all)

    # 严重异常漏报率 MRhigh：分子=high 金标准中被漏掉的条数，分母=high 金标准总条数
    g_high = [g for g in gold if g["severity"] == "high"]
    tp_high = [g for g in m["tp"] if g["severity"] == "high"]
    high_total = len(g_high)
    high_missed = high_total - len(tp_high)
    MRhigh = _ratio(high_missed, high_total)  # 无 high → value=None（N/A，不记 0）

    return {
        "TP": tp_n, "FP": fp_n, "FN": fn_n,
        "n_gold": len(gold), "n_model": len(model),
        "P": P["value"], "R": R["value"], "F1": F1,
        "Rw": Rw["value"], "MRhigh": MRhigh["value"],
        "num_den": {"P": P, "R": R, "Rw": Rw, "MRhigh": MRhigh},
        "high_total": high_total, "high_missed": high_missed,
        "w_all": w_all, "w_tp": w_tp,
        "period_mode": period_mode,
        "period_exact_tp": m["period_exact_tp"],
        "period_shifted_tp": m["period_shifted_tp"],
        "fp_list": [f["signal_type"] for f in m["fp"]],
        "fn_list": [f["signal_type"] for f in m["fn"]],
        "fn_modes": [{"signal_type": f["signal_type"], "mode": f["fn_mode"],
                      "periods": list(f.get("periods", []))} for f in m["fn"]],
    }


def summarize(case_metrics: List[Dict], n_cases_total: Optional[int] = None) -> Dict:
    """micro 聚合多样本指标（方案 §6.4：主指标报告分子与分母）。

    参数 case_metrics 只应包含**计入主指标**的样本（case_scored=True）；
    n_cases_total 用于同时披露被剔除的样本数。
    """
    tp = sum(r["TP"] for r in case_metrics)
    fp = sum(r["FP"] for r in case_metrics)
    fn = sum(r["FN"] for r in case_metrics)
    n_gold = sum(r["n_gold"] for r in case_metrics)
    high_total = sum(r["high_total"] for r in case_metrics)
    high_missed = sum(r["high_missed"] for r in case_metrics)
    w_all = sum(r["w_all"] for r in case_metrics)
    w_tp = sum(r["w_tp"] for r in case_metrics)

    P = _ratio(tp, tp + fp)
    R = _ratio(tp, n_gold)
    pv, rv = P["value"], R["value"]
    F1 = (2 * pv * rv / (pv + rv)) if (pv is not None and rv is not None and (pv + rv)) else None
    Rw = _ratio(w_tp, w_all)
    MRhigh = _ratio(high_missed, high_total)

    # macro 仅作附加参考：逐样本平均，跳过 N/A
    def _macro(field: str) -> Dict:
        vals = [r[field] for r in case_metrics if r.get(field) is not None]
        return {"value": (sum(vals) / len(vals)) if vals else None, "n_cases": len(vals)}

    fn_modes: Dict[str, int] = {}
    for r in case_metrics:
        for f in r.get("fn_modes", []):
            fn_modes[f["mode"]] = fn_modes.get(f["mode"], 0) + 1

    n_scored = len(case_metrics)
    total = n_cases_total if n_cases_total is not None else n_scored
    return {
        "n_cases": total,
        "n_cases_scored": n_scored,
        "n_cases_excluded": max(0, total - n_scored),
        "aggregation": "micro",
        "TP": tp, "FP": fp, "FN": fn, "n_gold": n_gold,
        "MRhigh": MRhigh, "P": P, "R": R, "Rw": Rw,
        "F1": {"value": F1, "num": None, "den": None},
        "MRhigh_macro": _macro("MRhigh"),
        "P_macro": _macro("P"), "R_macro": _macro("R"), "Rw_macro": _macro("Rw"),
        "period_exact_tp": sum(r["period_exact_tp"] for r in case_metrics),
        "period_shifted_tp": sum(r["period_shifted_tp"] for r in case_metrics),
        "fn_modes": fn_modes,
    }
