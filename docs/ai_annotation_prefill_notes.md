# 人工标注复核说明

本文记录标注材料从 AI 辅助预填到人工复核确认的流转关系，避免把模板、预填文件和正式人工标注混用。

## 文件关系

- `eval/validity/annotations_todo.csv`：卡片级 A/B 双人盲评空白模板，用于复现标注流程。
- `eval/validity/real_signal_todo.csv`：真实样本 D4/D5 信号级 A/B 双人标注空白模板，用于复现标注流程。
- `eval/validity/annotations_ai_prefill.csv`：AI 辅助预填草稿，作为人工复核的起点，不作为最终真值引用。
- `eval/validity/real_signal_ai_prefill.csv`：真实样本信号级 AI 辅助预填草稿，作为人工复核的起点，不作为最终真值引用。
- `eval/validity/annotations_filled.csv`：经人工检查、修正并确认后的卡片级正式标注结果。
- `eval/validity/real_signal_filled.csv`：经人工检查、修正并确认后的真实样本信号级正式标注结果。

正式 README、报告和提交清单只引用 `*_filled.csv` 作为人工标注依据；`*_ai_prefill.csv` 仅保留为过程材料。

## 当前人工复核结果

真实样本信号级人工标注可计算 56 个 `(sample_id, signal_type)` 项：

- TP = 6
- FP = 5
- FN = 2
- MRhigh = 2/4 = 0.50
- P = 6/11 = 0.5455
- R = 6/8 = 0.75
- Rw = 13/19 = 0.6842
- over_inference_rate = 5/11 = 0.4545

卡片级人工标注共 106 行，覆盖 53 张模型卡片与 A/B 两名标注者。当前 A/B 在 `signal_valid` 与 `d7_score` 上完全一致；由于 `signal_valid` 为单一类别且 `d7_score` 为常量，Cohen's kappa 与 Spearman 在统计定义上返回 `NaN`，这表示指标不可定义，不表示脚本失败或标注缺失。

## 复现实验命令

```bash
python -m eval.validity.agreement --csv eval/validity/annotations_filled.csv --json
python -m eval.validity.real_signal_metrics --csv eval/validity/real_signal_filled.csv
```
