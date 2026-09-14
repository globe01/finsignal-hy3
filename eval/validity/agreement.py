"""人工一致性（inter-annotator agreement）计算（对应 docs/annotation_guide.md）。

用途：在人工标注数据上计算 **一致性（agreement）**，而非准确性（accuracy）。
- 类别一致性：Cohen's kappa（两两）/ Fleiss kappa（≥3 名标注者），针对 `signal_valid`
- 序值相关性：Spearman 秩相关，针对 `d7_score` / `severity_label`
- 重复评估波动：同一 (case_id, card_id, annotator) 跨 `round` 的 `d7_score` 标准差

设计铁律：**绝不伪造标注结果**。
- 标注数据不足（如少于 2 名标注者且无重复轮次）时，只输出 `PENDING`，不编造任何数值。
- 退出码恒为 0（即使 PENDING），以便纳入 CI 而不误报失败；PENDING 状态由 JSON 的
  `status` 字段显式表达，供报告引用。

依赖：pandas / scikit-learn / scipy / numpy（均已列入 requirements.txt）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import cohen_kappa_score

# 注：scikit-learn ≥1.6 移除了 fleiss_kappa，这里自行实现（支持每窗口标注者人数不一致）。
def _fleiss_kappa(matrix: np.ndarray) -> float:
    """matrix: (n_items × n_categories) 计数矩阵，每行 = 该窗口各标注者的类别投票。
    返回 Fleiss κ；行和（=各窗口标注者数）可不一致（按广义公式处理）。"""
    matrix = np.asarray(matrix, dtype=float)
    if matrix.size == 0 or matrix.shape[1] < 2:
        return float("nan")
    n_items, _ = matrix.shape
    row_sums = matrix.sum(axis=1)
    if (row_sums < 2).any():
        return float("nan")
    total = row_sums.sum()
    # 每类全局占比 p_j
    col_sums = matrix.sum(axis=0)
    p_j = col_sums / total
    # 每窗口一致性 P_i
    P_i = np.zeros(n_items)
    for i in range(n_items):
        m_i = row_sums[i]
        P_i[i] = (np.sum(matrix[i] ** 2) - m_i) / (m_i * (m_i - 1))
    P_bar = P_i.mean()
    Pe = float(np.sum(p_j ** 2))
    if Pe >= 1.0:
        return 1.0 if P_bar >= 1.0 else 0.0
    return float((P_bar - Pe) / (1.0 - Pe))

_SIGNAL_MAP = {"yes": 2, "y": 2, "uncertain": 1, "u": 1, "maybe": 1,
               "no": 0, "n": 0, "nan": np.nan, "": np.nan, "na": np.nan}
_SEV_MAP = {"high": 3, "medium": 2, "low": 1,
            "na": np.nan, "": np.nan, "none": np.nan, "nan": np.nan}


def _map_signal(v) -> float:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    return _SIGNAL_MAP.get(str(v).strip().lower(), np.nan)


def _map_sev(v) -> float:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return np.nan
    return _SEV_MAP.get(str(v).strip().lower(), np.nan)


def _to_float(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return np.nan
        return float(v)
    except (ValueError, TypeError):
        return np.nan


def _json_safe(value):
    """Convert NaN/Infinity to JSON null for standards-compliant CLI output."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def compute_agreement(df: pd.DataFrame) -> Dict:
    """输入标注 DataFrame，输出一致性结果字典（status: ok / pending）。"""
    needed = ["case_id", "card_id", "annotator", "round", "signal_valid",
              "severity_label", "d7_score"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        return {"status": "pending",
                "reason": f"标注模板缺列：{missing}（请按 eval/validity/annotations_template.csv）",
                "metrics": {}}

    df = df.copy()
    df["signal_valid_code"] = df["signal_valid"].map(_map_signal)
    df["sev_code"] = df["severity_label"].map(_map_sev)
    df["d7_num"] = df["d7_score"].map(_to_float)
    df["round"] = pd.to_numeric(df["round"], errors="coerce").fillna(1).astype(int)

    annotators = [a for a in df["annotator"].dropna().unique().tolist() if str(a).strip()]
    n_annot = len(annotators)
    n_rows = len(df)
    out: Dict = {
        "status": "pending",
        "n_rows": int(n_rows),
        "n_annotators": n_annot,
        "annotators": annotators,
        "metrics": {},
    }
    if n_rows == 0:
        out["reason"] = "标注文件为空（仅表头），请先按 annotation_guide.md 完成标注"
        return out

    m: Dict = {}

    # ---- 1) 类别一致性：signal_valid ----
    valid = df.dropna(subset=["signal_valid_code"])
    if n_annot >= 2:
        pairwise: List[float] = []
        detail = []
        anno_pairs = [(a, b) for i, a in enumerate(annotators) for b in annotators[i + 1:]]
        for a, b in anno_pairs:
            pa = valid[valid["annotator"] == a]
            pb = valid[valid["annotator"] == b]
            merged = pd.merge(
                pa[["case_id", "card_id", "signal_valid_code"]],
                pb[["case_id", "card_id", "signal_valid_code"]],
                on=["case_id", "card_id"], suffixes=("_a", "_b"))
            if len(merged) >= 2:
                a_values = merged["signal_valid_code_a"].nunique(dropna=True)
                b_values = merged["signal_valid_code_b"].nunique(dropna=True)
                if a_values < 2 or b_values < 2:
                    detail.append({
                        "pair": f"{a}-{b}",
                        "n_shared": int(len(merged)),
                        "kappa": None,
                        "reason": "统计不可定义：至少一名标注者只有一个类别",
                    })
                else:
                    k = cohen_kappa_score(merged["signal_valid_code_a"], merged["signal_valid_code_b"])
                    pairwise.append(float(k))
                    detail.append({"pair": f"{a}-{b}", "n_shared": int(len(merged)), "kappa": float(k)})
        if pairwise:
            m["cohen_kappa_pairwise"] = {
                "mean": float(np.mean(pairwise)), "min": float(np.min(pairwise)),
                "max": float(np.max(pairwise)), "pairs": detail}
        elif detail:
            m["cohen_kappa_pairwise"] = {
                "mean": None, "min": None, "max": None, "pairs": detail}
        if n_annot >= 3:
            # Fleiss kappa：构建 (n_items × n_categories) 计数矩阵
            items = valid.groupby(["case_id", "card_id"])
            cats = sorted(valid["signal_valid_code"].dropna().unique())
            n_cat = len(cats)
            matrix = []
            for _, grp in items:
                counts = [0] * n_cat
                for code in grp["signal_valid_code"].dropna():
                    counts[cats.index(code)] += 1
                # 仅当该窗口有 ≥2 名标注者参与才计入（Fleiss 要求每项的评分人数）
                if grp["annotator"].nunique() >= 2:
                    matrix.append(counts)
            if matrix and n_cat >= 2:
                try:
                    m["fleiss_kappa"] = float(_fleiss_kappa(np.array(matrix)))
                except Exception as e:  # noqa: BLE001
                    m["fleiss_kappa"] = f"n/a: {e}"
    else:
        m["cohen_kappa_pairwise"] = "PENDING：需 ≥2 名标注者"

    # ---- 2) 严重度/质量相关性：Spearman on d7_score ----
    d7 = df.dropna(subset=["d7_num"])
    if n_annot >= 2:
        sp_detail = []
        for a, b in [(x, y) for i, x in enumerate(annotators) for y in annotators[i + 1:]]:
            pa = d7[d7["annotator"] == a]
            pb = d7[d7["annotator"] == b]
            merged = pd.merge(pa[["case_id", "card_id", "d7_num"]],
                              pb[["case_id", "card_id", "d7_num"]],
                              on=["case_id", "card_id"], suffixes=("_a", "_b"))
            if len(merged) >= 3:
                a_values = merged["d7_num_a"].nunique(dropna=True)
                b_values = merged["d7_num_b"].nunique(dropna=True)
                if a_values < 2 or b_values < 2:
                    sp_detail.append({
                        "pair": f"{a}-{b}",
                        "n": int(len(merged)),
                        "spearman_rho": None,
                        "p": None,
                        "reason": "统计不可定义：至少一名标注者的 d7_score 为常量",
                    })
                else:
                    rho, p = spearmanr(merged["d7_num_a"], merged["d7_num_b"])
                    sp_detail.append({"pair": f"{a}-{b}", "n": int(len(merged)),
                                      "spearman_rho": float(rho), "p": float(p)})
        if sp_detail:
            m["spearman_d7_pairwise"] = sp_detail
        else:
            m["spearman_d7_pairwise"] = "PENDING：共享且有 d7_score 的卡片不足"
    else:
        m["spearman_d7_pairwise"] = "PENDING：需 ≥2 名标注者"

    # ---- 3) 重复评估波动：同一 (case,card,annotator) 跨 round 的 d7_score 标准差 ----
    n_rounds = d7.groupby(["case_id", "card_id", "annotator"]).size()
    repeated = n_rounds[n_rounds >= 2]
    if len(repeated) > 0:
        rep_std = d7[d7.groupby(["case_id", "card_id", "annotator"]).transform("size") >= 2] \
            .groupby(["case_id", "card_id", "annotator"])["d7_num"].std(ddof=0).dropna()
        m["repeat_eval_fluctuation"] = {
            "n_repeated_cells": int(len(rep_std)),
            "mean_std": float(rep_std.mean()) if len(rep_std) else None,
            "max_std": float(rep_std.max()) if len(rep_std) else None,
        }
    else:
        m["repeat_eval_fluctuation"] = ("PENDING：无重复轮次（round≥2）；"
                                        "无法估计标注者内波动")

    # ---- 4) 单标注者 test-retest（仅有 1 名标注者但有多轮）----
    if n_annot == 1 and len(repeated) > 0:
        a = annotators[0]
        sub = valid[valid["annotator"] == a]
        # 跨轮次的 signal_valid 一致性（同 case/card 两张标注）
        grp = sub.groupby(["case_id", "card_id"])["signal_valid_code"].nunique()
        consistent = (grp == 1).sum()
        m["single_annotator_test_retest"] = {
            "n_cells_with_two_rounds": int((grp >= 2).sum()),
            "signal_valid_consistent_cells": int(consistent),
        }

    out["metrics"] = m

    # 判定 status：至少有 pairwise kappa 或 spearman 或 fluctuation 才算 ok
    has_real = any(
        (isinstance(v, dict) and v) or (isinstance(v, list) and v)
        for k, v in m.items() if k in (
            "cohen_kappa_pairwise", "spearman_d7_pairwise", "repeat_eval_fluctuation")
    )
    if n_annot >= 2 and has_real:
        out["status"] = "ok"
    elif n_annot == 1 and len(repeated) > 0:
        out["status"] = "ok_test_retest"
    else:
        out["status"] = "pending"
        out["reason"] = ("标注数据不足以计算一致性：需 ≥2 名标注者（Cohen's/Fleiss/Spearman）"
                         "或同标注者 ≥2 轮（波动）。当前仅 1 名标注者且无重复轮次。"
                         "请在 docs/annotation_guide.md 完成后回填。")
    return out


def _template_rows(cases: List[Dict]) -> List[Dict]:
    """从在线评测 cases 抽取待标注卡片清单（仅列卡片，不填标注）。

    设计（提交前收口修复）：
    - ``case_id`` 用匿名顺序号 ``case_000`` 替代 ``meta.name``，避免泄漏样本身份 / 注入类型。
    - ``card_id`` 用每样本内稳定序号 ``card_000``、``card_001``… 取代 ``signal_type``，
      因为同一 case 内可能出现重复 ``signal_type``；保证每个 ``(case_id, card_id)`` 全局唯一。
    - ``severity_label`` 留空：标注者**独立**判定严重度，不泄露模型自报严重度。
    不写入任何金标准 / 注入 / 评估字段（标注独立性由 docs/annotation_guide.md 约束）。
    """
    rows = []
    for ci, c in enumerate(cases):
        case_id = f"case_{ci:03d}"
        for ki, card in enumerate(c.get("cards_full", []) or []):
            rows.append({
                "case_id": case_id,
                "card_id": f"card_{ki:03d}",
                "signal_type": card.get("signal_type", ""),
                "period": "|".join(card.get("periods", []) or []),
                "annotator": "", "round": 1, "signal_valid": "",
                "severity_label": "",
                "d7_score": "", "d8_violation": "", "note": "",
            })
    return rows


def _emit_template(cases_json: str) -> None:
    """从在线评测 cases json 抽取待标注卡片清单（仅列卡片，不填标注）并打印 CSV。"""
    with open(cases_json, encoding="utf-8") as f:
        cases = json.load(f)
    out = pd.DataFrame(_template_rows(cases))
    sys.stdout.write(out.to_csv(index=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="人工一致性计算（Cohen's/Fleiss kappa + Spearman + 波动）")
    ap.add_argument("--csv", default="eval/validity/annotations.csv",
                    help="标注 CSV 路径（按 annotations_template.csv 列）")
    ap.add_argument("--emit-template", default=None,
                    help="从在线评测 cases json 抽取待标注卡片清单并打印 CSV")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    if args.emit_template:
        _emit_template(args.emit_template)
        return

    try:
        df = pd.read_csv(args.csv)
    except FileNotFoundError:
        result = {"status": "pending", "reason": f"标注文件不存在：{args.csv}",
                  "metrics": {}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    result = compute_agreement(df)
    if args.json:
        print(json.dumps(_json_safe(result), ensure_ascii=False, indent=2, allow_nan=False))
        return

    # 人类可读摘要
    print(f"标注一致性状态：{result['status'].upper()}")
    print(f"  标注行数：{result['n_rows']}  标注者：{result['n_annotators']} "
          f"({', '.join(result['annotators']) or '—'})")
    if result["status"] == "pending":
        print(f"  ⏳ {result.get('reason', '标注数据不足')}")
    m = result["metrics"]
    if isinstance(m.get("cohen_kappa_pairwise"), dict):
        k = m["cohen_kappa_pairwise"]
        print(f"  Cohen's κ（两两均值）：{k['mean']:.3f}  "
              f"（区间 {k['min']:.3f}–{k['max']:.3f}，{len(k['pairs'])} 对）")
    if isinstance(m.get("fleiss_kappa"), (int, float)):
        print(f"  Fleiss κ：{m['fleiss_kappa']:.3f}")
    if isinstance(m.get("spearman_d7_pairwise"), list):
        for s in m["spearman_d7_pairwise"]:
            print(f"  Spearman ρ（{s['pair']}，d7）：{s['spearman_rho']:.3f} (p={s['p']:.3g}, n={s['n']})")
    if isinstance(m.get("repeat_eval_fluctuation"), dict):
        f = m["repeat_eval_fluctuation"]
        print(f"  重复评估波动：{f['n_repeated_cells']} 格，"
              f"均值 std={f['mean_std']}, 最大 std={f['max_std']}")


if __name__ == "__main__":
    main()
