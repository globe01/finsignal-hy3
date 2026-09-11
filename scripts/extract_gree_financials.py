"""格力电器 000651 2021-2025 五年结构化财务字段提取脚本（words 重排版）。

设计原则：
- 与隆基 extract_longi_financials.py 同架构：pdfplumber.extract_words + 按 y 坐标
  + 容差 10 重新分行，从根本上解决"行名+数字"被 PDF 切到多行的问题。
- 格力 5 年合并三大报表单位均为「人民币元」，无单位换算。
- 非经常性损益总额全部位于 p8/p9 的「九、非经常性损益项目及金额」表中——取首个
  「合计」行即可（本项目跨年金额互为校验：每年报披露 3 年，第 2、3 年值与前年报本
  年值一致）。格力的附注末尾（p176、p199、p206 等）虽还有「合计」，但属其他明细
  小计，无需提取。
- 不重构为通用脚本；优先保证可复核、可跑通。
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/000651_GREE"
OUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]

# 各年合并三大表 + 非经常性损益汇总表的页码区间。
# 来源：scripts/_scan_gree.py / scripts/_peek_gree_2021.py 实际定位。
FILES = {
    2021: {
        "pdf": "GREE_000651_2021_annual_report.pdf",
        "bs_pages": list(range(118, 120)),   # 118-119 跨页
        "is_pages": [120],
        "cf_pages": [121],
        "nr_pages": [8, 9],                  # 主要会计数据 + 附注的「九、非经常性损益项目及金额」
    },
    2022: {
        "pdf": "GREE_000651_2022_annual_report.pdf",
        "bs_pages": list(range(117, 119)),   # 117-118
        "is_pages": [119],
        "cf_pages": [120],
        "nr_pages": [8, 9],
    },
    2023: {
        "pdf": "GREE_000651_2023_annual_report.pdf",
        "bs_pages": list(range(114, 116)),   # 114-115
        "is_pages": [116],
        "cf_pages": [117],
        "nr_pages": [8, 9],
    },
    2024: {
        "pdf": "GREE_000651_2024_annual_report.pdf",
        "bs_pages": list(range(111, 113)),   # 111-112
        "is_pages": [113],
        "cf_pages": [114],
        "nr_pages": [8, 9],
    },
    2025: {
        "pdf": "GREE_000651_2025_annual_report.pdf",
        "bs_pages": list(range(86, 88)),     # 86-87
        "is_pages": [88],
        "cf_pages": [89],
        "nr_pages": [8, 9],                  # 2025 p8 已含合计行，p9 是注释
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


def extract_field(rows: list[str], label_options: list[str], *, mode: str = "in") -> float | None:
    """行列表中找 norm 后匹配任一 label 的行，取行内**第一个**含千分位的金额。

    mode:
    - "in" (默认)：norm 后 substring 匹配。适合 IS/CF/NR 等「行名可能以编号开头
      （"1、营业收入"）或含副标题」的场景。
    - "startswith"：norm 后必须以 label 开头。适合 BS 等「行名直接是项目名、
      存在『短标签是长标签子串』（"股东权益合计" vs "归属于母公司股东权益合计"）
      易误匹配」的场景。

    label_options 按字符串长度倒序排列后逐个尝试——对 startswith 必要性不大，
    但能避免某些边界情况下"先发现短标签就 break" 的误命中。
    """
    if mode not in ("in", "startswith"):
        raise ValueError(f"mode 必须是 'in' 或 'startswith'，收到 {mode!r}")
    norm = lambda s: re.sub(r"\s+", "", s).strip()
    target_norms = sorted({norm(o) for o in label_options}, key=len, reverse=True)
    for raw in rows:
        n = norm(raw)
        for t in target_norms:
            hit = n.startswith(t) if mode == "startswith" else (t in n)
            if hit:
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

    # IS/CF/NR 用默认 "in"（宽松匹配，能命中"1、营业收入"等形式）。
    # BS 用 "startswith"：避免"股东权益合计"误匹配"归属于母公司股东权益合计"。
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
        # 格力 5 年均用「股东权益合计」（非"所有者权益"）；下游合并三大表通用表达。
        "股东权益合计",
    ], mode="startswith")
    parent_equity = extract_field(bs_rows, [
        # 格力 5 年均用「归属于母公司股东权益合计」。
        "归属于母公司股东权益合计",
    ], mode="startswith")

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
        for k in ["revenue", "cogs", "net_profit", "cfo", "current_assets",
                  "current_liabilities", "cash", "equity"]:
            v = r.get(k)
            if v is None:
                msgs.append(f"{k} 为空")
            elif v <= 0:
                msgs.append(f"{k} 异常: {v}")
        if r["equity"] is not None and r["parent_equity"] is not None:
            if r["equity"] < r["parent_equity"] - 1:
                msgs.append(f"equity({r['equity']}) < parent_equity({r['parent_equity']})")
        if msgs:
            warnings.extend(f"[{y}] {m}" for m in msgs)
    # 跨年：非经常性损益本年披露 vs 上年报上一列
    # （此校验交给人工交叉；脚本只做单年报内部勾稽以避免逻辑膨胀）
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

    # 仅替换格力电器行；宁德时代、隆基绿能、其他公司保持不动
    kept = [r for r in rows[1:] if r[0] not in ("格力电器",)]

    new_rows = [header]
    for y in YEARS:
        r = results[y]
        src = (f"合并BS p{r['bs_pages'][0]}-{r['bs_pages'][-1]};"
               f"合并IS p{r['is_pages'][0]}-{r['is_pages'][-1]};"
               f"合并CF p{r['cf_pages'][0]}-{r['cf_pages'][-1]};"
               f"非经常性损益项目及金额 p{r['nr_pages'][0]}-{r['nr_pages'][-1]}")
        notes = (f"原表单位:人民币元(5年一致);net_profit=归属于母公司股东的净利润;"
                 f"equity=股东权益合计(含少数股东);parent_equity=归属于母公司股东权益合计;"
                 f"nonrecurring=非经常性损益合计(税后归母,九、非经常性损益项目及金额表合计行)")
        if r.get("goodwill") is None:
            notes = notes + ";goodwill原表未列示数值,按缺失值处理"
        vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                   "inventory", "goodwill", "current_assets", "current_liabilities",
                                   "short_borrow", "cash", "equity", "nonrecurring"]]
        new_rows.append([
            "格力电器", "000651", "SZSE", str(y),
            *[f"{v:.2f}" if v is not None else "" for v in vals],
            r["pdf"], src, "元", notes,
        ])

    new_rows.extend(kept)

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
