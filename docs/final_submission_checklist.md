# 最终提交清单

本文按犀牛鸟开源实战任务一的交付要求整理 FinSignal-Hy3 当前状态，供提交前自查。

## 1. 开源项目仓库

- 项目名称与 README 已标注为「犀牛鸟开源实战任务 · 题目一 · 个人参赛作品」，避免被误认为腾讯官方项目。
- README 已包含项目介绍、目标用户、场景价值、运行方式、环境要求、在线 Demo、免责声明和 License。
- Hy3 调用通过 `.env` / 环境变量读取 `HY3_API_KEY`，仓库仅提交 `.env.example`；`.env`、原始 PDF、在线原始输出均在 `.gitignore` 中忽略。
- Streamlit Demo 支持真实样本、CSV/Excel 上传、表格录入和文本粘贴四种输入方式。

## 2. 应用侧

- 场景：制造业上市公司 3 至 5 年结构化财务数据的财务关注信号识别。
- 目标用户：财务学习者、投研实习生、审计辅助人员。
- Hy3 产物：包含事实依据、计算过程、替代解释、核查建议和结论边界的分析输出。
- 安全边界：输出只表示「值得进一步核查」，不构成投资建议、交易建议、审计结论或财务造假认定。

## 3. 评估方法

- 任务一要求的 5 个以上维度已覆盖：D1 事实引用、D2 公式复算、D3 可追溯、D4 覆盖/召回、D5 误报控制、D6 严重度一致、D7 解释与边界、D8 安全合规。
- 主指标为严重异常漏报率 MRhigh，支撑指标包括加权召回、精确率、数值准确、公式正确、严格可追溯、严重度一致和合规率。
- D7/D8 同时提供规则 Rubric 与 Hy3-as-Judge 两条评审路径，二者只报告一致性，不冒充人工真值。

## 4. 评测样本

- 合成注入样本：40 个窗口，覆盖阴性、低/中/高三档异常、阈下边界、长文本、术语堆砌和年份错置。
- 真实公开样本：8 家制造业上市公司，2021-2025 年共 40 份公开年报结构化摘录，生成 24 个三年窗口，其中 17 READY、7 PARTIAL。
- 真实 Hy3 小规模实跑：7 条代表样本，4 READY + 3 PARTIAL/N-A。

## 5. 有效性验证

- 判别力验证：`data/derived/discriminative_validation.csv`，32 条 fixture，当前排序为 good 100.00 > medium 85.41 > bad 79.89 > adversarial 57.09，排序假设全部 PASS。
- 一致性验证：`data/derived/consistency_validation.csv`，32 条 fixture 重复 3 轮，共 96 行，total 分数 max_delta = 0。
- 真实 Hy3 快照：`data/derived/hy3_real_eval_results.csv`，7 条样本当前规则评分均分 94.00，READY 均分 96.25，PARTIAL/N-A 均分 91.00。
- 人工一致性：`eval/validity/annotations_todo.csv` 已生成 53 张卡片 × A/B 两名标注者 = 106 行待填表；填完后运行 `eval.validity.agreement` 计算 Cohen's kappa / Spearman。当前不伪造人工一致性数值。
- 真实样本 D4/D5：`eval/validity/real_signal_todo.csv` 已生成 7 条真实 Hy3 输出 × 8 类信号 × A/B 两名标注者 = 112 行待填表；填完后运行 `eval.validity.real_signal_metrics` 计算 MRhigh/P/R/Rw。当前不伪造真实样本主指标。

## 6. 分析报告

- `docs/report.md` 已包含场景选择理由、样本构造、评估维度设计、在线评测、判别力验证、一致性验证、典型失败模式、模型能力边界和后续工作。
- `docs/manual_review_notes.md` 与 `docs/manual_gold_mini.md` 记录轻量人工抽检和 mini gold，用于说明规则快照与业务真值的边界。

## 7. Demo 视频

- 2 分钟以内录制脚本见 `docs/demo_video_script.md`。
- 推荐展示路径：打开在线 Demo 或本地 Streamlit，选择真实样本，运行扫描，展示信号卡片、本地校验面板和免责声明。

## 8. 提交前命令

```bash
python -m eval.run_eval --offline
python scripts/run_hy3_real_samples.py --score-existing
python -m eval.real_sample_eval
python -m eval.consistency_validation
python -m eval.validity.real_signal_metrics --csv eval/validity/real_signal_todo.csv
python -m pytest -q
git diff --check
```
