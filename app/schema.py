"""异常信号卡片的结构化 Schema（对应方案 §3.5）。

用 Pydantic 定义，既作输出契约文档，也用于评估侧回表/校验前的解析。
信号类型固定 8 类 + other，避免召回率陷入语义匹配泥潭。

设计取舍（对应方案 §5.1「JSON Schema 稳定性」风险）：
- 评估真正需要的字段（signal_type / severity / periods / fact_basis 的数值与期间）
  设为“实质必需”，缺失会触发自纠正重试；
- 可溯源的描述性字段（fact_id / source_file / source_row / source_column / statement 等）
  设为 Optional，避免模型偶尔漏填整张卡片；
- value / reported_result 做字符串→浮点容错，容忍“1,100,000”“12.3%”等写法；
- signal_type 非法值回退到 other，severity 非法值回退 medium，保证解析不崩。
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 固定 8 类目标异常 + other（枚举外但可能合理的发现，不计入主指标）
SIGNAL_TYPES = [
    "cashflow_profit_divergence",      # 经营现金流与净利润背离
    "receivables_revenue_divergence",  # 应收账款与营收背离
    "inventory_cost_divergence",       # 存货与营业成本背离
    "gross_net_margin_divergence",     # 毛利率与净利率背离
    "nonrecurring_profit_dependence",  # 非经常性损益依赖
    "goodwill_net_assets_pressure",    # 商誉占净资产压力
    "short_term_solvency_pressure",    # 短期偿债压力
    "impairment_loss_surge",           # 减值损失激增
    "other",
]


class SignalType(str, Enum):
    cashflow_profit_divergence = "cashflow_profit_divergence"
    receivables_revenue_divergence = "receivables_revenue_divergence"
    inventory_cost_divergence = "inventory_cost_divergence"
    gross_net_margin_divergence = "gross_net_margin_divergence"
    nonrecurring_profit_dependence = "nonrecurring_profit_dependence"
    goodwill_net_assets_pressure = "goodwill_net_assets_pressure"
    short_term_solvency_pressure = "short_term_solvency_pressure"
    impairment_loss_surge = "impairment_loss_surge"
    other = "other"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


def _coerce_number(v):
    """字符串/数字 → float；容忍千分位、百分号、单位汉字。无法解析返回 None。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("%", "").replace("元", "").replace("万元", "0000")
        try:
            return float(s)
        except ValueError:
            return None
    return None


class FactBasis(BaseModel):
    """声称来自原始报表的单一数值，理想情况下可回表核验。"""

    model_config = ConfigDict(extra="ignore")

    fact_id: Optional[str] = None
    metric_key: Optional[str] = None
    metric_name: Optional[str] = None
    period: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    source_record_id: Optional[str] = None
    source_file: Optional[str] = None
    source_row: Optional[Union[int, str]] = None  # 容忍模型把指标名填进来的情况
    source_column: Optional[str] = None
    statement: Optional[str] = None

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value(cls, v):
        return _coerce_number(v)


class Calculation(BaseModel):
    """由原始数值推导出的比率/差值，理想情况下可由公式注册表复算。"""

    model_config = ConfigDict(extra="ignore")

    formula_id: Optional[str] = None
    operand_fact_ids: List[str] = Field(default_factory=list)
    reported_result: Optional[float] = None
    readable: Optional[str] = None

    @field_validator("reported_result", mode="before")
    @classmethod
    def _coerce_result(cls, v):
        return _coerce_number(v)


class AnomalyCard(BaseModel):
    model_config = ConfigDict(extra="ignore")

    signal_type: SignalType = Field(default=SignalType.other)
    signal_name: str = ""
    severity: Severity = Field(default=Severity.medium)
    periods: List[str] = Field(default_factory=list)
    fact_basis: List[FactBasis] = Field(default_factory=list)
    calculation: Optional[Calculation] = None
    supported_explanation: Optional[str] = None
    possible_explanations: List[str] = Field(default_factory=list)
    next_checks: List[str] = Field(default_factory=list)
    conclusion_boundary: str = Field(
        default="",
        description="必须明确：该信号只表示值得关注，无法仅凭结构化数据确定具体原因或认定造假",
    )

    @field_validator("signal_type", mode="before")
    @classmethod
    def _map_signal_type(cls, v):
        if isinstance(v, SignalType):
            return v.value
        if isinstance(v, str) and v in SIGNAL_TYPES:
            return v
        return "other"

    @field_validator("severity", mode="before")
    @classmethod
    def _map_severity(cls, v):
        if isinstance(v, Severity):
            return v.value
        if isinstance(v, str) and v in {"low", "medium", "high"}:
            return v
        return "medium"


class ScanOutput(BaseModel):
    """模型一次扫描的顶层输出：信号卡片列表。"""

    model_config = ConfigDict(extra="ignore")

    cards: List[AnomalyCard] = Field(default_factory=list)
