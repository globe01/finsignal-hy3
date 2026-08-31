"""财务异常扫描入口：输入结构化财务文本，调用 Hy3 输出并校验异常卡片。

用法：python -m app.scan
本文件同时作为「JSON Schema 稳定性」的首次真实验证（方案 §5.1 的最大执行风险）。

解析策略（对应方案 §5.1）：
- 主路径：Hy3 输出 → json.loads → ScanOutput.model_validate；
- 自纠正：校验失败时把错误摘要回传 Hy3，最多重试 2 次；
- 兜底：仍不合规时做容错解析，保证流程不崩、尽量保留已识别卡片。
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from app.llm import Hy3Client
from app.prompts.scan_prompt import build_scan_messages
from app.schema import AnomalyCard, ScanOutput, SignalType

# 最小测试样本：虚构制造业公司，刻意植入两类明显异常
#  - 应收账款增速远高于营收（receivables_revenue_divergence）
#  - 经营现金流/净利润连续<0.5 且净利润为正（cashflow_profit_divergence）
SAMPLE = """\
公司：示例制造公司
单位：元

利润表：
年度,2022,2023,2024
营业收入,1000000000,1120000000,1254000000
营业成本,800000000,896000000,1003200000
毛利,200000000,224000000,251800000
净利润,80000000,90000000,100000000
非经常性损益,5000000,6000000,7000000

资产负债表：
年度,2022,2023,2024
货币资金,200000000,180000000,150000000
应收账款,150000000,210000000,310000000
存货,120000000,140000000,170000000
商誉,0,0,0
流动资产,600000000,680000000,750000000
流动负债,400000000,430000000,470000000
短期借款,100000000,120000000,150000000
总资产,1200000000,1300000000,1400000000
所有者权益,700000000,750000000,800000000
未分配利润,300000000,350000000,400000000

现金流量表：
年度,2022,2023,2024
经营活动现金流量净额,90000000,40000000,30000000
投资活动现金流量净额,-50000000,-40000000,-30000000
筹资活动现金流量净额,20000000,30000000,40000000
"""


def _lenient_parse(raw: dict) -> ScanOutput:
    """容错解析：逐卡片 model_validate，失败卡片降级为 other 占位，保证不丢流程。"""
    cards_raw = raw.get("cards", []) if isinstance(raw, dict) else []
    cards: list[AnomalyCard] = []
    for c in cards_raw:
        if not isinstance(c, dict):
            continue
        try:
            cards.append(AnomalyCard.model_validate(c))
        except Exception:
            cards.append(
                AnomalyCard(
                    signal_type=SignalType.other,
                    signal_name=str(c.get("signal_name", ""))[:200],
                    conclusion_boundary=str(c.get("conclusion_boundary", ""))[:500],
                )
            )
    return ScanOutput(cards=cards)


def scan_text(
    text: str,
    company: str = "示例制造公司",
    years: str = "2022-2024",
    max_schema_retries: int = 2,
    temperature: float | None = None,
) -> ScanOutput:
    client = Hy3Client()
    messages = build_scan_messages(company, years, text)
    raw = client.chat(messages, json_mode=True, temperature=temperature)

    for attempt in range(max_schema_retries + 1):
        try:
            data = json.loads(raw)
            return ScanOutput.model_validate(data)
        except (json.JSONDecodeError, ValidationError) as e:
            if attempt == max_schema_retries:
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
                out = _lenient_parse(data)
                print(
                    f"[warn] 经 {max_schema_retries} 次自纠正仍不合规，已做容错解析"
                    f"（保留 {len(out.cards)} 张卡片）。错误摘要：{str(e)[:300]}"
                )
                return out
            # 方案 §5.1：把校验错误回传 Hy3 让其修正
            messages = list(messages) + [
                {
                    "role": "user",
                    "content": (
                        "你的输出不符合 JSON Schema，请严格按系统示例的字段结构修正后"
                        "只输出 JSON（不要任何说明或代码块）：\n" + str(e)[:1500]
                    ),
                }
            ]
            raw = client.chat(messages, json_mode=True)
    # 理论上不会到这；保险返回空结果
    return ScanOutput(cards=[])


def main() -> None:
    out = scan_text(SAMPLE)
    print(f"识别到 {len(out.cards)} 张信号卡片：\n")
    for c in out.cards:
        facts = [(f.metric_name, f.period, f.value) for f in c.fact_basis]
        print(f"[{c.severity.value}] {c.signal_type.value} — {c.signal_name}")
        print(f"  期间: {c.periods}")
        print(f"  事实: {facts}")
        print(f"  计算: {c.calculation.readable if c.calculation else None}")
        print(f"  结论边界: {c.conclusion_boundary}\n")


if __name__ == "__main__":
    main()
