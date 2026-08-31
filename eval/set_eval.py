"""D4 覆盖完整性 / D5 误报控制 / D6 重要性排序，以及核心指标（对应方案 §6.1、§6.3）。

主指标：严重异常漏报率 MRhigh。
支撑：严重度加权召回 Rw、精确率 P、原始数值准确率(D1)、公式复算通过率(D2)、过度推断率。
"""
from __future__ import annotations

from typing import Dict, List, Tuple


def _key(signal_type: str, periods: List[str]) -> Tuple[str, frozenset]:
    return (signal_type, frozenset(periods))


def match(gold: List[dict], model: List[dict]) -> Dict:
    """按 (signal_type, 期间集合) 匹配，区分 TP / FP / FN。"""
    gold_keys = {_key(g["signal_type"], g["periods"]): g for g in gold}
    model_keys = {_key(m["signal_type"], m["periods"]): m for m in model}

    tp_keys = set(gold_keys) & set(model_keys)
    fp = [model_keys[k] for k in model_keys if k not in gold_keys]
    fn = [gold_keys[k] for k in gold_keys if k not in model_keys]
    tp = [gold_keys[k] for k in tp_keys]
    return {"tp": tp, "fp": fp, "fn": fn,
            "tp_keys": tp_keys, "fp_keys": set(model_keys) - set(gold_keys),
            "fn_keys": set(gold_keys) - set(model_keys)}


def compute_metrics(gold: List[dict], model: List[dict],
                    severity_weight=lambda s: {"high": 3, "medium": 2, "low": 1}.get(s, 1)
                    ) -> Dict:
    """计算 P/R/F1/Rw/MRhigh。model 仅含枚举内信号。"""
    m = match(gold, model)
    tp, fp, fn = m["tp"], m["fp"], m["fn"]
    tp_n, fp_n, fn_n = len(tp), len(fp), len(fn)

    P = tp_n / (tp_n + fp_n) if (tp_n + fp_n) else None
    if gold:
        R = tp_n / len(gold)
    else:
        R = None  # 无金标准（阴性样本）不计算召回
    F1 = (2 * P * R / (P + R)) if (P is not None and R is not None and (P + R)) else None

    # 加权召回 Rw
    if gold:
        w_all = sum(severity_weight(g["severity"]) for g in gold)
        w_tp = sum(severity_weight(g["severity"]) for g in tp)
        Rw = w_tp / w_all if w_all else 0.0
    else:
        Rw = None

    # 严重异常漏报率 MRhigh
    g_high = [g for g in gold if g["severity"] == "high"]
    if g_high:
        high_keys = {_key(g["signal_type"], g["periods"]) for g in g_high}
        missed = high_keys - m["tp_keys"]
        MRhigh = len(missed) / len(high_keys)
    else:
        MRhigh = None  # 分组内无 high → N/A

    return {
        "TP": tp_n, "FP": fp_n, "FN": fn_n,
        "P": P, "R": R, "F1": F1, "Rw": Rw, "MRhigh": MRhigh,
        "fp_list": [f["signal_type"] for f in fp],
        "fn_list": [f["signal_type"] for f in fn],
    }


def summarize(case_results: List[Dict]) -> Dict:
    """聚合多个样本的 MRhigh 等（方案 §6.4：报告分子分母）。"""
    mr_num = mr_den = 0
    tp = fp = fn = 0
    g_w_all = g_w_tp = 0
    w = {"high": 3, "medium": 2, "low": 1}
    for r in case_results:
        # 仅统计有 high 金标准的样本贡献 MRhigh 分子分母
        # 这里 case_results 已是 per-case compute_metrics 输出，需样本级 gold 才能精确聚合；
        # 简化：直接平均各样本 MRhigh（N/A 跳过）。
        pass
    # 由 run_eval 在样本级聚合更稳妥，这里仅做均值汇总
    mrs = [r["MRhigh"] for r in case_results if r["MRhigh"] is not None]
    mean_mr = sum(mrs) / len(mrs) if mrs else None
    ps = [r["P"] for r in case_results if r["P"] is not None]
    rs = [r["R"] for r in case_results if r["R"] is not None]
    rws = [r["Rw"] for r in case_results if r["Rw"] is not None]
    return {
        "n_cases": len(case_results),
        "mean_MRhigh": mean_mr,
        "mean_P": (sum(ps) / len(ps)) if ps else None,
        "mean_R": (sum(rs) / len(rs)) if rs else None,
        "mean_Rw": (sum(rws) / len(rws)) if rws else None,
    }
