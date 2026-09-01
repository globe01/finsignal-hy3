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

标注对象 = 模型产出的**异常卡片**。为避免标注者接触金标准（失去独立性），一律通过**盲评导出工具**
生成材料，再交付标注：

```bash
# 从一次固定在线评测导出盲评数据（匿名 case_id + 财务输入原文 + 模型卡片，剥离金标准）
python -m eval.validity.export_blind \
    --cases results/online_local/cases_run1.json \
    --out results/blind/cases_run1_blind.json
```

导出的每条记录只含三字段：`case_id`（匿名 `case_000`…）、`input_text`（模型实际看到的财务输入原文）、
`cards`（模型产出的卡片）。标注者**只看「财务数据原文 + 模型卡片」**，不接触任何金标准 / 注入元数据。

> ⚠️ 不要在标注前看到金标准（`ground_truth`）或注入元数据（`meta.inject` / `meta.severity` /
> `meta.category`）——否则标注会被锚定，失去独立性。盲评导出已自动剔除这些字段。

---

## 2. 标注字段（对应 `eval/validity/annotations_template.csv`）

| 列 | 含义 | 取值 |
|----|------|------|
| `case_id` | 样本窗口编号（**匿名**，由 `--emit-template` 自动生成 `case_000`…） | 如 `case_000` |
| `card_id` | 卡片编号（**同一样本内稳定序号 `card_000`…**，保证全局 `(case_id, card_id)` 唯一） | 如 `card_000` |
| `signal_type` | 卡片声称的信号类型（模型自陈，供标注者核对） | 8 类固定类型或 `other` |
| `period` | 卡片标注的期间 | 如 `2023` / `2022-2023` |
| `annotator` | 标注者代号 | `A` / `B` |
| `round` | 重复标注轮次（用于波动分析） | `1` / `2` |
| `signal_valid` | 该卡片是否对应数据中真实存在的可报告信号 | `yes` / `uncertain` / `no` |
| `severity_label` | 标注者认定的严重度（**模板留空**，由标注者独立判定，不泄露模型严重度） | `low` / `medium` / `high` / `na` |
| `d7_score` | 解释与边界质量（1–5，Rubric 锚定，见 §3） | 1.0–5.0 |
| `d8_violation` | 卡片是否含买卖建议/无依据造假认定等违规 | `yes` / `no` / `na` |
| `note` | 自由备注 | 文本 |

> `card_id` 用稳定序号而非 `signal_type`：同一 case 内可能出现重复 `signal_type`（多个同类信号），
> 用 `card_000`/`card_001`… 才能保证每个 `(case_id, card_id)` 唯一、可回表。`case_id` 已匿名化，
> 不泄露样本身份或注入类型。

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

# 从在线评测结果抽取待标注卡片清单（匿名 case_id + 稳定 card_000 序号，severity_label 留空）
python -m eval.validity.agreement --emit-template results/online_local/cases_run1.json

# 导出盲评数据（剥离金标准，供标注者独立判断）
python -m eval.validity.export_blind \
    --cases results/online_local/cases_run1.json \
    --out results/blind/cases_run1_blind.json
```

### 5.1 标注执行约定（提交前收口）

- **两名标注者 A、B 独立标注同一批卡片**：从盲评导出中选取 **50–60 张卡片**（覆盖各信号类型与
  阴性/注入/长文本/术语/年份错置等类别），A、B 各自独立标注，互不讨论。
- 同一批卡片建议做 **第 2 轮（round=2）** 重复标注，用于估计标注者内波动（见 §4 重复评估波动）。
- 标注完成后回填 `eval/validity/annotations.csv`，再跑 `agreement --csv` 计算 Cohen's / Fleiss κ
  与 Spearman ρ；标注者 <2 或仅 1 轮时 `agreement.py` 明确输出 `PENDING`，**绝不编造**一致性数值。
- 盲评导出产物（`results/blind/`）与在线原始产物（`results/online_local/`）均为真实模型输出，
  本地留档、**不入库**（已在 `.gitignore` 中）。

---

## 6. 与报告的关系

- `docs/report.md` 的 D7/D8 真值章节在标注完成前标注 `pending`。
- 规则 Rubric 与 Hy3-as-Judge 与人工标注的**一致性**称为 *agreement*（一致性），
  **绝不称为 accuracy（准确性）**——只有人工标注才是真值来源。
- 任何阶段都不得用规则 Rubric 或 Hy3-as-Judge 的分数冒充「人工验证通过」。
