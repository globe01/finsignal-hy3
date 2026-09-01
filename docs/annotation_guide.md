# 人工标注指南（FinSignal-Hy3 人工一致性验证）

> 本文件定义「人工标注」规范，用于为 D1（事实）与 D7（解释与边界质量）提供**真值**，
> 并据此计算标注者间一致性（inter-annotator agreement）。
> 规则 Rubric（`eval/rule_rubric.py`）与 Hy3-as-Judge（`eval/hy3_judge.py`）**都不是真值**，
> 它们只用于和人工标注做一致性对照。

⚠️ **当前状态：人工标注尚未开展（pending）**。本仓库**不伪造任何标注结果**；
`eval/validity/agreement.py` 在标注数据不足（如少于 2 名标注者）时会明确输出 `PENDING`，
绝不编造 Cohen's kappa / Spearman 数值。

---

## 1. 标注对象

标注对象 = 模型产出的**异常卡片**（来自在线评测的 `results/raw/cases_run*.json` 中 `cards_full`，
或从 `app/streamlit_app.py` 单条扫描导出）。每条卡片对应一格标注行。

> 不要在标注前看到金标准（`ground_truth`）——否则标注会被金标准锚定，失去独立性。
> 标注者只看「财务数据原文 + 模型卡片」。

---

## 2. 标注字段（对应 `eval/validity/annotations_template.csv`）

| 列 | 含义 | 取值 |
|----|------|------|
| `case_id` | 样本窗口编号 | 如 `inject_receivables_1.0` |
| `card_id` | 卡片编号（同一样本内唯一） | 如 `card_0` |
| `signal_type` | 卡片声称的信号类型 | 8 类固定类型或 `other` |
| `period` | 卡片标注的期间 | 如 `2023` / `2022-2023` |
| `annotator` | 标注者代号 | `A` / `B` / `C` |
| `round` | 重复标注轮次（用于波动分析） | `1` / `2` |
| `signal_valid` | 该卡片是否对应数据中真实存在的可报告信号 | `yes` / `uncertain` / `no` |
| `severity_label` | 标注者认定的严重度 | `low` / `medium` / `high` / `na` |
| `d7_score` | 解释与边界质量（1–5，Rubric 锚定，见 §3） | 1.0–5.0 |
| `d8_violation` | 卡片是否含买卖建议/无依据造假认定等违规 | `yes` / `no` / `na` |
| `note` | 自由备注 | 文本 |

---

## 3. D7 质量评分锚定（1–5）

| 分 | 标准 |
|----|------|
| 5 | 解释有完整证据锚定（fact_basis 可回表）、给出 ≥1 替代解释、给出可核查建议、明确结论边界 |
| 4 | 证据锚定基本完整，有替代解释或核查建议其一，边界明确 |
| 3 | 有证据但锚定偏弱，或缺替代解释，边界含糊 |
| 2 | 证据笼统或仅复述数字，无替代解释，边界缺失 |
| 1 | 无证据、编造数字、或含投资建议/无依据造假认定 |

> 注意：规则 Rubric 给满分（5.00）仅代表「套话结构齐全」，不代表真实质量——这正是需要
> 人工 D7 真值来校正的原因。人工 `d7_score` 与规则 Rubric 的差异本身是有价值的发现。

---

## 4. 一致性指标（由 `eval/validity/agreement.py` 计算）

| 指标 | 方法 | 说明 |
|------|------|------|
| 标注者间一致性（类别） | **Cohen's kappa**（两两）/ **Fleiss kappa**（≥3 人） | 对 `signal_valid` 三档（yes/uncertain/no）计算；κ≥0.6 视为可接受 |
| 严重度/质量相关性 | **Spearman 秩相关** | 对 `severity_label`（序值）或 `d7_score` 在共享卡片上计算 |
| 重复评估波动 | 同一 `(case_id, card_id, annotator)` 跨 `round` 的 `d7_score` **标准差** | 报告最大波动与均值波动；波动大说明标注指引不清晰 |

---

## 5. 运行方式

```bash
# 计算一致性（标注数据不足时输出 PENDING，不报错、不造假）
python -m eval.validity.agreement --csv eval/validity/annotations.csv

# 从在线评测结果抽取待标注卡片清单（仅列卡片，不填标注）
python -m eval.validity.agreement --emit-template results/raw/cases_run1.json
```

---

## 6. 与报告的关系

- `docs/report.md` 的 D7/D8 真值章节在标注完成前标注 `pending`。
- 规则 Rubric 与 Hy3-as-Judge 与人工标注的**一致性**称为 *agreement*（一致性），
  **绝不称为 accuracy（准确性）**——只有人工标注才是真值来源。
- 任何阶段都不得用规则 Rubric 或 Hy3-as-Judge 的分数冒充「人工验证通过」。
