"""隆基绿能 601012 2021-2025 五年结构化财务字段提取脚本（words 重排版）。

设计原则：
- 用 pdfplumber.extract_words + 按 y 坐标 + 容差 10 重新分行，从根本上解决 PDF 把
  "行名"和"数字"分到多行（视觉同一行，提取成 y=82/y=88/y=94 多段）的问题。
- 隆基报表"本年"在左、"上年"在右；统一取本年列（行内第一个含千分位的金额）。
- 跨页拼接文本：每页独立按 y 重排后拼接；不跨页合并（合并三大表每行通常完整在一页）。
- 不强行重构为通用脚本，优先保证可复核、可跑通。
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/601012_LONGI"
OUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]

FILES = {
    2021: {
        "pdf": "LONGI_601012_2021_annual_report.pdf",
        "bs_pages": list(range(105, 111)),
        "is_pages": list(range(111, 115)),
        "cf_pages": list(range(115, 118)),
        "nr_pages": list(range(11, 13)),
    },
    2022: {
        "pdf": "LONGI_601012_2022_annual_report.pdf",
        "bs_pages": list(range(113, 119)),
        "is_pages": list(range(119, 123)),
        "cf_pages": list(range(123, 127)),
        "nr_pages": list(range(9, 12)),
    },
    2023: {
        "pdf": "LONGI_601012_2023_annual_report.pdf",
        "bs_pages": list(range(121, 126)),
        "is_pages": list(range(126, 131)),
        "cf_pages": list(range(131, 136)),
        "nr_pages": list(range(11, 14)),
    },
    2024: {
        "pdf": "LONGI_601012_2024_annual_report_revised.pdf",
        "bs_pages": list(range(118, 124)),
        "is_pages": list(range(124, 127)),
        "cf_pages": list(range(127, 130)),
        "nr_pages": list(range(11, 14)),
    },
    2025: {
        "pdf": "LONGI_601012_2025_annual_report.pdf",
        "bs_pages": list(range(119, 125)),
        "is_pages": list(range(125, 128)),
        "cf_pages": list(range(128, 131)),
        "nr_pages": list(range(14, 16)),
    },
}

# 终止 marker：扫描拼接文本，遇到则截断
BS_STOP_MARKERS = ["母公司资产负债表", "母公司利润表", "合并利润表", "合并现金流量表", "合并所有者权益变动表"]
IS_STOP_MARKERS = ["母公司利润表", "合并现金流量表", "母公司现金流量表", "合并所有者权益变动表"]
CF_STOP_MARKERS = ["母公司现金流量表", "合并所有者权益变动表", "合并资产负债表"]

# 金额：必须含千分位 + 可选小数
_AMOUNT_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def get_lines_by_y(pdf_path: Path, pages: list[int]) -> list[str]:
    """按 y 坐标 + 容差 10 合并 words 为行。"""
    import pdfplumber
    out: list[str] = []
    with pdfplumber.open(pdf_path) as doc:
        for p in pages:
            page = doc.pages[p - 1]
            words = page.extract_words(use_text_flow=True, x_tolerance=3, y_tolerance=3)
            if not words:
                continue
            words = sorted(words, key=lambda w: (w["top"], w["x0"]))
            cur_top = words[0]["top"]
            cur_words = [words[0]["text"]]
            for w in words[1:]:
                if abs(w["top"] - cur_top) <= 10:
                    cur_words.append(w["text"])
                else:
                    out.append(" ".join(cur_words))
                    cur_words = [w["text"]]
                    cur_top = w["top"]
            out.append(" ".join(cur_words))
    return out


def collect_until_marker(lines: list[str], stop_markers: list[str]) -> list[str]:
    """遇到任一 stop marker 截断。"""
    out: list[str] = []
    for line in lines:
        hit = next((m for m in stop_markers if m in line), None)
        if hit:
            idx = line.find(hit)
            before = line[:idx].rstrip()
            if before:
                out.append(before)
            return out
        out.append(line)
    return out


def parse_num(s: str) -> float | None:
    if s is None:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "—", "－", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_field(rows: list[str], label_options: list[str]) -> float | None:
    """在行列表中找 norm 后包含任一 label 的行，取行内**第一个**含千分位的金额（本年列）。"""
    norm = lambda s: re.sub(r"\s+", "", s).strip()
    target_norms = [norm(o) for o in label_options]
    for raw in rows:
        n = norm(raw)
        for t in target_norms:
            if t in n:
                m = _AMOUNT_RE.search(raw)
                if m:
                    v = parse_num(m.group())
                    if v is not None:
                        return v
                break
    return None


def extract_all_fields(pdf_path: Path, cfg: dict) -> dict:
    bs_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["bs_pages"]), BS_STOP_MARKERS)
    is_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["is_pages"]), IS_STOP_MARKERS)
    cf_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["cf_pages"]), CF_STOP_MARKERS)
    nr_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["nr_pages"]),
                                   BS_STOP_MARKERS + IS_STOP_MARKERS + CF_STOP_MARKERS)

    revenue = extract_field(is_rows, ["其中：营业收入", "营业收入"])
    if revenue is None:
        revenue = extract_field(is_rows, ["一、营业总收入"])
    cogs = extract_field(is_rows, ["其中：营业成本", "营业成本"])
    net_profit = extract_field(is_rows, ["归属于母公司股东的净利润"])
    cfo = extract_field(cf_rows, ["经营活动产生的现金流量净额"])

    accounts_receivable = extract_field(bs_rows, ["应收账款"])
    inventory = extract_field(bs_rows, ["存货"])
    goodwill = extract_field(bs_rows, ["商誉"])
    current_assets = extract_field(bs_rows, ["流动资产合计"])
    current_liabilities = extract_field(bs_rows, ["流动负债合计"])
    short_borrow = extract_field(bs_rows, ["短期借款"])
    cash = extract_field(bs_rows, ["货币资金"])
    equity = extract_field(bs_rows, [
        "所有者权益（或股东权",  # PDF 截断到 "（或股东权" 后接数字
        "所有者权益(或股东权",
        "所有者权益合计",
    ])
    parent_equity = extract_field(bs_rows, [
        "归属于母公司所有者权益",  # PDF 截断后只到 "归属于母公司所有者权益"
        "归属于母公司所有者权益(或",
    ])

    nonrecurring = extract_field(nr_rows, ["合计"])

    return {
        "revenue": revenue, "cogs": cogs, "net_profit": net_profit, "cfo": cfo,
        "accounts_receivable": accounts_receivable, "inventory": inventory,
        "goodwill": goodwill, "current_assets": current_assets,
        "current_liabilities": current_liabilities, "short_borrow": short_borrow,
        "cash": cash, "equity": equity, "parent_equity": parent_equity,
        "nonrecurring": nonrecurring,
        "bs_pages": cfg["bs_pages"], "is_pages": cfg["is_pages"],
        "cf_pages": cfg["cf_pages"], "nr_pages": cfg["nr_pages"],
        "pdf": cfg["pdf"],
    }


def cross_check(results: dict[int, dict]) -> list[str]:
    """跨年一致性 + 表内勾稽。"""
    warnings: list[str] = []
    for y in YEARS:
        r = results[y]
        msgs: list[str] = []
        for k in ["revenue", "cogs", "current_assets", "current_liabilities",
                  "cash", "equity"]:
            v = r.get(k)
            if v is None:
                msgs.append(f"{k} 为空")
            elif v <= 0:
                msgs.append(f"{k} 异常: {v}")
        # equity >= parent_equity（合并 BS：所有者权益合计 >= 归母权益）
        if r["equity"] is not None and r["parent_equity"] is not None:
            if r["equity"] < r["parent_equity"] - 1:
                msgs.append(f"equity({r['equity']}) < parent_equity({r['parent_equity']})")
        # 跨年：本年期末 = 下年年初（上年的 last 行 = 本年的 first 行）
        # BS 本年期末 vs 下年年初（次年 BS 上年末列）—— 暂不做（已通过主要会计数据表交叉）
        if msgs:
            warnings.extend(f"[{y}] {m}" for m in msgs)
    return warnings


def main():
    results: dict[int, dict] = {}
    for y in YEARS:
        cfg = FILES[y]
        pdf = PDF_DIR / cfg["pdf"]
        print(f"\n========== {y}: {cfg['pdf']} ==========")
        results[y] = extract_all_fields(pdf, cfg)
        r = results[y]
        for k in ["revenue", "cogs", "net_profit", "cfo",
                  "accounts_receivable", "inventory", "goodwill",
                  "current_assets", "current_liabilities", "short_borrow",
                  "cash", "equity", "parent_equity", "nonrecurring"]:
            v = r.get(k)
            print(f"  {k:25s} = {v}")

    print("\n======== 交叉校验 ========")
    warns = cross_check(results)
    if warns:
        for w in warns:
            print(f"  WARN: {w}")
    else:
        print("  OK")

    if not OUT_CSV.exists():
        raise SystemExit(f"CSV 不存在: {OUT_CSV}")

    with open(OUT_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    header = rows[0]

    catl_rows = [r for r in rows[1:] if r[0] == "宁德时代"]
    other_rows = [r for r in rows[1:] if r[0] not in ("宁德时代", "隆基绿能")]

    new_rows = [header]
    for y in YEARS:
        r = next((x for x in catl_rows if x[3] == str(y)), None)
        assert r, f"宁德时代 {y} 数据缺失"
        new_rows.append(r)

    for y in YEARS:
        r = results[y]
        src = (f"合并BS p{r['bs_pages'][0]}-{r['bs_pages'][-1]};"
               f"合并IS p{r['is_pages'][0]}-{r['is_pages'][-1]};"
               f"合并CF p{r['cf_pages'][0]}-{r['cf_pages'][-1]};"
               f"非经常性损益 p{r['nr_pages'][0]}-{r['nr_pages'][-1]}")
        notes = (f"原表单位:元(5年一致);net_profit=归属于母公司股东的净利润;"
                 f"equity=所有者权益(或股东权益)合计(含少数股东);"
                 f"nonrecurring=非经常性损益合计(税后归母)")
        if y == 2024:
            notes = "修订版;" + notes
        if y == 2025 and r.get("goodwill") is None:
            notes = notes + ";2025合并BS商誉行未列示数值，按缺失值处理，CSV保持空值"
        vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                   "inventory", "goodwill", "current_assets", "current_liabilities",
                                   "short_borrow", "cash", "equity", "nonrecurring"]]
        new_rows.append([
            "隆基绿能", "601012", "SSE", str(y),
            *[f"{v:.2f}" if v is not None else "" for v in vals],
            r["pdf"], src, "元", notes,
        ])

    new_rows.extend(other_rows)

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(new_rows)

    with open(OUT_CSV, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    ncols = len(rows[0])
    bad = [(i, len(r)) for i, r in enumerate(rows[1:], 2) if len(r) != ncols]
    print(f"\nCSV -> {OUT_CSV.relative_to(ROOT)}: {len(rows)-1} 行 × {ncols} 列")
    print(f"列数异常行: {len(bad)}")


if __name__ == "__main__":
    main()
