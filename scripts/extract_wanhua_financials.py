"""万华化学 600309 2021-2025 五年结构化财务字段提取脚本（words 重排版）。

设计原则（与 BYD / 格力 / 隆基 extract_*.py 同架构）：
- pdfplumber.extract_words + 按 y 坐标 + 容差 10 重新分行，解决"行名+数字"被
  PDF 切到多行的问题。
- 单位策略：经逐年度表头核验，万华化学 2021-2025 合并资产负债表 / 合并利润表 /
  合并现金流量表 / 非经常性损益表 表头均为"单位：元 币种：人民币"，**无需换算**。
  bs_is_cf_unit = 1，nr_unit = 1。
- 关键 label（PDF 内全角括号，行名可能被 PDF 切行，靠 norm 去空白对齐）：
  - 股东权益合计行名："所有者权益（或股东权益）合计"（2021-2025 一致）；
    行内可能拆成"所有者权益（或股东权 / 益）合计"，故用 norm=lambda:
    re.sub(r"\\s+","",s) 去空白后再 startswith 匹配。
  - 归母权益："归属于母公司所有者权益（或股东权益）合计"（仅校验，不入 CSV）。
  - 净利润行名："1.归属于母公司股东的净利润（净亏损以"-"号填列）"；
    用 in 模式候选 ["1.归属于母公司股东的净利润","归属于母公司股东的净利润",
    "归属于母公司所有者的净利润"] 命中。
- 非经常性损益：表内"合计"行即税后归母口径合计；取行内第一个金额。
- 不重构为通用脚本；优先保证可复核、可跑通。
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/600309_WANHUA"
OUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]

# 各年合并三大表 + 非经常性损益汇总表的页码区间（含 1 页 buffer 用于命中母公司
# 终止 marker）。万华 2021-2025 全部为"元"，bs_is_cf_unit / nr_unit 均为 1。
FILES = {
    2021: {
        "pdf": "WANHUA_600309_2021_annual_report.pdf",
        "bs_pages": [71, 72, 73, 74],
        "is_pages": [75, 76, 77, 78],
        "cf_pages": [78, 79, 80],
        "nr_pages": [7],
        "bs_is_cf_unit": 1,
        "nr_unit": 1,
    },
    2022: {
        "pdf": "WANHUA_600309_2022_annual_report.pdf",
        "bs_pages": [66, 67, 68, 69],
        "is_pages": [70, 71, 72, 73],
        "cf_pages": [74, 75, 76],
        "nr_pages": [7],
        "bs_is_cf_unit": 1,
        "nr_unit": 1,
    },
    2023: {
        "pdf": "WANHUA_600309_2023_annual_report.pdf",
        "bs_pages": [72, 73, 74, 75],
        "is_pages": [76, 77, 78, 79],
        "cf_pages": [79, 80, 81],
        "nr_pages": [8],
        "bs_is_cf_unit": 1,
        "nr_unit": 1,
    },
    2024: {
        "pdf": "WANHUA_600309_2024_annual_report.pdf",
        "bs_pages": [79, 80, 81, 82],
        "is_pages": [83, 84, 85, 86],
        "cf_pages": [87, 88, 89],
        "nr_pages": [8],
        "bs_is_cf_unit": 1,
        "nr_unit": 1,
    },
    2025: {
        "pdf": "WANHUA_600309_2025_annual_report.pdf",
        "bs_pages": [79, 80, 81, 82],
        "is_pages": [83, 84, 85, 86],
        "cf_pages": [87, 88, 89],
        "nr_pages": [8],
        "bs_is_cf_unit": 1,
        "nr_unit": 1,
    },
}

# 终止 marker：扫描拼接文本，遇到则截断（只保留合并表，剔除母公司表）。
BS_STOP_MARKERS = ["母公司资产负债表", "母公司利润表", "母公司现金流量表"]
IS_STOP_MARKERS = ["母公司利润表", "母公司现金流量表"]
CF_STOP_MARKERS = ["母公司现金流量表"]

# 金额：必须含千分位 + 可选小数（万华数据带 2 位小数，如 34,216,298,291.79）。
_AMOUNT_RE = re.compile(r"\(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def get_lines_by_y(pdf_path: Path, pages: list[int]) -> list[str]:
    """按 y 坐标 + 容差 10 合并 words 为行；跨页拼接。"""
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
    """万华数字格式：带千分位 + 2 位小数，如 34,216,298,291.79；括号负数偶见。"""
    if s is None:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "—", "－", "--"):
        return None
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def extract_field(rows: list[str], label_options: list[str], *, mode: str = "in") -> float | None:
    """行列表中找 norm 后匹配任一 label 的行，取行内**第一个**含千分位的金额。

    mode:
    - "in" (默认)：substring 匹配，适合 IS/CF/NR 行名以编号开头。
    - "startswith"：防子串误匹配（如"股东权益合计" vs "归属于母公司股东权益合计"）。
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
    nr_rows = get_lines_by_y(pdf_path, cfg["nr_pages"])

    k = cfg["bs_is_cf_unit"]
    kn = cfg["nr_unit"]

    revenue = extract_field(is_rows, ["其中：营业收入", "营业收入", "一、 营业收入"])
    if revenue is not None:
        revenue *= k
    cogs = extract_field(is_rows, ["其中：营业成本", "营业成本", "减：营业成本"])
    if cogs is not None:
        cogs *= k
    # 万华 IS 净利润行名两种形态：2021/2023 为"1.归属于母公司股东的净利润（净亏损以"-"号填列）"，
    # 2022/2024/2025 PDF 重排后"润"字可能与数字粘连/脱落，行内可见"1.归属于母公司股东的净利<金额>"。
    # 用稳定前缀"归属于母公司股东"做 in 匹配，命中净利润行（"归属于母公司所有者"不含"股东"，不会误命中）。
    net_profit = extract_field(is_rows, [
        "1.归属于母公司股东的净利润",
        "归属于母公司股东的净利润",
        "归属于母公司股东的净利",
        "归属于母公司股东",
    ])
    if net_profit is not None:
        net_profit *= k
    cfo = extract_field(cf_rows, ["经营活动产生的现金流量净额"])
    if cfo is not None:
        cfo *= k

    accounts_receivable = extract_field(bs_rows, ["应收账款"])
    if accounts_receivable is not None:
        accounts_receivable *= k
    inventory = extract_field(bs_rows, ["存货"])
    if inventory is not None:
        inventory *= k
    goodwill = extract_field(bs_rows, ["商誉"])
    if goodwill is not None:
        goodwill *= k
    current_assets = extract_field(bs_rows, ["流动资产合计"])
    if current_assets is not None:
        current_assets *= k
    current_liabilities = extract_field(bs_rows, ["流动负债合计"])
    if current_liabilities is not None:
        current_liabilities *= k
    short_borrow = extract_field(bs_rows, ["短期借款"])
    if short_borrow is not None:
        short_borrow *= k
    cash = extract_field(bs_rows, ["货币资金"])
    if cash is not None:
        cash *= k
    # 万华 BS 末段权益合计行：2023 为整行"所有者权益（或股东权益）合计<金额>"；
    # 2021/2022/2024/2025 拆行为"所有者权益（或股东权<金额>" + "益）合计"。
    # 用稳定前缀"所有者权益（或股东"做 startswith，可唯一命中"总权益"行
    # （母公司权益行以"归属于母公司"开头，不会误命中；章节标题"所有者权益（或股东权益）："
    # 无金额，会被跳过）。
    equity = extract_field(bs_rows, ["所有者权益（或股东"], mode="startswith")
    if equity is not None:
        equity *= k
    parent_equity = extract_field(bs_rows, ["归属于母公司所有者权益"], mode="startswith")
    if parent_equity is not None:
        parent_equity *= k

    # NR 表：取"合计"行金额（税后归母口径合计）。NR 表单位始终为元。
    nonrecurring = extract_field(nr_rows, ["合计"])
    if nonrecurring is not None:
        nonrecurring *= kn

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
    """表内 sanity check：关键字段非空/非负、equity>=parent_equity。"""
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
    return warnings


def main():
    results: dict[int, dict] = {}
    for y in YEARS:
        cfg = FILES[y]
        pdf = PDF_DIR / cfg["pdf"]
        print(f"\n========== {y}: {cfg['pdf']} (BS/IS/CF×{cfg['bs_is_cf_unit']}, NR×{cfg['nr_unit']}) ==========")
        results[y] = extract_all_fields(pdf, cfg)
        r = results[y]
        for k in ["revenue", "cogs", "net_profit", "cfo",
                  "accounts_receivable", "inventory", "goodwill",
                  "current_assets", "current_liabilities", "short_borrow",
                  "cash", "equity", "parent_equity", "nonrecurring"]:
            v = r.get(k)
            print(f"  {k:25s} = {v}")

    print("\n======== 表内 sanity check（关键字段 + 权益关系） ========")
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
    ncols = len(header)

    # 仅替换万华化学行；其他公司保持不动
    kept = [r for r in rows[1:] if r[0] != "万华化学"]

    new_rows = [header]
    for y in YEARS:
        r = results[y]
        cfg = FILES[y]
        src = (f"合并BS p{r['bs_pages'][0]}-{r['bs_pages'][-1]};"
               f"合并IS p{r['is_pages'][0]}-{r['is_pages'][-1]};"
               f"合并CF p{r['cf_pages'][0]}-{r['cf_pages'][-1]};"
               f"非经常性损益项目及金额 p{r['nr_pages'][0]}-{r['nr_pages'][-1]}")
        notes = (f"原表单位:BS/IS/CF=元,NR=元(5年一致,无需换算);"
                 f"net_profit=归属于母公司股东的净利润(1.归属于母公司股东的净利润);"
                 f"equity=所有者权益（或股东权益）合计(含少数股东);"
                 f"parent_equity=归属于母公司所有者权益（或股东权益）合计(仅校验,不入CSV);"
                 f"nonrecurring=非经常性损益合计(税后归母)")
        vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                   "inventory", "goodwill", "current_assets", "current_liabilities",
                                   "short_borrow", "cash", "equity", "nonrecurring"]]
        new_rows.append([
            "万华化学", "600309", "SSE", str(y),
            *[f"{v:.2f}" if v is not None else "" for v in vals],
            r["pdf"], src, "元", notes,
        ])

    new_rows.extend(kept)

    # 列数校验
    bad = [(i, len(rr)) for i, rr in enumerate(new_rows[1:], 2) if len(rr) != ncols]
    if bad:
        raise SystemExit(f"CSV 列数异常: {bad}")

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(new_rows)

    print(f"\nCSV -> {OUT_CSV.relative_to(ROOT)}: {len(new_rows)-1} 行 × {ncols} 列")


if __name__ == "__main__":
    main()
