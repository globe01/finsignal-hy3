"""财务异常扫描 prompt 构造（对应方案 §3.5 / §5.1）。

设计要点：
- 强制从固定 8 类枚举中选择 signal_type，other 仅用于枚举外但可能合理的发现；
- 要求每张卡片包含原始数值 + 计算过程 + 替代解释 + 结论边界；
- 明确禁止把异常信号表述为财务造假或投资建议；
- 通过「完整示例」锚定 JSON 字段形态，配合 schema 自纠正重试（方案 §5.1）；
- 输出严格 JSON（顶层 {"cards": [...]}），由调用层用 JSON 模式约束。

两处与评估器对齐的修正（否则指标会被 prompt 缺陷污染，而非反映模型能力）：
1. `calculation.formula_id` 必须取自 config/formula_registry.yaml，注册表目录直接
   写进 prompt（原示例用的 `growth_delta` 并不在注册表中，会让 D2 第 1 步必然失败）；
2. `source_record_id` 必须回填输入数据里给出的 `<row_id>_<年度>` 形式（如 BS_R03_2024），
   原示例写的是 `bs_2023` 这类自造 ID，无法回表，会让 D3 严格可追溯必然为 0。
"""
from __future__ import annotations

from app.formulas import formula_catalog_text
from app.schema import SIGNAL_TYPES

# 八类异常的中文释义，提供给模型作为语义锚点
SIGNAL_GLOSSARY = """\
- cashflow_profit_divergence：经营现金流与净利润背离（经营净现金流/净利润连续两年<0.5 且净利润为正）
- receivables_revenue_divergence：应收账款与营业收入背离（应收增速−营收增速≥20个百分点）
- inventory_cost_divergence：存货与营业成本背离（存货增速−营业成本增速≥20个百分点）
- gross_net_margin_divergence：毛利率与净利率背离（毛利率上升而净利率下降≥2个百分点）
- nonrecurring_profit_dependence：非经常性损益依赖（非经常性损益绝对值/净利润≥30%）
- goodwill_net_assets_pressure：商誉占净资产压力（商誉/所有者权益≥20%）
- short_term_solvency_pressure：短期偿债压力（流动比率<1 且 短期借款/货币资金>1）
- impairment_loss_surge：减值损失激增（减值损失同比增幅≥100% 且占利润总额绝对值≥10%）
"""

EVIDENCE_RULES = """\
证据字段填写规则（务必严格遵守，否则无法回表核对）：
- source_record_id：必须写成输入表格里的 `<row_id>_<年度>`，例如 BS_R03_2024、IS_R02_2023；
- source_row：取 row_id 里的数字（BS_R03 → 3）；
- source_column 与 period：都写年度，例如 "2024"；
- statement：写 income / balance / cashflow（分别对应利润表 / 资产负债表 / 现金流量表）；
- source_file：写输入中该表标注的文件名，例如 balance_sheet.csv；
- metric_key：写英文科目键（如 accounts_receivable、revenue、cfo、net_profit）；
- 不要自造 ID，也不要凭空补全没在输入里出现的单元格。
"""

FORMULA_RULES = """\
calculation 填写规则：
- formula_id 必须从下列注册表中选择（不得自造公式名，也不得混用其它信号的公式）：
{catalog}
- operand_fact_ids 必须列出复算所需的全部 fact_basis 条目 id；
  同比类公式（需要 t-1 与 t 两个年度）必须把两年的操作数都写进 fact_basis；
- reported_result 用小数表示比率：0.25 表示 25 个百分点，不要写成 25；
- 若无法用注册表中的公式表达，则省略 calculation 字段，不要硬凑。
"""

# 完整输出示例，作为字段形态锚点（注意每个 fact_basis 都带全部字段）
EXAMPLE_BLOCK = """\
输出示例（严格照此结构，字段一个不能少；示例中的数字仅示形态，必须替换为输入里真实存在的数字）：

{"cards": [
  {
    "signal_type": "receivables_revenue_divergence",
    "signal_name": "应收账款增速显著高于营业收入增速",
    "severity": "high",
    "periods": ["2024"],
    "fact_basis": [
      {"fact_id": "ar_2023", "metric_key": "accounts_receivable", "metric_name": "应收账款", "period": "2023", "value": 168, "unit": "元", "source_record_id": "BS_R03_2023", "source_file": "balance_sheet.csv", "source_row": 3, "source_column": "2023", "statement": "balance"},
      {"fact_id": "ar_2024", "metric_key": "accounts_receivable", "metric_name": "应收账款", "period": "2024", "value": 250, "unit": "元", "source_record_id": "BS_R03_2024", "source_file": "balance_sheet.csv", "source_row": 3, "source_column": "2024", "statement": "balance"},
      {"fact_id": "rev_2023", "metric_key": "revenue", "metric_name": "营业收入", "period": "2023", "value": 1120, "unit": "元", "source_record_id": "IS_R02_2023", "source_file": "income_statement.csv", "source_row": 2, "source_column": "2023", "statement": "income"},
      {"fact_id": "rev_2024", "metric_key": "revenue", "metric_name": "营业收入", "period": "2024", "value": 1254, "unit": "元", "source_record_id": "IS_R02_2024", "source_file": "income_statement.csv", "source_row": 2, "source_column": "2024", "statement": "income"}
    ],
    "calculation": {"formula_id": "ar_minus_rev_growth", "operand_fact_ids": ["ar_2023", "ar_2024", "rev_2023", "rev_2024"], "reported_result": 0.37, "readable": "应收账款增速约49% 减去营业收入增速约12%，差约37个百分点"},
    "supported_explanation": "应收账款由2023年168增至2024年250（增速约49%），同期营业收入增速约12%，两者背离约37个百分点。",
    "possible_explanations": ["放宽信用政策冲收入", "下游回款变慢", "季节性备货导致单纯时点偏高"],
    "next_checks": ["核对账龄结构与坏账计提", "比对前五大客户回款", "查看期后回款情况"],
    "conclusion_boundary": "仅为值得关注的信号，不能据此认定财务造假，也不构成投资建议。"
  }
]}

注意：
- 若未识别到任何异常，返回 {"cards": []}；
- 不要输出 Markdown 代码块、不要额外说明文字，只输出 JSON；
- fact_basis 中每个数值都要填 value（数字）、period（如 "2024"）、metric_name；
- conclusion_boundary 必须包含「不能据此认定财务造假」。
"""

SYSTEM_PROMPT_HEAD = """你是一名财务分析辅助助手，服务于财务学习、投研初筛与审计辅助场景。
任务：阅读一家制造业上市公司连续多年的结构化财务数据，识别值得关注的财务信号。

规则：
1. 每张信号卡片必须从以下固定枚举中选择 signal_type（不要自造类型名，除非明显属于 other）：
"""

SYSTEM_PROMPT_TAIL = """
2. 每张卡片必须给出全部字段：signal_type、signal_name、severity(low/medium/high)、periods、fact_basis（逐条原始数值，每条都要含 metric_name/period/value 以及 source_record_id/source_file/source_row/source_column/statement）、calculation（如有推导）、supported_explanation、possible_explanations（仅作为待核查假设）、next_checks、conclusion_boundary。
3. fact_basis 中的数值必须是输入数据里真实存在的数字；calculation.reported_result 必须能由 fact_basis 中的数值复算得到。
4. 严格区分“事实”“数据支持的推断”“仅作为假设的替代解释”。
5. 结论边界必须写明：该信号只表示值得进一步核查，不能据此认定财务造假，也不构成投资建议。
6. 只输出 JSON，顶层结构为 {"cards": [ ... ]}，不要任何额外说明或 Markdown 代码块。"""

USER_TEMPLATE = """以下是公司「{company}」连续 {years} 年的结构化财务数据（单位已统一为元）：

{data_text}

请按系统指令识别财务关注信号并输出 JSON。"""


def build_scan_messages(company: str, years: str, data_text: str) -> list[dict]:
    system_content = (
        SYSTEM_PROMPT_HEAD
        + "（合法取值：" + " / ".join(SIGNAL_TYPES) + "）\n"
        + SIGNAL_GLOSSARY
        + "\n"
        + SYSTEM_PROMPT_TAIL
        + "\n\n"
        + EVIDENCE_RULES
        + "\n"
        + FORMULA_RULES.format(catalog=formula_catalog_text())
        + "\n"
        + EXAMPLE_BLOCK
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": USER_TEMPLATE.format(company=company, years=years, data_text=data_text)},
    ]
