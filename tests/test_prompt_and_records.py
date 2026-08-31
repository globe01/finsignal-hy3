"""Prompt 与记录索引的一致性（方案 §5.2、§5.3）。

如果 prompt 里的示例 formula_id 不在注册表、或 source_record_id 用了自造格式，
模型会被"教坏"，D2 第 1 步与 D3 严格口径必然为 0 —— 那时指标反映的是 prompt 缺陷，
不是模型能力。这些测试就是防止这种评测污染再次出现。
"""
from __future__ import annotations

import json
import re

from app.formulas import available_formula_ids, formula_catalog_text
from app.prompts.scan_prompt import EXAMPLE_BLOCK, build_scan_messages
from app.schema import SIGNAL_TYPES, ScanOutput
from generator.base import (
    ROW_LAYOUT,
    STATEMENT_META,
    build_record_index,
    company_to_text,
    record_id_of,
    row_id_of,
)

RECORD_ID_RE = re.compile(r"^(IS|BS|CF)_R\d{2}_\d{4}$")


def _example_payload() -> dict:
    """从示例块里抽出那段 JSON（示例必须本身就是合法的 ScanOutput）。"""
    start = EXAMPLE_BLOCK.index('{"cards"')
    depth, end = 0, None
    for i, ch in enumerate(EXAMPLE_BLOCK[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    assert end is not None, "示例块中的 JSON 括号不闭合"
    return json.loads(EXAMPLE_BLOCK[start:end])


# ---------------------------------------------------------------- 示例块自洽
def test_example_block_is_valid_scan_output():
    out = ScanOutput(**_example_payload())
    assert out.cards and out.cards[0].signal_type.value in SIGNAL_TYPES
    assert out.cards[0].signal_type.value != "other"


def test_example_formula_id_exists_in_registry():
    """回归：示例曾用 growth_delta，注册表里并不存在。"""
    card = _example_payload()["cards"][0]
    fid = card["calculation"]["formula_id"]
    assert fid in available_formula_ids(), f"示例 formula_id {fid} 不在注册表"
    assert "growth_delta" not in EXAMPLE_BLOCK


def test_example_record_ids_are_real(clean_company):
    """回归：示例曾用 bs_2023 这类自造 ID，无法回表。"""
    idx = build_record_index(clean_company)
    card = _example_payload()["cards"][0]
    for fact in card["fact_basis"]:
        rid = fact["source_record_id"]
        assert RECORD_ID_RE.match(rid), f"{rid} 不符合 <row_id>_<年度> 格式"
        assert rid in idx, f"{rid} 无法在记录索引中回表"
        rec = idx[rid]
        assert fact["source_file"] == rec.source_file
        assert fact["statement"] == rec.statement
        assert int(fact["source_row"]) == rec.source_row
        assert fact["source_column"] == rec.period
        assert fact["metric_key"] == rec.metric_key
    assert "bs_2023" not in EXAMPLE_BLOCK


def test_example_operand_fact_ids_match_facts():
    card = _example_payload()["cards"][0]
    ids = {f["fact_id"] for f in card["fact_basis"]}
    assert set(card["calculation"]["operand_fact_ids"]) <= ids


def test_example_boundary_contains_disclaimer():
    card = _example_payload()["cards"][0]
    assert "不能据此认定财务造假" in card["conclusion_boundary"]


# ---------------------------------------------------------------- 组装后的 prompt
def test_prompt_lists_all_signal_types(clean_company):
    msgs = build_scan_messages("测试公司", "3", company_to_text(clean_company))
    system = msgs[0]["content"]
    for st in SIGNAL_TYPES:
        assert st in system, f"prompt 缺少枚举值 {st}"


def test_prompt_injects_formula_catalog(clean_company):
    msgs = build_scan_messages("测试公司", "3", company_to_text(clean_company))
    system = msgs[0]["content"]
    catalog = formula_catalog_text()
    assert catalog in system
    for fid in available_formula_ids():
        assert fid in system, f"prompt 未列出可用公式 {fid}"
    assert "{catalog}" not in system            # 模板占位符必须已被替换


def test_prompt_states_record_id_format(clean_company):
    system = build_scan_messages("测试公司", "3", company_to_text(clean_company))[0]["content"]
    assert "source_record_id" in system
    assert "BS_R03_2024" in system


def test_user_message_carries_data_text(clean_company):
    text = company_to_text(clean_company)
    msgs = build_scan_messages(clean_company.name, "3", text)
    assert msgs[1]["role"] == "user"
    assert text in msgs[1]["content"]
    assert clean_company.name in msgs[1]["content"]


# ---------------------------------------------------------------- 记录索引 / 文本一致性
def test_text_row_ids_match_record_index(clean_company):
    """模型看到的 row_id 必须与评估器回表用的 row_id 完全一致（D3 的地基）。"""
    text = company_to_text(clean_company)
    idx = build_record_index(clean_company)
    seen = set()
    for line in text.splitlines():
        m = re.match(r"^((?:IS|BS|CF)_R\d{2}),(\d+),", line)
        if not m:
            continue
        row_id, row_no = m.group(1), int(m.group(2))
        seen.add(row_id)
        assert row_id.endswith(f"R{row_no:02d}")
        for y in clean_company.years:
            rid = f"{row_id}_{y}"
            assert rid in idx, f"{rid} 出现在文本里却不在记录索引"
            assert idx[rid].source_row == row_no
    assert seen, "文本中未解析到任何 row_id"


def test_every_layout_metric_is_indexed(clean_company):
    idx = build_record_index(clean_company)
    for statement, keys in ROW_LAYOUT.items():
        table = clean_company.table(statement)
        for key in keys:
            assert key in table, f"{statement}.{key} 在布局中但数据缺失"
            for y in clean_company.years:
                assert record_id_of(statement, key, y) in idx


def test_row_ids_are_unique_and_stable():
    ids = [row_id_of(s, k) for s, keys in ROW_LAYOUT.items() for k in keys]
    assert len(ids) == len(set(ids)), "row_id 出现重复"
    assert all(i is not None for i in ids)
    # 固化几个已发布的 ID，防止后人往布局中间插行导致历史结果失效
    assert row_id_of("income", "revenue") == "IS_R02"
    assert row_id_of("balance", "accounts_receivable") == "BS_R03"
    assert row_id_of("cashflow", "cfo") == "CF_R02"


def test_row_id_of_unknown_metric_is_none():
    assert row_id_of("balance", "not_a_metric") is None
    assert record_id_of("balance", "not_a_metric", 2024) is None


def test_balance_sheet_identity_is_verifiable_from_visible_rows(clean_company):
    """模型可见的表格必须能自行核对「总资产 = 负债 + 权益」，否则会诱发无意义误报。"""
    visible = set(ROW_LAYOUT["balance"])
    assert {"total_assets", "current_liabilities", "non_current_liabilities",
            "equity"} <= visible
    assert {"fixed_assets", "non_current_assets"} <= visible
    for t in clean_company.years:
        b = clean_company.balance
        assert abs(b["total_assets"][t]
                   - (b["current_liabilities"][t] + b["non_current_liabilities"][t]
                      + b["equity"][t])) < 1.0
        assert abs(b["total_assets"][t]
                   - (b["current_assets"][t] + b["non_current_assets"][t])) < 1.0


def test_statement_meta_covers_all_statements():
    assert set(STATEMENT_META) == set(ROW_LAYOUT)
    for meta in STATEMENT_META.values():
        assert meta["source_file"].endswith(".csv")
