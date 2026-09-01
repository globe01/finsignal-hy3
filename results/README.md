# results/ —— 评测产物与可复核性说明

本目录存放评测运行产物。**只有下列 9 个脱敏文件纳入 Git 版本库**（离线自检 7 个 + 在线脱敏汇总 2 个），其余原始输出、密钥、
单次运行聚合结果均被 `.gitignore` 忽略，不会入库。

## 纳入版本库的 9 个文件（脱敏、可复核）

| 文件 | 内容 | 生成方 |
|---|---|---|
| `results/raw/cases_run1.json` | 第 1 次离线自检逐样本明细（金标准 + 完美模型卡片 + D1–D8 评估） | `run_eval --offline --runs 3` |
| `results/raw/cases_run2.json` | 第 2 次 | 同上 |
| `results/raw/cases_run3.json` | 第 3 次 | 同上 |
| `results/tables/report_run1.json` | 第 1 次离线自检集合级聚合指标（micro 口径，含分子/分母） | 同上 |
| `results/tables/report_run2.json` | 第 2 次 | 同上 |
| `results/tables/report_run3.json` | 第 3 次 | 同上 |
| `results/tables/stability.json` | 3 次运行的均值/最小/最大稳定性汇总 | 同上 |
| `results/online_summary.json` | 在线评测脱敏汇总（各指标 均值/最小/最大 + 逐轮 num/den + 运行配置） | `eval/summary_online.py` |
| `results/online_runs_summary.csv` | 同上，宽表（metric, run, value, num, den） | `eval/summary_online.py` |

> 这些文件**不含任何 API Key、endpoint、私有数据或真实上市公司数据**。离线自检由金标准
> 反向构造「完美模型」输出，全程不联网、不导入 `openai`，因此不存在接口元数据泄露风险。

## 生成命令

```bash
# 确定性离线自检：不耗 API、不需要 Key，验证评估器数学与证据链路正确性
python -m eval.run_eval --offline --runs 3
```

- 单次自检：`python -m eval.run_eval --offline`
- 真实在线评测（需配置 `.env` 中的 `HY3_API_KEY`，产物不入库）：`python -m eval.run_eval --runs 3`

## 在线评测产物（本地留档，不入库）

经 `--output-dir` 隔离，在线评测产物写入独立目录，**不覆盖**上面的离线自检锚点：

| 目录 / 文件 | 内容 | 是否入库 |
|---|---|---|
| `results/online_local/` | 3 轮真实 Hy3 评测原始产物（`cases_run1-3.json` / `report_run1-3.json` / `stability.json`），gitignored | 否（本地留档，作 §5 分析原始凭证） |
| `results/online_judge_local/` | 启用 `--hy3-judge` 的小规模评测产物，gitignored | 否 |
| `results/blind/` | 盲评导出（`export_blind.py`，剥离金标准），gitignored | 否（真实模型输出，供人工标注） |
| `results/online_summary.json` | 在线评测**脱敏汇总**（各指标 均值/最小/最大 + 逐轮 num/den + 运行配置） | ✅ 入库 |
| `results/online_runs_summary.csv` | 同上，宽表（metric, run, value, num, den） | ✅ 入库 |

> 仅 `results/online_summary.json` / `results/online_runs_summary.csv` 入库供 GitHub 用户复核；
> 二者**不含任何 API Key / endpoint / 模型原始输出 / 公司身份**，由 `eval/summary_online.py` 从
> `results/online_local/` 生成。原始在线产物始终保留在本地、不入库。

### 生成命令（在线 / 汇总 / 盲评）

```bash
# 在线评测 3 轮，产物隔离到 results/online_local（不覆盖离线锚点）
python -m eval.run_eval --runs 3 --output-dir results/online_local

# 由本地在线评测生成脱敏汇总（可提交）
python -m eval.summary_online --in-dir results/online_local \
    --out-json results/online_summary.json --out-csv results/online_runs_summary.csv

# 导出盲评数据（匿名 case_id + 财务输入 + 模型卡片，剥离金标准）
python -m eval.validity.export_blind \
    --cases results/online_local/cases_run1.json \
    --out results/blind/cases_run1_blind.json
```

## 运行时间（最近一次提交入库的运行）

- 生成日期：2026-08-31
- 样本规模：**40** 个合成（synthetic）评测窗口
- 运行环境：Python 3.13，确定性（无随机种子，构造完全由代码与固定 seed 决定）

> 重新生成会得到**逐字节一致**的结果（离线自检构造的是确定性「完美模型」，
> 不调用在线模型）。如需刷新，直接重跑上面的命令即可。

## 模型配置

| 项 | 离线自检（本目录 7 文件） | 在线评测（不入库，需 Key） |
|---|---|---|
| 模型 | 合成「完美模型」（由金标准 + 公式注册表安全函数构造，**非 Hy3**） | 腾讯云 TokenHub 上混元 `hy3`（`temperature=0`） |
| endpoint | 不联网 | `HY3_BASE_URL`（见 `.env.example`） |
| 用途 | 仅验证**评估器实现**是否正确（数学/证据核验/D1–D8 口径） | 评估 **Hy3 真实性能** |
| 结论 | 必须全 100%（否则是评估器 bug） | 反映模型能力，需多次取均值 |

## 数据集版本

- 数据集由 `eval/run_eval.py::build_dataset()` 程序化生成，**完全确定性**：
  - 阴性对照：`generator/negative.py::make_negative_control`（seed 固定）
  - 注入样本：`generator/inject.py::inject` + `eval/ground_truth.py::finalize_injection`
  - 边界样本：`generator/negative.py::make_boundary_control`
  - 类别覆盖：阴性 / 低·中·高三档注入 / 临界阈值 / 长文本 / 术语堆砌 / 年份错置（详见 `docs/report.md` §评测样本）
- 数据性质：**全部为合成（synthetic）数据**，由清洁基底 + 受控异常注入生成，**不冒充任何真实上市公司**，
  不构成任何公司存在财务造假的暗示。真实数据接入为后续阶段（Phase 3）。
- 数据集「版本」即本仓库当前 `generator/` 与 `eval/run_eval.py` 的代码状态；复现请对齐对应 commit。

## ⚠️ 关键解读边界（务必区分）

1. **本目录 7 个文件 = 评估器自检（evaluator self-check），不是 Hy3 性能结果。**
   它们用「完美模型」跑通全链路，目的是证明评估器数学与证据核验无误
   （MRhigh=0%、P/R/Rw=100%、D1/D2/D3/D8=100% 为**预期且必须**的结果）。
   任何把这套 100% 当作「Hy3 准确率」的说法都是错误的。
2. **Hy3 真实性能**须经在线评测（`--runs 3`，需 Key）取得，且即便 `temperature=0`
   在线模型仍有波动，必须多次取均值/区间 —— 该结果**不入库**，仅在 `docs/report.md`
   的「在线评测（待运行）」一节标注 `pending`。
3. D7/D8 的真值需人工标注（`docs/annotation_guide.md`）与 Hy3 语义评审交叉确认，
   当前规则 Rubric 与 Hy3-as-Judge 两条路径只报**一致性（agreement）**，不报准确性（accuracy）。

详见 `docs/report.md`（评测报告）与 `docs/proposal.md`（方法学方案）。
