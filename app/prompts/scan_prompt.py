"""财务异常扫描 prompt 构造（对应方案 §3.5 / §5.1）。

设计要点：
- 强制从固定 8 类枚举中选择 signal_type，other 仅用于枚举外但可能合理的发现；
- 要求每张卡片包含原始数值 + 计算过程 + 替代解释 + 结论边界；
- 明确禁止把异常信号表述为财务造假或投资建议；
- 通过「完整示例」锚定 JSON 字段形态，配合 schema 自纠正重试（方案 §5.1）；
- 输出严格 JSON（顶层 {"cards": [...]}），由调用层用 JSON 模式约束。
"""
from __future__ import annotations

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

# 完整输出示例，作为字段形态锚点（注意每个 fact_basis 都带全部字段）
EXAMPLE_BLOCK = """\
输出示例（严格照此结构，字段一个不能少，数值用输入里真实存在的数字）：

{"cards": [
  {
    "signal_type": "receivables_revenue_divergence",
    "signal_name": "应收账款增速显著高于营业收入增速",
    "severity": "high",
    "periods": ["2023", "2024"],
    "fact_basis": [
      {"fact_id": "ar_2023", "metric_key": "accounts_receivable", "metric_name": "应收账款", "period": "2023", "value": 210000000, "unit": "元", "source_record_id": "bs_2023", "source_file": "资产负债表", "source_row": 6, "source_column": "2023", "statement": "资产负债表"},
      {"fact_id": "ar_2024", "metric_key": "accounts_receivable", "metric_name": "应收账款", "period": "2024", "value": 310000000, "unit": "元", "source_record_id": "bs_2024", "source_file": "资产负债表", "source_row": 6, "source_column": "2024", "statement": "资产负债表"},
      {"fact_id": "rev_2023", "metric_key": "revenue", "metric_name": "营业收入", "period": "2023", "value": 1120000000, "unit": "元", "source_record_id": "pl_2023", "source_file": "利润表", "source_row": 2, "source_column": "2023", "statement": "利润表"}
    ],
    "calculation": {"formula_id": "growth_delta", "operand_fact_ids": ["ar_2023", "ar_2024", "rev_2023"], "reported_result": 0.27, "readable": "应收账款增速约27% 高于营收增速约12%"},
    "supported_explanation": "应收账款由2023年2.1亿增至2024年3.1亿（增速约47%），同期营收增速约12%，背离明显。",
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
        + SIGNAL_GLOSSARY
        + "\n"
        + SYSTEM_PROMPT_TAIL
        + "\n\n完整输出示例（照此结构）：\n"
        + EXAMPLE_BLOCK
    )
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": USER_TEMPLATE.format(company=company, years=years, data_text=data_text)},
    ]
