"""D1 原始数值准确性 与 D3 证据可追溯性（对应方案 §6.1、§5.3）。

严格口径：

- **D1 原始数值准确性**：fact_basis 中声称来自报表的数值，优先按 source_record_id 回表，
  其次按 (科目, 期间) 降级定位；相对误差 <= 1% 视为命中。降级定位的条数单独统计，
  以便说明有多少 D1 命中并非由稳定记录号支撑。
- **D3 证据可追溯性**：必须通过 source_record_id 回表成功，且
  D3_FIELDS（共 10 个字段）与记录索引逐一一致，才计为「严格可追溯」：
  source_record_id / source_file / source_row / source_column / statement /
  metric_key / metric_name / period / value / unit。
  仅"科目名和年份存在"不再计为可追溯——那只是定位可行，不是证据可核验。

同时输出逐字段通过率，便于定位模型到底缺哪一类证据字段。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.schema import AnomalyCard
from generator.base import (
    CN,
    Company,
    SourceRecord,
    build_lookup_index,
    build_record_index,
)

# D3 严格可追溯需要逐一核验的字段（共 10 个，方案 §5.3 / 评测需求）
# 任一项不一致即 D3 严格口径判不通过；不为提分放宽任何字段。
D3_FIELDS = (
    "source_record_id",
    "source_file",
    "source_row",
    "source_column",
    "statement",
    "metric_key",
    "metric_name",
    "period",
    "value",
    "unit",
)

D1_REL_TOL = 0.01  # 方案 §6.1：相对误差 <= 1% 视为命中

# statement 归一化：容忍模型填英文 key、中文报表名或源文件名
_STATEMENT_ALIASES = {
    "income": "income", "利润表": "income", "income_statement": "income",
    "income_statement.csv": "income", "损益表": "income", "is": "income",
    "balance": "balance", "资产负债表": "balance", "balance_sheet": "balance",
    "balance_sheet.csv": "balance", "bs": "balance",
    "cashflow": "cashflow", "现金流量表": "cashflow", "cash_flow": "cashflow",
    "cash_flow.csv": "cashflow", "cf": "cashflow", "cash flow": "cashflow",
}

_CN_TO_KEY = {cn: key for key, cn in CN.items()}


def _norm_statement(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _STATEMENT_ALIASES.get(str(value).strip().lower(), str(value).strip())


def _norm_row(value) -> Optional[int]:
    """source_row 容忍 int / '2' / 'BS_R03' / '第3行' 等写法。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None
    # 形如 BS_R03_2023 会含年份，取第一段连续数字
    for part in text.replace("_", " ").replace("R", " ").split():
        p = "".join(ch for ch in part if ch.isdigit())
        if p and len(p) <= 2:
            return int(p)
    return int(digits[:2])


def _norm_metric_key(fact) -> Optional[str]:
    """metric_key 缺失时用中文 metric_name 反查，避免误判为不可追溯。"""
    if fact.metric_key:
        return str(fact.metric_key).strip()
    if fact.metric_name:
        return _CN_TO_KEY.get(str(fact.metric_name).strip())
    return None


@dataclass
class FactCheck:
    """单条 fact_basis 的核验结果。"""

    fact_index: int
    claimed_record_id: Optional[str] = None
    resolved_by: str = "unresolved"  # source_record_id / fallback / unresolved
    field_ok: Dict[str, bool] = field(default_factory=dict)
    d1_hit: Optional[bool] = None
    d3_strict_ok: bool = False
    issues: List[str] = field(default_factory=list)


def _resolve(fact, record_idx: Dict[str, SourceRecord],
             lookup_idx) -> tuple[Optional[SourceRecord], str]:
    """优先用 source_record_id 回表；失败则用 (科目, 期间) 降级定位。"""
    rid = (str(fact.source_record_id).strip() if fact.source_record_id else "")
    if rid and rid in record_idx:
        return record_idx[rid], "source_record_id"
    period = str(fact.period).strip() if fact.period is not None else ""
    for token in (fact.metric_name, fact.metric_key):
        if token and period:
            rec = lookup_idx.get((str(token).strip(), period))
            if rec is not None:
                return rec, "fallback"
    return None, "unresolved"


def check_fact(fact, index: int, record_idx: Dict[str, SourceRecord],
               lookup_idx) -> FactCheck:
    """对单条 fact_basis 做 D1 + D3 严格核验。"""
    chk = FactCheck(fact_index=index,
                    claimed_record_id=str(fact.source_record_id) if fact.source_record_id else None)
    rec, how = _resolve(fact, record_idx, lookup_idx)
    chk.resolved_by = how

    if rec is None:
        chk.issues.append("无法定位源记录")
        chk.field_ok = {f: False for f in D3_FIELDS}
        chk.d1_hit = False
        return chk

    # ---- D3 逐字段核验（全部以记录索引为准）----
    ok: Dict[str, bool] = {}
    ok["source_record_id"] = how == "source_record_id"
    if not ok["source_record_id"]:
        chk.issues.append("source_record_id 缺失或无法回表")

    ok["source_file"] = (
        str(fact.source_file).strip() == rec.source_file if fact.source_file else False
    )
    ok["source_row"] = _norm_row(fact.source_row) == rec.source_row
    ok["source_column"] = (
        str(fact.source_column).strip() == rec.source_column if fact.source_column else False
    )
    ok["statement"] = _norm_statement(fact.statement) == rec.statement
    ok["metric_key"] = _norm_metric_key(fact) == rec.metric_key
    ok["metric_name"] = (
        str(fact.metric_name).strip() == rec.metric_name if fact.metric_name else False
    )
    ok["period"] = (str(fact.period).strip() == rec.period) if fact.period else False

    # ---- D1 数值核验（1% 相对容差；0 值用绝对容差）----
    value_ok = False
    if fact.value is not None:
        if rec.value == 0:
            value_ok = abs(fact.value) <= 1e-6
        else:
            value_ok = abs(fact.value - rec.value) / abs(rec.value) <= D1_REL_TOL
    ok["value"] = value_ok
    ok["unit"] = (str(fact.unit).strip() == rec.unit) if fact.unit else False

    chk.field_ok = ok
    chk.d1_hit = value_ok
    chk.d3_strict_ok = all(ok[f] for f in D3_FIELDS)
    if not chk.d3_strict_ok:
        chk.issues += [f"{f} 不一致" for f in D3_FIELDS if not ok[f] and f != "source_record_id"]
    return chk


def eval_facts(card: AnomalyCard, company: Company) -> Dict:
    """返回该卡片在 D1/D3 上的核验明细与计数。"""
    record_idx = build_record_index(company)
    lookup_idx = build_lookup_index(company)
    checks = [check_fact(f, i, record_idx, lookup_idx)
              for i, f in enumerate(card.fact_basis)]
    field_hits = {f: sum(1 for c in checks if c.field_ok.get(f)) for f in D3_FIELDS}
    return {
        "n_facts": len(checks),
        "d1_hits": sum(1 for c in checks if c.d1_hit),
        "d1_total": len(checks),
        "d3_strict_hits": sum(1 for c in checks if c.d3_strict_ok),
        "d3_strict_total": len(checks),
        "resolved_by_record_id": sum(1 for c in checks if c.resolved_by == "source_record_id"),
        "resolved_by_fallback": sum(1 for c in checks if c.resolved_by == "fallback"),
        "unresolved": sum(1 for c in checks if c.resolved_by == "unresolved"),
        "field_hits": field_hits,
        "checks": [
            {
                "fact_index": c.fact_index,
                "claimed_record_id": c.claimed_record_id,
                "resolved_by": c.resolved_by,
                "d1_hit": c.d1_hit,
                "d3_strict_ok": c.d3_strict_ok,
                "field_ok": c.field_ok,
                "issues": c.issues,
            }
            for c in checks
        ],
    }


def aggregate_facts(cards: List[AnomalyCard], company: Company) -> Dict:
    """汇总多张卡片的 D1/D3，所有比率同时给出分子分母（方案 §6.4）。"""
    h1 = t1 = h3 = t3 = 0
    by_rid = by_fb = unres = 0
    field_hits = {f: 0 for f in D3_FIELDS}
    details: List[Dict] = []
    for card in cards:
        r = eval_facts(card, company)
        h1 += r["d1_hits"]; t1 += r["d1_total"]
        h3 += r["d3_strict_hits"]; t3 += r["d3_strict_total"]
        by_rid += r["resolved_by_record_id"]
        by_fb += r["resolved_by_fallback"]
        unres += r["unresolved"]
        for f in D3_FIELDS:
            field_hits[f] += r["field_hits"][f]
        details.append(r)
    return {
        # D1
        "d1_hits": h1, "d1_total": t1, "d1_rate": (h1 / t1) if t1 else None,
        # D3 严格口径
        "d3_strict_hits": h3, "d3_strict_total": t3,
        "d3_strict_rate": (h3 / t3) if t3 else None,
        # 定位方式分布：用于说明证据链强度
        "resolved_by_record_id": by_rid,
        "resolved_by_fallback": by_fb,
        "unresolved": unres,
        # 逐字段通过率，便于定位缺哪类证据
        "d3_field_hits": field_hits,
        "d3_field_rates": {f: (field_hits[f] / t3) if t3 else None for f in D3_FIELDS},
        "per_card": details,
    }
