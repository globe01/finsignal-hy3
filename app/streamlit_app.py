"""FinSignal-Hy3 最小可用交互 Demo（Streamlit）。

功能（对应交付要求「五、最小可用 Demo」）：
- 上传 CSV 或粘贴结构化财务文本 → 调用 Hy3（腾讯云 TokenHub）扫描异常；
- 以卡片形式展示信号（严重度 / 类型 / 事实依据 / 计算过程 / 替代解释 / 结论边界）；
- 本地运行 D1（数值结构）/ D2（公式）/ D3（证据字段完整）/ D8（安全合规）校验，
  并展示 D7 规则 Rubric 评分；
- 显著展示免责声明。

设计要点：
- **不在代码中硬编码任何 API Key**；密钥仅来自本地 `.env`（`app/llm.Hy3Client` 读取），
  缺失时给出友好提示而非崩溃。
- D1/D3 的「完整回表」需要结构化源数据集（见 `eval.run_eval` 离线评测）；
  本 Demo 对自由粘贴/上传文本只能做**字段完整性与结构**层面的校验，并明确标注这一限制，
  不把结构性检查冒充为可追溯性真值。
- 本 Demo 仅用于交互演示；批量、可复核评测请走 `python -m eval.run_eval`。

启动：streamlit run app/streamlit_app.py
"""
from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from app.scan import SAMPLE
from app.schema import AnomalyCard, ScanOutput
from eval.fact_eval import D3_FIELDS
from eval.formula_eval import evaluate_d2
from eval.rule_rubric import judge_cards, judge_d7, judge_d8

DISCLAIMER = (
    "本工具仅用于教育、研究与开源展示。输出不代表相关公司存在财务造假，"
    "不构成投资建议、交易建议或审计结论。异常信号仅表示「值得进一步核查」。"
)


def _render_facts_table(card: AnomalyCard) -> pd.DataFrame:
    rows = []
    for f in card.fact_basis:
        rows.append({
            "字段": f.metric_name or f.metric_key or "",
            "期间": f.period or "",
            "数值": f.value if f.value is not None else "",
            "单位": f.unit or "",
            "source_record_id": f.source_record_id or "",
            "源文件/行/列": f"{f.source_file or ''} / {f.source_row or ''} / {f.source_column or ''}",
            "报表": f.statement or "",
        })
    return pd.DataFrame(rows)


def _structural_d3(cards: list[AnomalyCard]) -> dict:
    """对自由文本输入的结构性 D3 检查：统计 10 个证据字段的填写完整度。
    不做回表匹配（无结构化源数据集），仅报告字段层面完整性。"""
    field_filled: dict[str, int] = {f: 0 for f in D3_FIELDS}
    total_facts = 0
    all_ten = 0
    per_card = []
    for card in cards:
        cf_filled = 0
        cf_total = len(card.fact_basis)
        card_filled: dict[str, int] = {f: 0 for f in D3_FIELDS}
        for f in card.fact_basis:
            total_facts += 1
            filled_here = 0
            for k in D3_FIELDS:
                val = getattr(f, k, None)
                if val not in (None, "", []):
                    field_filled[k] += 1
                    card_filled[k] += 1
                    filled_here += 1
            if filled_here == len(D3_FIELDS):
                all_ten += 1
                cf_filled += 1
        per_card.append({"signal_type": card.signal_type.value,
                         "n_facts": cf_total, "all_ten_fields": cf_filled})
    return {
        "total_facts": total_facts,
        "all_ten_fields": all_ten,
        "field_fill_rate": {k: (field_filled[k] / total_facts if total_facts else None)
                            for k in D3_FIELDS},
        "per_card": per_card,
    }


def _structural_d1(cards: list[AnomalyCard]) -> dict:
    total = 0
    with_value = 0
    for card in cards:
        for f in card.fact_basis:
            total += 1
            if f.value is not None:
                with_value += 1
    return {"total": total, "with_value": with_value,
            "rate": (with_value / total) if total else None}


def main() -> None:
    st.set_page_config(page_title="FinSignal-Hy3 Demo", layout="wide")
    st.title("FinSignal-Hy3 · 财务异常信号扫描 Demo")
    st.caption("基于混元 Hy3（腾讯云 TokenHub）的上市公司财务异常信号识别与漏报敏感型评估")

    st.warning(DISCLAIMER, icon="⚠️")

    with st.sidebar:
        st.header("配置")
        base_url = os.getenv("HY3_BASE_URL", "（默认 TokenHub）")
        model = os.getenv("HY3_MODEL", "hy3")
        st.text(f"模型：{model}  | endpoint：{base_url}")
        temperature = st.slider("temperature", 0.0, 1.0, 0.0, 0.05,
                                help="评测默认 0；演示可用稍高值以增加多样性")
        st.divider()
        st.caption("API Key 取自本地 `.env`（HY3_API_KEY），不在代码中硬编码、不入库。")

    mode = st.radio("输入方式", ["粘贴文本", "上传 CSV"], horizontal=True)
    text = ""
    if mode == "粘贴文本":
        text = st.text_area("财务数据（建议用示例格式：利润表/资产负债表/现金流量表 + 年度列）",
                            value=SAMPLE, height=320)
    else:
        up = st.file_uploader("上传 CSV（将作为财务文本原文送入模型）", type=["csv"])
        if up is not None:
            raw = up.getvalue().decode("utf-8", errors="replace")
            text = raw
            st.text_area("CSV 预览", value=raw[:2000], height=200, disabled=True)

    if st.button("运行扫描", type="primary"):
        if not text.strip():
            st.error("请输入或上传财务数据。")
            return
        with st.spinner("正在调用 Hy3 扫描……"):
            try:
                from app.scan import scan_text
                out = scan_text(text, temperature=temperature)
            except RuntimeError as e:
                st.error(f"无法调用 Hy3：{e}\n\n请确认本地 `.env` 已配置 HY3_API_KEY，"
                          "或改用 `python -m eval.run_eval --offline` 体验离线自检。")
                return
            except Exception as e:  # noqa: BLE001
                st.error(f"扫描失败：{type(e).__name__}: {e}")
                return

        _show_results(out)


def _show_results(out: ScanOutput) -> None:
    cards = out.cards
    st.header(f"识别结果：{len(cards)} 张卡片")
    if not cards:
        st.info("模型未识别到异常信号。阴性结果在评测中用于误报率（D5）观测。")
        return

    # ---- 校验面板（D1/D2/D3/D7/D8）----
    with st.expander("📐 本地校验（D1/D2/D3/D7/D8）", expanded=True):
        judge = judge_cards(cards)
        d2 = evaluate_d2(cards, company=None)
        d1 = _structural_d1(cards)
        d3 = _structural_d3(cards)

        c1, c2, c3, c4 = st.columns(4)
        d7_mean = (judge["d7_sum"] / judge["n_cards"]) if judge.get("n_cards") else None
        d8_viol = judge.get("d8_violations", 0)
        c1.metric("D7 规则 Rubric（均分/5）",
                  f"{d7_mean:.2f}" if d7_mean is not None else "—")
        c2.metric("D8 安全合规率",
                  f"{(1 - d8_viol / judge['n_cards']) * 100:.0f}%" if judge.get("n_cards") else "—")
        c3.metric("D2 公式可校验率",
                  f"{d2['d2_hits']}/{d2['d2_total']}" if d2.get("d2_total") else "无公式")
        c4.metric("D1 数值字段填充率",
                  f"{d1['rate'] * 100:.0f}%" if d1["rate"] is not None else "无 fact")

        st.caption(
            "D7/D8 为确定性规则 Rubric（启发式，套话可满分，非质量真值）；"
            "D2 仅校验公式已知/信号匹配/操作数/期间，算术复算需结构化源数据（本 Demo 自由文本为 N/A）；"
            "D1/D3 此处为**字段结构完整性**检查，完整回表可追溯性请见 `eval.run_eval` 离线评测。"
        )

        # D3 字段完整度
        if d3["total_facts"]:
            st.write("**D3 证据字段填写完整度（10 字段）**")
            fdf = pd.DataFrame(
                [{"字段": k, "填写率": (f"{v * 100:.0f}%" if v is not None else "—")}
                 for k, v in d3["field_fill_rate"].items()])
            st.dataframe(fdf, use_container_width=True, hide_index=True)
            st.write(f"全部 10 字段齐全的 fact：{d3['all_ten_fields']}/{d3['total_facts']}")

    # ---- 卡片展示 ----
    for i, card in enumerate(cards):
        with st.container(border=True):
            col_a, col_b = st.columns([1, 4])
            with col_a:
                sev = card.severity.value
                color = {"high": "🔴", "medium": "🟠", "low": "🟡"}.get(sev, "⚪")
                st.markdown(f"**{color} {sev.upper()}**")
                st.markdown(f"`{card.signal_type.value}`")
                if card.signal_name:
                    st.caption(card.signal_name)
                if card.periods:
                    st.caption("期间：" + ", ".join(card.periods))
            with col_b:
                if card.supported_explanation:
                    st.write(card.supported_explanation)
                if card.possible_explanations:
                    st.markdown("**替代解释(假设)**：" + "；".join(card.possible_explanations))
                if card.next_checks:
                    st.markdown("**建议核查**：" + "；".join(card.next_checks))
                if card.conclusion_boundary:
                    st.success(f"边界：{card.conclusion_boundary}")
            if card.fact_basis:
                with st.expander("事实依据 fact_basis", expanded=False):
                    st.dataframe(_render_facts_table(card), use_container_width=True, hide_index=True)
            if card.calculation:
                with st.expander("计算过程 calculation", expanded=False):
                    st.write(card.calculation.readable or "")
                    st.caption(f"formula_id={card.calculation.formula_id} · "
                               f"reported_result={card.calculation.reported_result}")
            # 单卡 D7/D8 明细
            with st.expander("本卡 D7/D8 规则校验", expanded=False):
                d7_score, d7_issues = judge_d7(card)
                d8_viol, d8_reasons = judge_d8(card)
                st.write(f"D7 评分：{d7_score} / 5 · 问题：{d7_issues or '无'}")
                st.write(f"D8 违规：{'是' if d8_viol else '否'}"
                         f"{(' — ' + '; '.join(d8_reasons)) if d8_viol else ''}")

    st.divider()
    st.warning(DISCLAIMER, icon="⚠️")
    st.caption("© 犀牛鸟开源活动个人参赛作品，非腾讯/混元官方发布。")


if __name__ == "__main__":
    main()
