"""Streamlit Demo 输入侧辅助：把结构化财务数据转换为 scan_text 可识别的文本格式。

避免在 streamlit_app.py 中堆积转换逻辑；本模块**不依赖 openai / Hy3**，纯本地工具。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "data" / "derived" / "real_eval_samples.jsonl"
TEMPLATE_PATH = ROOT / "data" / "derived" / "finsignal_input_template.csv"

METRIC_META: dict[str, dict] = {
    "revenue": {"label": "营业收入", "statement": "income", "category": "利润表"},
    "cogs": {"label": "营业成本", "statement": "income", "category": "利润表"},
    "net_profit": {"label": "净利润", "statement": "income", "category": "利润表"},
    "nonrecurring": {"label": "非经常性损益", "statement": "income", "category": "利润表"},
    "cash": {"label": "货币资金", "statement": "balance", "category": "资产负债表"},
    "accounts_receivable": {"label": "应收账款", "statement": "balance", "category": "资产负债表"},
    "inventory": {"label": "存货", "statement": "balance", "category": "资产负债表"},
    "goodwill": {"label": "商誉", "statement": "balance", "category": "资产负债表"},
    "current_assets": {"label": "流动资产", "statement": "balance", "category": "资产负债表"},
    "current_liabilities": {"label": "流动负债", "statement": "balance", "category": "资产负债表"},
    "short_borrow": {"label": "短期借款", "statement": "balance", "category": "资产负债表"},
    "equity": {"label": "所有者权益", "statement": "balance", "category": "资产负债表"},
    "cfo": {"label": "经营活动现金流量净额", "statement": "cashflow", "category": "现金流量表"},
}

STATEMENT_ORDER = ["利润表", "资产负债表", "现金流量表"]


def load_real_samples(path: Path | str = SAMPLES_PATH) -> dict[str, dict]:
    """加载真实评测样本，返回 {sample_id: sample_record}。"""
    p = Path(path)
    if not p.exists():
        return {}
    return {
        json.loads(line)["sample_id"]: json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def format_display_value(value) -> str:
    """Return a scalar display string for Streamlit labels and metrics."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [str(item) for item in value if item is not None and str(item) != ""]
        try:
            years = [int(item) for item in parts]
        except ValueError:
            return ", ".join(parts)
        if years and years == list(range(years[0], years[-1] + 1)):
            return f"{years[0]}-{years[-1]}" if len(years) > 1 else str(years[0])
        return ", ".join(parts)
    return str(value)


def sample_options(samples: dict[str, dict] | None = None) -> dict[str, str]:
    """返回 {展示文本: sample_id}，供 st.selectbox 使用。"""
    if samples is None:
        samples = load_real_samples()
    opts = {}
    for sid, s in samples.items():
        years = format_display_value(s.get("window_years", ""))
        status = format_display_value(s.get("sample_status", ""))
        company = format_display_value(s.get("company", sid))
        opts[f"{company} · {years} · {status}"] = sid
    return opts


def financials_to_text(company: str, unit: str, input_financials: dict) -> str:
    """把 {year: {metric_key: value}} 转成 scan_text 接受的类 CSV 文本格式。

    与 `scripts/run_hy3_real_samples.py::build_prompt` 中的 JSON 块是两种等价输入形式：
    JSON 块更利于模型直接解析；文本表格更贴近财报披露形态。Demo 统一用文本表格，
    便于用户肉眼核对。
    """
    years = sorted(str(y) for y in input_financials.keys())
    by_cat = {cat: [k for k, m in METRIC_META.items() if m["category"] == cat]
              for cat in STATEMENT_ORDER}

    lines = [f"公司：{company}", f"单位：{unit}", ""]
    for cat in STATEMENT_ORDER:
        lines.append(f"{cat}：")
        lines.append(f"年度,{','.join(years)}")
        for key in by_cat[cat]:
            label = METRIC_META[key]["label"]
            vals = []
            for y in years:
                raw = input_financials.get(y, {}).get(key)
                if raw is None or raw == "":
                    vals.append("")
                else:
                    vals.append(str(raw))
            lines.append(f"{label},{','.join(vals)}")
        lines.append("")
    return "\n".join(lines).strip()


def financials_to_dataframe(input_financials: dict) -> pd.DataFrame:
    """把 {year: {metric_key: value}} 转成内部编辑 DataFrame：metric_key | metric_name | year1 | year2 | ..."""
    years = sorted(str(y) for y in input_financials.keys())
    rows = []
    for key, meta in METRIC_META.items():
        row = {"metric_key": key, "metric_name": meta["label"]}
        for y in years:
            v = input_financials.get(y, {}).get(key)
            row[y] = "" if v is None or v == "" else v
        rows.append(row)
    return pd.DataFrame(rows)


def dataframe_to_financials(df: pd.DataFrame) -> dict:
    """把编辑 DataFrame 转回 {year: {metric_key: value}}。"""
    year_cols = [c for c in df.columns if c not in {"metric_key", "metric_name"}]
    out: dict = {y: {} for y in year_cols}
    for _, row in df.iterrows():
        key = row.get("metric_key")
        if key not in METRIC_META:
            continue
        for y in year_cols:
            v = row.get(y)
            if v is None or v == "":
                continue
            try:
                v = float(v)
            except (ValueError, TypeError):
                pass
            out[y][key] = v
    return out


def build_empty_template(years: list[str] | None = None) -> pd.DataFrame:
    """生成空录入模板。"""
    years = years or ["2022", "2023", "2024"]
    return financials_to_dataframe({y: {} for y in years})


def write_template_csv(path: Path | str = TEMPLATE_PATH, years: list[str] | None = None) -> None:
    """写模板 CSV 到 data/derived/，供用户下载。"""
    build_empty_template(years).to_csv(path, index=False, encoding="utf-8-sig")


def parse_uploaded_file(file_obj) -> pd.DataFrame | None:
    """解析上传的 CSV / Excel，返回标准编辑 DataFrame；失败返回 None。"""
    try:
        name = file_obj.name.lower()
        if name.endswith(".csv"):
            df = pd.read_csv(file_obj)
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(file_obj)
        else:
            return None
    except Exception:
        return None

    # 兼容两种列名：metric_key / metric_name 二选一
    if "metric_key" not in df.columns and "metric_name" in df.columns:
        # 用中文名反查 key
        rev = {m["label"]: k for k, m in METRIC_META.items()}
        df["metric_key"] = df["metric_name"].map(rev)
    if "metric_key" not in df.columns:
        return None

    # 保留已知指标，补齐 metric_name
    df = df[df["metric_key"].isin(METRIC_META.keys())].copy()
    df["metric_name"] = df["metric_key"].map(lambda k: METRIC_META[k]["label"])

    # 确定年份列：除 metric_key/metric_name 之外的数字列
    year_cols = [c for c in df.columns if c not in {"metric_key", "metric_name"}]
    keep = ["metric_key", "metric_name"] + year_cols
    return df[keep].copy()


if __name__ == "__main__":
    # 生成并打印模板（本地调试 / 生成 csv 用）
    write_template_csv()
    print(f"template written to {TEMPLATE_PATH}")
    print(build_empty_template().to_csv(index=False))
