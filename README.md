# FinSignal-Hy3

> 基于 Hy3 的上市公司财务异常信号识别与漏报敏感型评估系统
>
> 犀牛鸟开源实战任务 · 题目一 · 个人参赛作品

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-proposal-orange)](#项目进度)

## 项目简介

FinSignal-Hy3 面向财务学习者、投研实习生和审计辅助人员。项目输入一家制造业上市公司连续 3～5 年的结构化财务数据，由混元 Hy3 输出包含原始数据、计算过程、替代解释、核查建议和结论边界的财务异常信号卡片。

项目同时设计一套“漏报敏感型”混合评估方法，不只检查模型报出的异常是否正确，还会显式统计模型遗漏了哪些标准异常，并通过规则复算、注入实验、阴性对照、人工标注和 Hy3 语义评审验证评估方法的可靠性。

本项目中的“财务异常”仅指满足预设条件、值得进一步核查的财务关注信号，不代表相关公司存在财务造假，也不构成投资建议。

## 核心特点

- **漏报敏感评估**：以严重异常漏报率为主指标，同时报告加权召回率、精确率和过度推断率。
- **底层数据可验证**：原始数字、期间、公式、单位和证据位置均可由程序回表复算。
- **开放分析可评审**：使用锚点式 Rubric 评价异常重要性、替代解释、核查建议和结论边界。
- **会计一致的异常注入**：通过配套传导和报表恒等式检查构造可控评测样本。
- **阴性与对抗验证**：检查模型是否强行找问题，以及评估器能否识别伪造证据、错误公式和术语堆砌。
- **评估器消融**：对比 **Hy3 语义评审**（`eval/hy3_judge.py`，逐卡片锚定 Rubric 打分）与 **规则 Rubric**（`eval/rule_rubric.py`，确定性启发式）两条路径的一致性（`compare_judges`）。两者都不是 D7/D8 的真值——规则路径会被套话骗过，Hy3 路径有同族自我偏好；真值需人工标注（待补）。

## 目标异常类型

第一版限定制造业上市公司，覆盖以下八类目标信号：

| signal_type | 财务关注信号 |
|---|---|
| cashflow_profit_divergence | 经营现金流与净利润背离 |
| receivables_revenue_divergence | 应收账款与营业收入背离 |
| inventory_cost_divergence | 存货与营业成本背离 |
| gross_net_margin_divergence | 毛利率与净利率背离 |
| nonrecurring_profit_dependence | 非经常性损益依赖 |
| goodwill_net_assets_pressure | 商誉占净资产压力 |
| short_term_solvency_pressure | 短期偿债压力 |
| impairment_loss_surge | 减值损失激增 |

系统保留 other 类型，用于记录枚举之外但可能合理的额外发现。other 不进入八类目标信号的主召回率和精确率，由人工单独复核。

## 系统架构

~~~text
制造业公司 3～5 年结构化财务数据
                    │
                    ▼
          指标预计算与 Prompt 组装
                    │
                    ▼
            Hy3 直接识别异常
                    │
                    ▼
             结构化异常卡片
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
  规则评估层             Hy3 语义评审层
  （确定性启发式）       （eval/hy3_judge.py，锚定 Rubric）
  · 原始数值回表         · 解释质量（证据锚定/替代解释/可核查性/边界）
  · 公式安全复算         · 过度推断（隐含指控等只有语义层能查的项）
  · 证据定位检查         · 安全边界
  · 漏报/误报比对
          └─────────┬─────────┘
                    ▼
             评测结果与归因报告（两条路径并列上报 + 一致性，不互相替代）
~~~

> **方法学红线**：规则评估层与 Hy3 语义评审层是**并列的两条评估路径**，都不是 D7/D8 的真值。
> 默认应用链路不会把规则候选答案提供给 Hy3；规则引擎只位于评估侧，避免模型变成对已知答案的简单确认。
> Hy3 语义评审**看不到金标准**，只喂「财务数据原文 + 待评卡片」（`eval/hy3_judge.py` 顶部说明）。

## 核心评估指标

主指标：

- 严重异常漏报率。

支撑指标：

- 严重程度加权召回率；
- 异常识别精确率；
- 原始数值匹配准确率；
- 公式与算术复算通过率；
- 过度推断率。

项目还将通过好、中、差三档输出、阴性对照、注入强度梯度和对抗样本验证评估器的判别力与一致性。

## 项目进度

当前进入**实现与验证阶段**（Phase 1 评测正确性已锁定，离线自检 + 单元测试全绿）。

- [x] 选题、范围约束和评估方法设计（漏报敏感型主指标 + 八维评估）
- [x] 系统架构与时间规划
- [x] Hy3 调用层（TokenHub OpenAI 兼容）与 JSON Schema 容错解析
- [x] 注入引擎（会计恒等式自洽）+ 四层金标准（注入元数据独立真值）
- [x] 规则评估器 D1–D8 + 微平均聚合（输出分子/分母，N/A 不记 0）
- [x] Hy3-as-Judge 语义评审模块（方案 §5.6，与规则 Rubric 并列，非替代）
- [x] 离线自检（`--offline` 零依赖）+ 203 项 pytest 单元测试
- [ ] 接入真实上市公司公开数据（Phase 3：8 家 40 份 PDF 已收集，`data/derived/manifest.csv` 来源清单已生成；**宁德时代、隆基绿能、格力电器、比亚迪、万华化学 2021-2025 已完成 5 年结构化摘录**（隆基 2024 使用修订版、2025 商誉原表为空；比亚迪 2021 BS/IS/CF 原表单位元、2022-2025 BS/IS/CF 原表单位千元、NR 表 5 年单位均元；万华化学 2021-2025 BS/IS/CF/NR 原表单位均为元，已在 CSV 中统一为元），其余 3 家待扩展；真实样本评测管线 `eval/run_real_eval.py` 仅完成骨架，不出 D4/D5 主结论。详见 [docs/real_data_sources.md](docs/real_data_sources.md) §7）
- [x] 人工一致性材料（`docs/annotation_guide.md` + `eval/validity/agreement.py` + 标注模板；标注数据待补）
- [x] Streamlit 最小可用 Demo（`app/streamlit_app.py`，上传/粘贴 → Hy3 → 卡片 + D1/D2/D3/D8 校验）

完整方案见 [docs/proposal.md](docs/proposal.md)。

## 快速开始

> 评测器（`eval/run_eval.py`）已实现且离线/在线均可用；单公司扫描与 Web Demo 为后续阶段。

### 1. 环境要求

- Python 3.10+（仓库已用 3.13 验证）
- 可访问的 Hy3 OpenAI-compatible API（腾讯云 TokenHub）

### 2. 安装依赖

~~~bash
python3 -m venv .venv

# macOS / Linux
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
~~~

Windows PowerShell 与换机复现步骤见 [docs/environment_setup.md](docs/environment_setup.md)。

### 3. 配置环境变量

复制 `.env.example` 为 `.env`，并填写本地配置（仅本地保留，不入库）：

~~~env
HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1
HY3_API_KEY=your-api-key
HY3_MODEL=hy3
~~~

### 4. 运行方式

~~~bash
# 离线自检：校验评测器数学与证据链路，不耗 API、不需要 Key
python -m eval.run_eval --offline

# 真实评测（默认 temperature=0；建议跑 3 次取均值/区间，见 §6）
python -m eval.run_eval --runs 3

# 仅冒烟前 3 个样本
python -m eval.run_eval --limit 3

# 额外启用 Hy3 语义评审（每张卡片 1 次调用，与规则 Rubric 并列上报）
python -m eval.run_eval --limit 3 --hy3-judge

# 期间严格匹配敏感性分析
python -m eval.run_eval --period-mode exact

# 在线评测产物隔离到独立目录（不覆盖 results/raw|tables 离线自检锚点）
python -m eval.run_eval --runs 3 --output-dir results/online_local
python -m eval.run_eval --limit 5 --hy3-judge --output-dir results/online_judge_local

# 由本地在线评测生成脱敏汇总（可提交，供 GitHub 复核，不含 Key/endpoint/原始输出）
python -m eval.summary_online --in-dir results/online_local \
    --out-json results/online_summary.json --out-csv results/online_runs_summary.csv

# 导出盲评数据（匿名 case_id + 财务输入 + 模型卡片，剥离金标准，供人工标注）
python -m eval.validity.export_blind --cases results/online_local/cases_run1.json \
    --out results/blind/cases_run1_blind.json

# 交互式 Demo（上传 CSV / 粘贴数据 → 调 Hy3 → 卡片展示 + D1/D2/D3/D8 校验 + 免责声明）
streamlit run app/streamlit_app.py
~~~

> 单公司扫描 CLI（`app.cli`）为后续阶段；**Streamlit 交互看板（`app/streamlit_app.py`）已实现**（见上）。
> Demo 仅做交互演示与结构性校验；批量、可复核评测请走 `python -m eval.run_eval`。

## 计划中的仓库结构

~~~text
finsignal-hy3/
├── app/                 # Hy3 调用、Schema、CLI 与 Streamlit
├── config/              # 异常规则、严重度和安全公式注册表
├── data/                # 基底数据、派生样本和金标准
├── docs/                # 方案、评估方法、标注指南和分析报告
├── eval/                # 规则评估、Hy3 Judge 和有效性实验
├── generator/           # 注入引擎与阴性样本生成
├── results/             # 原始输出、结果表和图表
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
~~~

## 数据与复现

- 财务数据只使用公开披露材料，并在数据清单中记录来源 URL、年度、报表口径和提取时间。
- 不在仓库中提交 API Key、私有数据或包含敏感信息的本地文件。
- 派生数据将记录基础公司、注入类型、修改字段、异常强度和会计一致性检查结果。
- 同一家公司的真实与派生窗口只进入同一个数据分组，避免同源数据泄漏。

## 免责声明

本项目仅用于教育、研究和开源活动展示。输出内容不能替代注册会计师、审计机构、证券研究人员或其他专业人士的判断，不构成投资建议、交易建议或财务造假认定。

## 活动声明

本项目为犀牛鸟开源活动个人参赛作品，并非腾讯、腾讯混元或 Hy3 官方发布或维护的项目。Hy3 的名称和相关权利归其权利人所有。

## License

项目代码使用 [MIT License](LICENSE) 开源。第三方模型、数据和依赖仍遵循其各自许可证与使用条款。
