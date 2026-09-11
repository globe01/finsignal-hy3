# AI 辅助标注预填说明

本文记录 `eval/validity/prefill_ai_annotations.py` 生成的两份预填表。它们用于降低人工复核成本，**不是人工金标准**，也不应在 README / 报告中写成「人工一致性已完成」。

## 生成文件

- `eval/validity/annotations_ai_prefill.csv`：基于 `results/online_local/cases_run1.json` 与合成样本金标准，对 53 张模型卡片 × A/B 两名标注者生成卡片级预填。
- `eval/validity/real_signal_ai_prefill.csv`：基于 7 条真实 Hy3 输出、结构化财务数据与当前信号阈值，对 7 × 8 类信号 × A/B 两名标注者生成信号级预填。

所有行的 `note` 均写入 `AI-assisted prefill; not human gold.`，避免误当成人工标注。

## 使用方式

```bash
python -m eval.validity.prefill_ai_annotations
python -m eval.validity.real_signal_metrics --csv eval/validity/real_signal_ai_prefill.csv
python -m eval.validity.agreement --csv eval/validity/annotations_ai_prefill.csv --json
```

## 当前预填结果

真实样本信号级预填可计算 56 个 `(sample_id, signal_type)` 项：

- TP = 6
- FP = 5
- FN = 2
- MRhigh = 2/4 = 0.50
- P = 6/11 = 0.5455
- R = 6/8 = 0.75
- Rw = 13/19 = 0.6842

卡片级预填中，53 张在线卡片均与合成样本金标准按 `signal_type + period overlap` 匹配为 `signal_valid=yes`。由于 A/B 两列由同一套 AI 预填逻辑生成，Cohen's kappa 和 Spearman 可能为 `NaN` 或无实际解释意义；它只能作为复核起点，不能作为人工一致性证明。

## 正式提交边界

如需形成正式人工一致性材料，应由两名真实标注者独立复核预填表，必要时修改标签与备注，再另存为：

- `eval/validity/annotations_filled.csv`
- `eval/validity/real_signal_filled.csv`

只有这两份经人工复核后的文件，才适合用于报告人工一致性与真实样本 D4/D5 主结论。
