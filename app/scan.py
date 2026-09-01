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
from app.sample_data import SAMPLE
from app.schema import AnomalyCard, ScanOutput, SignalType



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
