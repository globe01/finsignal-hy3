"""FinSignal-Hy3 交互 Demo（Streamlit）。

功能（对应交付要求「五、最小可用 Demo」）：
- 选择真实公司样本 / 上传 CSV·Excel / 表格录入 / 粘贴文本 → 调用 Hy3（腾讯云 TokenHub）扫描异常；
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
from pathlib import Path

import pandas as pd
import streamlit as st

from app.input_helpers import (
    METRIC_META,
    build_empty_template,
    dataframe_to_financials,
    financials_to_dataframe,
    financials_to_text,
    load_real_samples,
    parse_uploaded_file,
    sample_options,
)
from app.sample_data import SAMPLE
from app.schema import AnomalyCard, ScanOutput
from eval.fact_eval import D3_FIELDS
from eval.formula_eval import evaluate_d2
from eval.rule_rubric import judge_cards, judge_d7, judge_d8

ROOT = Path(__file__).resolve().parent.parent
LOGO_SVG = (ROOT / "app" / "assets" / "logo.svg").read_text(encoding="utf-8")

DISCLAIMER = (
    "本工具仅用于教育、研究与开源展示。输出不代表相关公司存在财务造假，"
    "不构成投资建议、交易建议或审计结论。异常信号仅表示「值得进一步核查」。"
)

SEVERITY_META = {
    "high": {"label": "高", "class": "sev-high", "color": "#ff5a5f"},
    "medium": {"label": "中", "class": "sev-medium", "color": "#d6a53f"},
    "low": {"label": "低", "class": "sev-low", "color": "#5dbb7b"},
}


def _svg_icon(name: str, size: int = 18) -> str:
    """返回标准 inline SVG 图标字符串（在 Streamlit 里用 unsafe_allow_html=True 渲染）。"""
    icons = {
        "scan": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
</svg>""",
        "database": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/>
  <path d="M3 12a9 3 0 0 0 18 0"/>
</svg>""",
        "upload": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/>
  <line x1="12" y1="3" x2="12" y2="15"/>
</svg>""",
        "table": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18"/><path d="M9 21V9"/>
</svg>""",
        "paste": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/>
  <rect x="8" y="2" width="8" height="4" rx="1"/>
</svg>""",
        "alert": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
  <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
</svg>""",
        "check": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="20 6 9 17 4 12"/>
</svg>""",
        "info": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/>
</svg>""",
        "trending": """
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/>
</svg>""",
    }
    svg = icons.get(name, icons["info"])
    return f'<span class="fs-icon" style="width:{size}px;height:{size}px">{svg}</span>'


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --fs-bg: #0d0f10;
            --fs-bg-soft: #111315;
            --fs-panel: #16191d;
            --fs-panel-2: #1e2025;
            --fs-panel-3: #242126;
            --fs-line: rgba(230, 224, 214, 0.11);
            --fs-line-strong: rgba(230, 224, 214, 0.20);
            --fs-text: #f2efe9;
            --fs-muted: #96938d;
            --fs-soft: #c8c1b7;
            --fs-red: #e2555c;
            --fs-red-deep: #a93f46;
            --fs-amber: #c8a45d;
            --fs-green: #74b59a;
            --fs-cyan: #72b8b2;
            --fs-blue: #8ba7d4;
            --fs-focus: rgba(116, 181, 154, 0.35);
        }

        html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] > .main {
            background: var(--fs-bg) !important;
            color: var(--fs-text);
        }

        [data-testid="stHeader"] {
            background: rgba(13, 15, 16, 0.94) !important;
            border-bottom: 1px solid var(--fs-line);
            backdrop-filter: blur(10px);
        }

        [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu, footer {
            visibility: hidden;
            height: 0;
        }

        [data-testid="stAppViewContainer"] > .main .block-container {
            max-width: 1240px;
            padding-top: 2.4rem;
            padding-bottom: 4rem;
        }

        [data-testid="stSidebar"] {
            background: #15181d;
            border-right: 1px solid var(--fs-line);
        }

        [data-testid="stSidebar"] > div:first-child {
            background: #15181d;
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
        [data-testid="stSidebar"] label,
        [data-testid="stSidebar"] span {
            color: var(--fs-soft);
        }

        h1, h2, h3 { letter-spacing: 0; color: var(--fs-text); }

        p, label, span, div[data-testid="stMarkdownContainer"] { color: var(--fs-soft); }

        /* tabs */
        [data-testid="stTabs"] [role="tablist"] {
            border-bottom: 1px solid var(--fs-line);
            gap: 0.2rem;
        }
        [data-testid="stTabs"] [role="tab"] {
            color: var(--fs-muted);
            font-weight: 600;
            font-size: 0.92rem;
            padding: 0.65rem 1rem;
            border-radius: 8px 8px 0 0;
        }
        [data-testid="stTabs"] [aria-selected="true"] {
            color: var(--fs-text);
            background: rgba(116, 181, 154, 0.10);
            border-bottom: 2px solid var(--fs-green);
        }

        /* metrics */
        div[data-testid="stMetric"] {
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.018));
            border: 1px solid var(--fs-line);
            border-radius: 8px;
            padding: 0.85rem 1rem;
        }
        div[data-testid="stMetric"] label { color: var(--fs-muted) !important; }
        div[data-testid="stMetricValue"] { color: var(--fs-text); font-size: 1.6rem; font-weight: 800; }

        /* buttons */
        .stButton > button,
        button[data-testid="baseButton-primary"] {
            border: 1px solid rgba(226, 85, 92, 0.62) !important;
            border-radius: 8px;
            background: linear-gradient(180deg, #e45a61 0%, #bb434b 100%) !important;
            color: #fff7f3 !important;
            font-weight: 700;
            min-height: 2.9rem;
            box-shadow: 0 10px 26px rgba(187, 67, 75, 0.24);
            transition: all 0.15s ease;
        }
        .stButton > button:hover,
        button[data-testid="baseButton-primary"]:hover {
            border-color: #f07a80 !important;
            transform: translateY(-1px);
            box-shadow: 0 14px 32px rgba(187, 67, 75, 0.30);
        }
        .stButton > button[kind="secondary"],
        button[data-testid="baseButton-secondary"],
        button[data-testid="baseButton-header"] {
            background: #202328 !important;
            border: 1px solid rgba(200, 164, 93, 0.30) !important;
            color: #efe4cf !important;
            box-shadow: none;
        }

        button[data-testid="baseButton-secondary"]:hover,
        button[data-testid="baseButton-header"]:hover {
            background: #29272a !important;
            border-color: rgba(116, 181, 154, 0.48) !important;
            color: #f4efe5 !important;
        }

        .stDownloadButton > button {
            background: #202328 !important;
            border: 1px solid rgba(116, 181, 154, 0.45) !important;
            border-radius: 8px;
            color: #d9f0e6 !important;
            font-weight: 700;
        }

        /* inputs */
        .stTextArea textarea, .stTextInput input, .stNumberInput input {
            background: #181b20 !important;
            border: 1px solid var(--fs-line-strong);
            border-radius: 8px;
            color: var(--fs-text) !important;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }
        .stTextArea textarea:focus, .stTextInput input:focus, .stNumberInput input:focus {
            border-color: rgba(116, 181, 154, 0.75);
            box-shadow: 0 0 0 1px var(--fs-focus);
        }

        .stTextInput input::placeholder, .stTextArea textarea::placeholder {
            color: rgba(200, 193, 183, 0.48) !important;
        }

        [data-baseweb="select"] > div {
            background: #181b20 !important;
            border: 1px solid var(--fs-line-strong) !important;
            border-radius: 8px !important;
            color: var(--fs-text) !important;
            min-height: 2.7rem;
        }
        [data-baseweb="select"] input, [data-baseweb="select"] span, [data-baseweb="select"] svg {
            color: var(--fs-text) !important;
            fill: var(--fs-soft) !important;
        }
        [data-baseweb="select"] div[aria-disabled="true"], [data-baseweb="select"] input::placeholder {
            color: rgba(200, 193, 183, 0.55) !important;
        }
        [data-baseweb="popover"], [data-baseweb="menu"] {
            background: #181b20 !important;
            border: 1px solid var(--fs-line-strong) !important;
            color: var(--fs-text) !important;
        }
        [role="option"] {
            background: #181b20 !important;
            color: var(--fs-text) !important;
        }
        [role="option"]:hover, [aria-selected="true"][role="option"] {
            background: rgba(116, 181, 154, 0.14) !important;
        }

        [data-testid="stFileUploader"] {
            background: rgba(255, 255, 255, 0.025);
            border: 1px dashed rgba(203, 209, 220, 0.30);
            border-radius: 8px;
            padding: 1.1rem;
        }

        [data-testid="stFileUploader"] section {
            background: transparent !important;
            border-color: rgba(116, 181, 154, 0.30) !important;
            color: var(--fs-soft) !important;
        }

        /* data editor */
        div[data-testid="stDataFrame"] { border: 1px solid var(--fs-line); border-radius: 8px; overflow: hidden; }

        /* custom components */
        .fs-icon { display: inline-flex; vertical-align: middle; color: currentColor; }
        .fs-icon svg { width: 100%; height: 100%; }

        .fs-topbar {
            display: flex;
            align-items: center;
            gap: 0.9rem;
            border-bottom: 1px solid var(--fs-line);
            padding-bottom: 1.2rem;
            margin-bottom: 1.6rem;
        }
        .fs-logo { width: 42px; height: 42px; color: var(--fs-red); flex-shrink: 0; }
        .fs-brand { font-size: 1.5rem; font-weight: 800; letter-spacing: 0; }
        .fs-tagline { color: var(--fs-muted); font-size: 0.9rem; margin-top: 0.15rem; }

        .fs-hero {
            background:
                linear-gradient(180deg, rgba(255,255,255,0.050) 0%, rgba(255,255,255,0.018) 100%),
                linear-gradient(115deg, rgba(116,181,154,0.10) 0%, rgba(169,63,70,0.11) 100%);
            border: 1px solid rgba(230, 224, 214, 0.12);
            border-radius: 8px;
            padding: 1.75rem 1.9rem;
            margin-bottom: 1.4rem;
        }
        .fs-hero-title {
            font-size: 1.9rem;
            font-weight: 800;
            margin: 0 0 0.45rem;
        }
        .fs-hero-sub {
            color: var(--fs-soft);
            font-size: 1rem;
            line-height: 1.65;
            max-width: 760px;
            margin: 0;
        }

        .fs-strip {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.85rem;
            margin-top: 1.2rem;
        }
        .fs-strip-item {
            background: rgba(13, 15, 16, 0.38);
            border: 1px solid var(--fs-line);
            border-radius: 8px;
            padding: 0.85rem 1rem;
        }
        .fs-strip-label { color: var(--fs-muted); font-size: 0.75rem; margin-bottom: 0.2rem; }
        .fs-strip-value { color: var(--fs-text); font-size: 1rem; font-weight: 700; }

        .fs-note {
            border: 1px solid rgba(214, 165, 63, 0.35);
            border-left: 4px solid var(--fs-amber);
            background: rgba(214, 165, 63, 0.11);
            border-radius: 8px;
            padding: 0.9rem 1rem;
            color: #f3e4bc;
            margin: 1.1rem 0;
        }

        .fs-panel-title {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 1rem;
            margin: 1.6rem 0 0.8rem;
        }
        .fs-panel-title h2 { font-size: 1.15rem; margin: 0; }
        .fs-panel-title span { color: var(--fs-muted); font-size: 0.88rem; }

        .fs-help {
            background: rgba(255,255,255,0.025);
            border: 1px solid var(--fs-line);
            border-radius: 8px;
            padding: 1rem 1.1rem;
            color: var(--fs-soft);
            font-size: 0.95rem;
            line-height: 1.7;
        }
        .fs-help h4 { margin: 0 0 0.5rem; color: var(--fs-text); }
        .fs-help ul { margin: 0.3rem 0 0; padding-left: 1.2rem; }

        .fs-sidebar-brand { padding: 0.2rem 0 0.9rem; border-bottom: 1px solid var(--fs-line); margin-bottom: 1rem; }
        .fs-sidebar-brand strong { display: block; color: var(--fs-text); font-size: 1.18rem; margin-bottom: 0.2rem; }
        .fs-sidebar-brand span { color: var(--fs-muted); font-size: 0.82rem; }

        .fs-side-row {
            display: flex; justify-content: space-between; gap: 0.75rem;
            border-bottom: 1px solid rgba(232, 236, 244, 0.08);
            padding: 0.55rem 0; font-size: 0.88rem;
        }
        .fs-side-row span:first-child { color: var(--fs-muted); }
        .fs-side-row span:last-child { color: var(--fs-text); text-align: right; overflow-wrap: anywhere; }

        .fs-pill {
            display: inline-flex; align-items: center; border-radius: 6px;
            padding: 0.2rem 0.6rem; font-size: 0.78rem; font-weight: 700;
            border: 1px solid var(--fs-line-strong); color: var(--fs-soft);
            background: rgba(255, 255, 255, 0.04); white-space: nowrap;
        }
        .fs-pill.sev-high { border-color: rgba(255, 90, 95, 0.55); color: #ffb5b8; background: rgba(255, 90, 95, 0.11); }
        .fs-pill.sev-medium { border-color: rgba(214, 165, 63, 0.55); color: #f0d391; background: rgba(214, 165, 63, 0.11); }
        .fs-pill.sev-low { border-color: rgba(93, 187, 123, 0.55); color: #b8e1c6; background: rgba(93, 187, 123, 0.11); }

        .fs-card-head {
            display: flex; justify-content: space-between; gap: 1rem; align-items: flex-start;
            border-bottom: 1px solid var(--fs-line); padding-bottom: 0.85rem; margin-bottom: 0.85rem;
        }
        .fs-card-title { font-size: 1.05rem; font-weight: 780; color: var(--fs-text); margin-bottom: 0.28rem; }
        .fs-card-meta { color: var(--fs-muted); font-size: 0.84rem; }
        .fs-card-body { color: var(--fs-soft); line-height: 1.68; margin-bottom: 0.65rem; }
        .fs-label { color: var(--fs-muted); font-size: 0.78rem; font-weight: 700; margin: 0.8rem 0 0.2rem; }
        .fs-callout {
            border: 1px solid rgba(93, 187, 123, 0.35); border-left: 4px solid var(--fs-green);
            border-radius: 8px; padding: 0.65rem 0.8rem; color: #c9ead4;
            background: rgba(93, 187, 123, 0.09); margin-top: 0.75rem;
        }

        [data-testid="stExpander"] {
            border: 1px solid var(--fs-line) !important;
            border-radius: 8px !important;
            background: rgba(255, 255, 255, 0.022) !important;
            color: var(--fs-soft) !important;
        }
        [data-testid="stExpander"] summary, [data-testid="stExpander"] summary p {
            color: var(--fs-soft) !important;
        }

        .fs-empty-state {
            text-align: center; color: var(--fs-muted); padding: 2.5rem 1rem;
            border: 1px dashed var(--fs-line); border-radius: 8px;
        }

        @media (max-width: 760px) {
            [data-testid="stAppViewContainer"] > .main .block-container { padding-top: 1.6rem; }
            .fs-strip { grid-template-columns: 1fr; }
            .fs-hero-title { font-size: 1.45rem; }
            .fs-card-head { display: block; }
            .fs-card-head .fs-pill { margin-top: 0.6rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    st.markdown(
        f"""
        <div class="fs-topbar">
          <div class="fs-logo">{LOGO_SVG}</div>
          <div>
            <div class="fs-brand">FinSignal Lab · Hy3</div>
            <div class="fs-tagline">上市公司财务异常信号扫描与审慎分析 Demo</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
        <div class="fs-hero">
          <h1 class="fs-hero-title">财务异常信号扫描</h1>
          <p class="fs-hero-sub">
            基于上市公司结构化财务数据，生成带事实依据、计算过程、替代解释和结论边界的审慎分析。
            支持选择真实公司样本、上传表格、在线录入或直接粘贴数据。
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
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="fs-note">{_svg_icon("alert", 18)} {DISCLAIMER}</div>', unsafe_allow_html=True)


def _render_sidebar() -> float:
    with st.sidebar:
        st.markdown(
            f"""
            <div class="fs-sidebar-brand">
              <div style="display:flex;align-items:center;gap:0.6rem;">
                <div style="width:28px;height:28px;color:#ff5a5f;">{LOGO_SVG}</div>
                <div>
                  <strong>FinSignal-Hy3</strong>
                  <span>财务异常信号扫描 Demo</span>
                </div>
              </div>
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

        with st.expander("数据从哪来？", expanded=False):
            st.markdown(
                """
                本工具需要**结构化财务数据**（连续 3 年的利润表、资产负债表、现金流量表字段）。来源可以是：

                1. **真实样本**：我们已预处理了 8 家制造业公司 2021-2025 年报，可直接选择。
                2. **下载模板**：点击「上传 CSV/Excel」页内的模板按钮，按行填写科目、按列填写年度。
                3. **手动录入**：在「表格录入」页直接输入数字，系统自动拼装成模型可读的文本。
                4. **粘贴文本**：高级用户可粘贴符合格式的文本（如年报摘要或导出的 CSV）。

                原始数据来自上市公司公开披露年报，本项目仅做教育与研究展示。
                """,
                unsafe_allow_html=True,
            )
        return temperature


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _init_session() -> None:
    """初始化 session_state，避免 tab 切换丢失当前输入。"""
    defaults = {
        "scan_company": "示例制造公司",
        "scan_years": "2022-2024",
        "scan_unit": "元",
        "scan_text": SAMPLE,
        "input_df": build_empty_template(["2022", "2023", "2024"]),
        "samples": load_real_samples(),
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _on_sample_change() -> None:
    label = st.session_state.get("selected_sample_label")
    opts = sample_options(st.session_state["samples"])
    sid = opts.get(label)
    if not sid:
        return
    s = st.session_state["samples"][sid]
    years = sorted(str(y) for y in s.get("input_financials", {}).keys())
    st.session_state["scan_company"] = s.get("company", sid)
    st.session_state["scan_years"] = "-".join(years)
    st.session_state["scan_unit"] = "元"
    st.session_state["input_df"] = financials_to_dataframe(s["input_financials"])
    st.session_state["scan_text"] = financials_to_text(
        st.session_state["scan_company"], st.session_state["scan_unit"], s["input_financials"]
    )


def _on_file_upload() -> None:
    up = st.session_state.get("uploaded_file")
    if up is None:
        return
    df = parse_uploaded_file(up)
    if df is None:
        st.toast("无法解析文件，请使用模板格式", icon="⚠️")
        return
    years = [c for c in df.columns if c not in {"metric_key", "metric_name"}]
    fin = dataframe_to_financials(df)
    st.session_state["scan_company"] = "上传公司"
    st.session_state["scan_years"] = "-".join(years) if years else "2022-2024"
    st.session_state["scan_unit"] = "元"
    st.session_state["input_df"] = df
    st.session_state["scan_text"] = financials_to_text(
        st.session_state["scan_company"], st.session_state["scan_unit"], fin
    )


def _render_sample_tab() -> None:
    st.markdown("从已结构化的 8 家制造业公司中选择窗口，自动填入对应财务数据。")
    opts = sample_options(st.session_state["samples"])
    st.selectbox(
        "选择示例公司与窗口",
        options=list(opts.keys()),
        index=None,
        placeholder="请选择示例公司与窗口",
        key="selected_sample_label",
        on_change=_on_sample_change,
        label_visibility="collapsed",
    )
    if st.session_state.get("selected_sample_label"):
        sid = opts[st.session_state["selected_sample_label"]]
        s = st.session_state["samples"][sid]
        c1, c2, c3 = st.columns(3)
        c1.metric("公司", s.get("company", ""))
        c2.metric("窗口", s.get("window_years", ""))
        c3.metric("状态", s.get("sample_status", ""))


def _render_upload_tab() -> None:
    st.markdown("下载模板，按年度填写后上传 CSV 或 Excel。")
    col1, col2 = st.columns([1, 2])
    with col1:
        template_path = ROOT / "data" / "derived" / "finsignal_input_template.csv"
        if template_path.exists():
            st.download_button(
                label="下载模板 CSV",
                data=template_path.read_bytes(),
                file_name="finsignal_input_template.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with col2:
        st.file_uploader(
            "上传 CSV / Excel",
            type=["csv", "xlsx", "xls"],
            key="uploaded_file",
            on_change=_on_file_upload,
            label_visibility="collapsed",
        )


def _render_table_tab() -> None:
    st.markdown("直接在线填写各年度财务数据，缺失年份留空即可。")
    edited = st.data_editor(
        st.session_state["input_df"],
        column_config={
            "metric_key": st.column_config.TextColumn("科目键", disabled=True, width="small"),
            "metric_name": st.column_config.TextColumn("科目名", disabled=True, width="medium"),
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key="financials_editor",
    )
    # 每次编辑后同步回文本
    fin = dataframe_to_financials(edited)
    st.session_state["scan_text"] = financials_to_text(
        st.session_state["scan_company"], st.session_state["scan_unit"], fin
    )


def _render_paste_tab() -> None:
    st.markdown("高级：直接粘贴符合格式的财务文本。")
    st.text_area(
        "财务数据文本",
        key="scan_text",
        height=360,
        label_visibility="collapsed",
    )


def _render_input_section() -> None:
    st.markdown(
        f'<div class="fs-panel-title"><h2>{_svg_icon("database", 18)} 输入数据</h2></div>',
        unsafe_allow_html=True,
    )

    tab_labels = [
        f"{_svg_icon('database', 16)} 选择样本",
        f"{_svg_icon('upload', 16)} 上传 CSV/Excel",
        f"{_svg_icon('table', 16)} 表格录入",
        f"{_svg_icon('paste', 16)} 粘贴文本",
    ]
    tabs = st.tabs(["选择样本", "上传 CSV/Excel", "表格录入", "粘贴文本"])
    with tabs[0]:
        _render_sample_tab()
    with tabs[1]:
        _render_upload_tab()
    with tabs[2]:
        _render_table_tab()
    with tabs[3]:
        _render_paste_tab()

    # 元信息行
    meta_cols = st.columns([2, 2, 2, 2])
    st.session_state["scan_company"] = meta_cols[0].text_input(
        "公司名", value=st.session_state["scan_company"], key="meta_company"
    )
    st.session_state["scan_years"] = meta_cols[1].text_input(
        "年度窗口", value=st.session_state["scan_years"], key="meta_years"
    )
    st.session_state["scan_unit"] = meta_cols[2].text_input(
        "金额单位", value=st.session_state["scan_unit"], key="meta_unit"
    )
    if meta_cols[3].button("重置为空模板", use_container_width=True, type="secondary"):
        st.session_state["scan_company"] = "示例公司"
        st.session_state["scan_years"] = "2022-2024"
        st.session_state["scan_unit"] = "元"
        st.session_state["input_df"] = build_empty_template(["2022", "2023", "2024"])
        st.session_state["scan_text"] = financials_to_text(
            st.session_state["scan_company"], st.session_state["scan_unit"], {}
        )
        st.rerun()

    # 预览
    with st.expander("查看当前将提交给模型的文本", expanded=False):
        st.text_area("文本预览", value=st.session_state["scan_text"], height=220, disabled=True)


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


def _run_scan(temperature: float) -> None:
    text = st.session_state["scan_text"]
    company = st.session_state["scan_company"]
    years = st.session_state["scan_years"]
    if not text.strip():
        st.error("请输入或上传财务数据。")
        return
    with st.spinner("正在调用 Hy3 扫描……"):
        try:
            from app.scan import scan_text
            out = scan_text(text, company=company, years=years, temperature=temperature)
        except RuntimeError as e:
            st.error(f"无法调用 Hy3：{e}\n\n请确认本地 `.env` 已配置 HY3_API_KEY，"
                      "或改用 `python -m eval.run_eval --offline` 体验离线自检。")
            return
        except Exception as e:  # noqa: BLE001
            st.error(f"扫描失败：{type(e).__name__}: {e}")
            return
    st.session_state["last_output"] = out


def _score_color(score: float | None) -> str:
    if score is None:
        return "var(--fs-muted)"
    if score >= 4:
        return "var(--fs-green)"
    if score >= 2.5:
        return "var(--fs-amber)"
    return "var(--fs-red)"


def _show_results(out: ScanOutput) -> None:
    cards = out.cards
    st.markdown(
        f'<div class="fs-panel-title"><h2>{_svg_icon("scan", 18)} 识别结果</h2><span>{len(cards)} 张信号卡片</span></div>',
        unsafe_allow_html=True,
    )
    if not cards:
        st.info("模型未识别到异常信号。阴性结果在评测中用于误报率（D5）观测。")
        return

    # ---- 校验面板（D1/D2/D3/D7/D8）----
    checks = _compute_checks(out)
    judge, d2, d1, d3 = checks["judge"], checks["d2"], checks["d1"], checks["d3"]

    st.markdown(f'<div class="fs-panel-title"><h3>{_svg_icon("check", 16)} 本地校验面板（D1/D2/D3/D7/D8）</h3></div>', unsafe_allow_html=True)
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

    # ---- 可视化摘要 ----
    st.markdown(f'<div class="fs-panel-title"><h3>{_svg_icon("trending", 16)} 信号摘要</h3></div>', unsafe_allow_html=True)
    sev_df = pd.DataFrame([{"signal_type": c.signal_type.value, "severity": c.severity.value,
                            "D7": judge_d7(c)[0]} for c in cards])
    col_chart1, col_chart2 = st.columns(2)
    with col_chart1:
        st.bar_chart(sev_df.groupby("severity").size().rename("数量"), color="#6aa8ff")
    with col_chart2:
        st.bar_chart(sev_df.set_index("signal_type")["D7"].rename("D7 评分"), color="#ff6469")

    # ---- 卡片展示 ----
    st.markdown(f'<div class="fs-panel-title"><h3>{_svg_icon("alert", 16)} 信号卡片</h3></div>', unsafe_allow_html=True)
    for i, card in enumerate(cards):
        with st.container(border=True):
            sev = card.severity.value
            sev_meta = SEVERITY_META.get(sev, {"label": sev.upper(), "class": "", "color": "#888"})
            title = card.signal_name or card.signal_type.value
            periods = "、".join(card.periods) if card.periods else "未标注期间"
            d7_score, d7_issues = judge_d7(card)
            score_color = _score_color(d7_score)
            st.markdown(
                f"""
                <div class="fs-card-head">
                  <div>
                    <div class="fs-card-title">{i + 1}. {_html_escape(title)}</div>
                    <div class="fs-card-meta">{_html_escape(card.signal_type.value)} · {_html_escape(periods)} · D7={d7_score}/5</div>
                  </div>
                  <span class="fs-pill {sev_meta['class']}">风险等级 {_html_escape(sev_meta['label'])}</span>
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
                    st.caption(f"formula_id={card.calculation.formula_id} · reported_result={card.calculation.reported_result}")

    st.divider()
    _render_disclaimer()
    st.caption("© 犀牛鸟开源活动个人参赛作品，非腾讯/混元官方发布。")


def _render_disclaimer() -> None:
    st.markdown(f'<div class="fs-note">{_svg_icon("alert", 18)} {DISCLAIMER}</div>', unsafe_allow_html=True)


def main() -> None:
    st.set_page_config(page_title="FinSignal-Hy3 Demo", layout="wide", page_icon="🔍")
    _inject_css()
    _init_session()
    _render_header()
    temperature = _render_sidebar()
    _render_input_section()

    run_col, _ = st.columns([1, 4])
    if run_col.button(f"运行扫描", type="primary", use_container_width=True):
        _run_scan(temperature)

    if "last_output" in st.session_state:
        _show_results(st.session_state["last_output"])


if __name__ == "__main__":
    main()
