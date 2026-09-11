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

from app.sample_data import SAMPLE
from app.schema import AnomalyCard, ScanOutput
from eval.fact_eval import D3_FIELDS
from eval.formula_eval import evaluate_d2
from eval.rule_rubric import judge_cards, judge_d7, judge_d8

DISCLAIMER = (
    "本工具仅用于教育、研究与开源展示。输出不代表相关公司存在财务造假，"
    "不构成投资建议、交易建议或审计结论。异常信号仅表示「值得进一步核查」。"
)

SEVERITY_META = {
    "high": {"label": "高", "class": "sev-high"},
    "medium": {"label": "中", "class": "sev-medium"},
    "low": {"label": "低", "class": "sev-low"},
}


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --fs-bg: #0e1117;
            --fs-panel: #171a22;
            --fs-panel-2: #20242f;
            --fs-line: rgba(232, 236, 244, 0.11);
            --fs-line-strong: rgba(232, 236, 244, 0.18);
            --fs-text: #f4f6fb;
            --fs-muted: #9aa3b2;
            --fs-soft: #cbd1dc;
            --fs-red: #ff5a5f;
            --fs-amber: #d6a53f;
            --fs-green: #5dbb7b;
            --fs-cyan: #7cc7d8;
        }

        .stApp {
            background:
                linear-gradient(180deg, #11141b 0%, #0d1016 46%, #0b0e13 100%);
            color: var(--fs-text);
        }

        [data-testid="stAppViewContainer"] > .main .block-container {
            max-width: 1180px;
            padding-top: 4.2rem;
            padding-bottom: 4rem;
        }

        [data-testid="stSidebar"] {
            background: #171a22;
            border-right: 1px solid var(--fs-line);
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] span {
            color: var(--fs-soft);
        }

        h1, h2, h3 {
            letter-spacing: 0;
        }

        div[data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.035);
            border: 1px solid var(--fs-line);
            border-radius: 8px;
            padding: 0.75rem 0.9rem;
        }

        div[data-testid="stMetric"] label {
            color: var(--fs-muted) !important;
        }

        div[data-testid="stMetricValue"] {
            color: var(--fs-text);
            font-size: 1.55rem;
        }

        .stButton > button {
            border: 1px solid rgba(255, 90, 95, 0.65);
            border-radius: 8px;
            background: linear-gradient(180deg, #ff6469 0%, #e94950 100%);
            color: white;
            font-weight: 700;
            min-height: 2.7rem;
            box-shadow: 0 8px 24px rgba(233, 73, 80, 0.22);
        }

        .stButton > button:hover {
            border-color: #ff8a8e;
            color: white;
            transform: translateY(-1px);
        }

        .stTextArea textarea {
            background: #1a1d26;
            border: 1px solid var(--fs-line-strong);
            border-radius: 8px;
            color: #eef2f8;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
            line-height: 1.48;
        }

        .stTextArea textarea:focus,
        .stTextInput input:focus {
            border-color: rgba(124, 199, 216, 0.72);
            box-shadow: 0 0 0 1px rgba(124, 199, 216, 0.18);
        }

        [data-testid="stFileUploader"] {
            background: rgba(255, 255, 255, 0.035);
            border: 1px dashed rgba(203, 209, 220, 0.28);
            border-radius: 8px;
            padding: 1rem;
        }

        .fs-hero {
            border-bottom: 1px solid var(--fs-line);
            padding: 0.35rem 0 1.45rem;
            margin-bottom: 1.2rem;
        }

        .fs-kicker {
            display: inline-flex;
            align-items: center;
            gap: 0.45rem;
            color: var(--fs-cyan);
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0;
            margin-bottom: 0.65rem;
        }

        .fs-title {
            font-size: 4rem;
            line-height: 0.96;
            font-weight: 800;
            letter-spacing: 0;
            margin: 0 0 0.75rem;
            max-width: 980px;
        }

        .fs-subtitle {
            color: var(--fs-soft);
            font-size: 1.04rem;
            line-height: 1.72;
            max-width: 820px;
            margin: 0;
        }

        .fs-strip {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 1.25rem 0 0;
        }

        .fs-strip-item {
            border: 1px solid var(--fs-line);
            border-radius: 8px;
            padding: 0.82rem 0.95rem;
            background: rgba(255, 255, 255, 0.032);
        }

        .fs-strip-label {
            color: var(--fs-muted);
            font-size: 0.78rem;
            margin-bottom: 0.2rem;
        }

        .fs-strip-value {
            color: var(--fs-text);
            font-size: 1rem;
            font-weight: 700;
        }

        .fs-note {
            border: 1px solid rgba(214, 165, 63, 0.38);
            border-left: 4px solid var(--fs-amber);
            background: rgba(214, 165, 63, 0.12);
            border-radius: 8px;
            padding: 0.78rem 0.9rem;
            color: #f3e4bc;
            margin: 1.1rem 0 1.1rem;
        }

        .fs-panel-title {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            margin: 1.2rem 0 0.65rem;
        }

        .fs-panel-title h2 {
            font-size: 1.08rem;
            margin: 0;
        }

        .fs-panel-title span {
            color: var(--fs-muted);
            font-size: 0.86rem;
        }

        .fs-sidebar-brand {
            padding: 0.35rem 0 0.85rem;
            border-bottom: 1px solid var(--fs-line);
            margin-bottom: 1rem;
        }

        .fs-sidebar-brand strong {
            display: block;
            color: var(--fs-text);
            font-size: 1.18rem;
            margin-bottom: 0.2rem;
        }

        .fs-sidebar-brand span {
            color: var(--fs-muted);
            font-size: 0.82rem;
        }

        .fs-side-row {
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            border-bottom: 1px solid rgba(232, 236, 244, 0.08);
            padding: 0.55rem 0;
            font-size: 0.88rem;
        }

        .fs-side-row span:first-child {
            color: var(--fs-muted);
        }

        .fs-side-row span:last-child {
            color: var(--fs-text);
            text-align: right;
            overflow-wrap: anywhere;
        }

        .fs-pill {
            display: inline-flex;
            align-items: center;
            border-radius: 6px;
            padding: 0.18rem 0.55rem;
            font-size: 0.76rem;
            font-weight: 700;
            border: 1px solid var(--fs-line-strong);
            color: var(--fs-soft);
            background: rgba(255, 255, 255, 0.04);
            white-space: nowrap;
        }

        .fs-pill.sev-high {
            border-color: rgba(255, 90, 95, 0.55);
            color: #ffb5b8;
            background: rgba(255, 90, 95, 0.11);
        }

        .fs-pill.sev-medium {
            border-color: rgba(214, 165, 63, 0.55);
            color: #f0d391;
            background: rgba(214, 165, 63, 0.11);
        }

        .fs-pill.sev-low {
            border-color: rgba(93, 187, 123, 0.55);
            color: #b8e1c6;
            background: rgba(93, 187, 123, 0.11);
        }

        .fs-card-head {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: flex-start;
            border-bottom: 1px solid var(--fs-line);
            padding-bottom: 0.78rem;
            margin-bottom: 0.78rem;
        }

        .fs-card-title {
            font-size: 1.02rem;
            font-weight: 780;
            color: var(--fs-text);
            margin-bottom: 0.28rem;
        }

        .fs-card-meta {
            color: var(--fs-muted);
            font-size: 0.84rem;
        }

        .fs-card-body {
            color: var(--fs-soft);
            line-height: 1.68;
            margin-bottom: 0.65rem;
        }

        .fs-label {
            color: var(--fs-muted);
            font-size: 0.78rem;
            font-weight: 700;
            margin: 0.7rem 0 0.18rem;
        }

        .fs-callout {
            border: 1px solid rgba(93, 187, 123, 0.35);
            border-left: 4px solid var(--fs-green);
            border-radius: 8px;
            padding: 0.62rem 0.75rem;
            color: #c9ead4;
            background: rgba(93, 187, 123, 0.09);
            margin-top: 0.72rem;
        }

        [data-testid="stExpander"] {
            border: 1px solid var(--fs-line) !important;
            border-radius: 8px !important;
            background: rgba(255, 255, 255, 0.025);
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--fs-line);
            border-radius: 8px;
            overflow: hidden;
        }

        @media (max-width: 760px) {
            [data-testid="stAppViewContainer"] > .main .block-container {
                padding-top: 2.2rem;
            }
            .fs-strip {
                grid-template-columns: 1fr;
            }
            .fs-title {
                font-size: 2.28rem;
            }
            .fs-card-head {
                display: block;
            }
            .fs-card-head .fs-pill {
                margin-top: 0.6rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    st.markdown(
        """
        <section class="fs-hero">
          <div class="fs-kicker">FinSignal Lab · Hy3</div>
          <h1 class="fs-title">财务异常信号扫描</h1>
          <p class="fs-subtitle">
            基于上市公司结构化财务数据，生成带事实依据、计算过程、替代解释和结论边界的审慎分析。
          </p>
          <div class="fs-strip">
            <div class="fs-strip-item">
              <div class="fs-strip-label">Model</div>
              <div class="fs-strip-value">Hy3 / TokenHub</div>
            </div>
            <div class="fs-strip-item">
              <div class="fs-strip-label">Evidence</div>
              <div class="fs-strip-value">Fact Basis + Formula</div>
            </div>
            <div class="fs-strip-item">
              <div class="fs-strip-label">Boundary</div>
              <div class="fs-strip-value">Research Demo Only</div>
            </div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_disclaimer() -> None:
    st.markdown(f'<div class="fs-note">{DISCLAIMER}</div>', unsafe_allow_html=True)


def _render_sidebar() -> float:
    with st.sidebar:
        st.markdown(
            """
            <div class="fs-sidebar-brand">
              <strong>FinSignal-Hy3</strong>
              <span>财务异常信号扫描 Demo</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        base_url = os.getenv("HY3_BASE_URL", "默认 TokenHub")
        model = os.getenv("HY3_MODEL", "hy3")
        key_state = "已配置" if os.getenv("HY3_API_KEY") else "未配置"
        st.markdown(
            f"""
            <div class="fs-side-row"><span>模型</span><span>{_html_escape(model)}</span></div>
            <div class="fs-side-row"><span>API Key</span><span>{_html_escape(key_state)}</span></div>
            <div class="fs-side-row"><span>Endpoint</span><span>{_html_escape(base_url)}</span></div>
            """,
            unsafe_allow_html=True,
        )
        temperature = st.slider("temperature", 0.0, 1.0, 0.0, 0.05,
                                help="评测默认 0；演示可用稍高值以增加多样性")
        st.caption("密钥仅从本地 `.env` 读取，不在代码中硬编码、不入库。")
        return temperature


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
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
    _inject_css()
    _render_hero()
    _render_disclaimer()
    temperature = _render_sidebar()

    st.markdown(
        '<div class="fs-panel-title"><h2>输入数据</h2><span>利润表 / 资产负债表 / 现金流量表 + 年度列</span></div>',
        unsafe_allow_html=True,
    )
    mode = st.radio("输入方式", ["粘贴文本", "上传 CSV"], horizontal=True, label_visibility="collapsed")
    text = ""
    if mode == "粘贴文本":
        text = st.text_area("财务数据", value=SAMPLE, height=360, label_visibility="collapsed")
    else:
        up = st.file_uploader("上传 CSV", type=["csv"], label_visibility="collapsed")
        if up is not None:
            raw = up.getvalue().decode("utf-8", errors="replace")
            text = raw
            st.text_area("CSV 预览", value=raw[:2000], height=220, disabled=True)

    run_col, hint_col = st.columns([1, 3], vertical_alignment="center")
    run_clicked = run_col.button("运行扫描", type="primary", use_container_width=True)
    hint_col.caption("当前输入将作为单次扫描样本。")
    if run_clicked:
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


def _compute_checks(out: ScanOutput) -> dict:
    """Demo 校验面板（D1/D2/D3/D7/D8）的纯计算逻辑，不依赖 streamlit，便于测试。

    D2 在自由文本 / 未解析 CSV 场景（无结构化 Company）下只做「公式结构检查」
    （公式已知 / 信号匹配 / 操作数齐备），期间匹配与算术复算记为 N/A，不会崩溃。
    """
    cards = out.cards
    judge = judge_cards(cards)
    d2 = evaluate_d2(cards, company=None)
    d1 = _structural_d1(cards)
    d3 = _structural_d3(cards)
    return {"judge": judge, "d2": d2, "d1": d1, "d3": d3}


def _show_results(out: ScanOutput) -> None:
    cards = out.cards
    st.markdown(
        f'<div class="fs-panel-title"><h2>识别结果</h2><span>{len(cards)} 张信号卡片</span></div>',
        unsafe_allow_html=True,
    )
    if not cards:
        st.info("模型未识别到异常信号。阴性结果在评测中用于误报率（D5）观测。")
        return

    # ---- 校验面板（D1/D2/D3/D7/D8）----
    with st.expander("本地校验 D1/D2/D3/D7/D8", expanded=True):
        checks = _compute_checks(out)
        judge, d2, d1, d3 = checks["judge"], checks["d2"], checks["d1"], checks["d3"]

        c1, c2, c3, c4 = st.columns(4)
        d7_mean = (judge["d7_sum"] / judge["n_cards"]) if judge.get("n_cards") else None
        d8_viol = judge.get("d8_violations", 0)
        c1.metric("D7 规则 Rubric（均分/5）",
                  f"{d7_mean:.2f}" if d7_mean is not None else "—")
        c2.metric("D8 安全合规率",
                  f"{(1 - d8_viol / judge['n_cards']) * 100:.0f}%" if judge.get("n_cards") else "—")
        if d2.get("d2_mode") == "full":
            c3.metric("D2 公式可校验率",
                      f"{d2['d2_hits']}/{d2['d2_total']}" if d2.get("d2_total") else "无公式")
        else:
            c3.metric("D2 公式结构通过率",
                      f"{d2['d2_struct_pass']}/{d2['d2_struct_total']}"
                      if d2.get("d2_struct_total") else "无公式")
        c4.metric("D1 数值字段填充率",
                  f"{d1['rate'] * 100:.0f}%" if d1["rate"] is not None else "无 fact")

        st.caption(
            "D7/D8 为确定性规则 Rubric（启发式，套话可满分，非质量真值）；"
            "D2 自由文本模式仅做**公式结构检查**（公式已知 / 信号匹配 / 操作数齐备），"
            "期间匹配与算术复算需结构化源数据（本 Demo 为 N/A）；"
            "D1/D3 此处为**字段结构完整性**检查，完整回表可追溯性请见 `eval.run_eval` 离线评测。"
        )

        # D3 字段完整度
        if d3["total_facts"]:
            st.markdown("**D3 证据字段填写完整度（10 字段）**")
            fdf = pd.DataFrame(
                [{"字段": k, "填写率": (f"{v * 100:.0f}%" if v is not None else "—")}
                 for k, v in d3["field_fill_rate"].items()])
            st.dataframe(fdf, use_container_width=True, hide_index=True)
            st.write(f"全部 10 字段齐全的 fact：{d3['all_ten_fields']}/{d3['total_facts']}")

    # ---- 卡片展示 ----
    for i, card in enumerate(cards):
        with st.container(border=True):
            sev = card.severity.value
            sev_meta = SEVERITY_META.get(sev, {"label": sev.upper(), "class": ""})
            sev_class = sev_meta["class"]
            sev_label = sev_meta["label"]
            title = card.signal_name or card.signal_type.value
            periods = "、".join(card.periods) if card.periods else "未标注期间"
            st.markdown(
                f"""
                <div class="fs-card-head">
                  <div>
                    <div class="fs-card-title">{i + 1}. {_html_escape(title)}</div>
                    <div class="fs-card-meta">{_html_escape(card.signal_type.value)} · {_html_escape(periods)}</div>
                  </div>
                  <span class="fs-pill {sev_class}">风险等级 {_html_escape(sev_label)}</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if card.supported_explanation:
                st.markdown(
                    f'<div class="fs-card-body">{_html_escape(card.supported_explanation)}</div>',
                    unsafe_allow_html=True,
                )
            if card.possible_explanations:
                st.markdown('<div class="fs-label">替代解释</div>', unsafe_allow_html=True)
                st.markdown("；".join(card.possible_explanations))
            if card.next_checks:
                st.markdown('<div class="fs-label">建议核查</div>', unsafe_allow_html=True)
                st.markdown("；".join(card.next_checks))
            if card.conclusion_boundary:
                st.markdown(
                    f'<div class="fs-callout">边界：{_html_escape(card.conclusion_boundary)}</div>',
                    unsafe_allow_html=True,
                )
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
    _render_disclaimer()
    st.caption("© 犀牛鸟开源活动个人参赛作品，非腾讯/混元官方发布。")


if __name__ == "__main__":
    main()
