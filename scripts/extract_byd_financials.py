"""比亚迪 002594 2021-2025 五年结构化财务字段提取脚本（words 重排版）。

设计原则：
- 与隆基/格力 extract_*.py 同架构：pdfplumber.extract_words + 按 y 坐标 + 容差 10
  重新分行，从根本上解决"行名+数字"被 PDF 切到多行的问题。
- 单位策略（逐年显式配置，口径来自表头/财务附注核验）：
  - 2021 BS/IS/CF：原表单位"元" → CSV 存元
  - 2022-2025 BS/IS/CF：原表"财务附注中报表的单位为：千元"（BS/IS/CF 表头未单独
    标"单位：X"，通过数据精度判断为千元）→ CSV 存元（×1000）
  - 2021-2025 NR：原表"单位：元" → CSV 存元
- NR 表只有「合计」一个数；本年金额列位置在 2021/2022 与 2023-2025 不同（首列
  vs 第二列），脚本通过"行内第一个金额"统一处理。
- 不重构为通用脚本；优先保证可复核、可跑通。
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/002594_BYD"
OUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]

# 各年合并三大表 + 非经常性损益汇总表的页码区间。
# 单位换算：bs_is_cf_unit="1"(元) 或 "1000"(千元→元)；nr_unit="1" (NR 表均为元)。
FILES = {
    2021: {
        "pdf": "BYD_002594_2021_annual_report.pdf",
        "bs_pages": [136, 137, 138],
        "is_pages": [140, 141, 142],
        "cf_pages": [143, 144, 145],
        "nr_pages": [9, 10],
        "bs_is_cf_unit": 1,      # 原表"单位：元"
        "nr_unit": 1,             # NR 表"单位：元"
    },
    2022: {
        "pdf": "BYD_002594_2022_annual_report.pdf",
        "bs_pages": [127, 128],
        "is_pages": [129, 130],
        "cf_pages": [133, 134],
        "nr_pages": [9],
        "bs_is_cf_unit": 1000,    # "财务附注中报表的单位为：千元"
        "nr_unit": 1,             # NR 表"单位：元"
    },
    2023: {
        "pdf": "BYD_002594_2023_annual_report.pdf",
        "bs_pages": [138, 139],
        "is_pages": [140, 141],
        "cf_pages": [144, 145],
        "nr_pages": [12],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1,
    },
    2024: {
        "pdf": "BYD_002594_2024_annual_report.pdf",
        "bs_pages": [142, 143, 144],
        "is_pages": [145, 146],
        "cf_pages": [149, 150],
        "nr_pages": [12],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1,
    },
    2025: {
        "pdf": "BYD_002594_2025_annual_report.pdf",
        "bs_pages": [123, 124, 125],
        "is_pages": [126, 127],
        "cf_pages": [130, 131],
        "nr_pages": [12],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1,
    },
}

# 终止 marker：扫描拼接文本，遇到则截断（只保留合并表，剔除母公司表）。
BS_STOP_MARKERS = [
    "2、母公司资产负债表",  # 2021/2022/2023/2024/2025 出现的位置
    "母公司资产负债表",
    "母公司利润表",
    "母公司现金流量表",
]
IS_STOP_MARKERS = ["4、母公司利润表", "母公司利润表", "母公司现金流量表"]
CF_STOP_MARKERS = ["6、母公司现金流量表", "母公司现金流量表"]

# 金额：必须含千分位 + 可选小数；BYD 数据里也有 "(123,456)" 括号负数形式
_AMOUNT_RE = re.compile(r"\(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def get_lines_by_y(pdf_path: Path, pages: list[int]) -> list[str]:
    """按 y 坐标 + 容差 10 合并 words 为行；跨页拼接（去掉每页页眉「比亚迪股份...全文」）。"""
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
    """BYD 数字格式：123,456（无小数）；偶尔 (123,456) 括号负数；极少带 .00。"""
    if s is None:
        return None
    s = s.strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "—", "－", "--"):
        return None
    # 括号负数
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def extract_field(rows: list[str], label_options: list[str], *, mode: str = "in") -> float | None:
    """行列表中找 norm 后匹配任一 label 的行，取行内**第一个**含千分位的金额。

    mode:
    - "in" (默认)：substring 匹配，适合 IS/CF/NR 行名以编号开头（"1、营业收入"）。
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

    # BS 行名用 startswith 防子串误匹配；IS/CF/NR 用默认 in（可命中"一、营业收入"等）。
    revenue = extract_field(is_rows, ["其中：营业收入", "营业收入", "一、 营业收入"])
    if revenue is not None:
        revenue *= k
    cogs = extract_field(is_rows, ["其中：营业成本", "营业成本", "减：营业成本"])
    if cogs is not None:
        cogs *= k
    # 2022 BYD IS 行名是"六、 按所有权归属分类" + "归属于母公司所有者的净利润"
    # 2023-2025 BYD IS 行名同 2022
    # 2021 BYD IS 行名是"（二）按所有权归属分类" + "1.归属于母公司股东的净利润"
    net_profit = extract_field(is_rows, [
        "归属于母公司所有者的净利润",
        "归属于母公司股东的净利润",
        "1.归属于母公司股东的净利润",
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
    # BYD 2022-2025 BS 末段用"股东权益合计"；2021 BS 用"所有者权益合计"
    equity = extract_field(bs_rows, ["股东权益合计", "所有者权益合计"], mode="startswith")
    if equity is not None:
        equity *= k
    parent_equity = extract_field(bs_rows, [
        "归属于母公司股东权益合计",
        "归属于母公司所有者权益合计",
    ], mode="startswith")
    if parent_equity is not None:
        parent_equity *= k

    # NR 表：只取"合计"行的本年金额（行内第一个数字）。NR 表单位始终为元。
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
    """表内 sanity check。

    注：本脚本只取 NR 表的"本年合计"入 CSV。NR 表第二列"上年合计"与去年报本年
    应一致——但本次提取不保留"上年值"字段，跨年勾稽交给人工或单独脚本复核。
    本函数仅做表内 sanity check（关键字段非空/非负、equity>=parent_equity）。
    """
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

    # 仅替换比亚迪行；其他公司保持不动
    kept = [r for r in rows[1:] if r[0] not in ("比亚迪",)]

    new_rows = [header]
    for y in YEARS:
        r = results[y]
        cfg = FILES[y]
        bs_unit_note = "元" if cfg["bs_is_cf_unit"] == 1 else "千元(已×1000换算为元)"
        nr_unit_note = "元" if cfg["nr_unit"] == 1 else "千元(已×1000换算为元)"
        src = (f"合并BS p{r['bs_pages'][0]}-{r['bs_pages'][-1]};"
               f"合并IS p{r['is_pages'][0]}-{r['is_pages'][-1]};"
               f"合并CF p{r['cf_pages'][0]}-{r['cf_pages'][-1]};"
               f"非经常性损益项目及金额 p{r['nr_pages'][0]}-{r['nr_pages'][-1]}")
        notes = (f"原表单位:BS/IS/CF={bs_unit_note},NR={nr_unit_note};"
                 f"net_profit=归属于母公司所有者的净利润;"
                 f"equity=股东权益合计(含少数股东,2021为所有者权益合计);"
                 f"parent_equity=归属于母公司股东权益合计(仅校验,不入CSV);"
                 f"nonrecurring=非经常性损益合计(税后归母,九、非经常性损益项目及金额表合计行)")
        vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                   "inventory", "goodwill", "current_assets", "current_liabilities",
                                   "short_borrow", "cash", "equity", "nonrecurring"]]
        new_rows.append([
            "比亚迪", "002594", "SZSE", str(y),
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
