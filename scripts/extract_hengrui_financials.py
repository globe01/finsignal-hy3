"""恒瑞医药 600276 2021-2025 五年结构化财务字段提取脚本（words 重排版）。

设计原则（与 CATL/隆基/格力/BYD/万华/三一/中兴 extract_*.py 同架构）：
- pdfplumber.extract_words + 按 y 坐标 + 容差 10 重新分行，解决"行名+数字"被 PDF
  切到多行的问题。
- 单位口径：恒瑞医药 2021-2025 合并资产负债表 / 合并利润表 / 合并现金流量表 /
  非经常性损益明细表 表头均为"单位：元 / 币种：人民币"，**5 年一致，无需换算**
  （bs_is_cf_unit = 1、nr_unit = 1，写入即原值）。
- 列布局（本期/上期）在 2023 年出现局部翻转，必须按"标签位置"判定本期列，
  不能机械取首个/末个金额：
  * 标准（2021/2022/2024/2025 的 BS/IS/CF，以及 2023 的 IS/CF 部分行）：
    "标签 [附注] 本期 上期" → 标签在金额之前 → 本期 = 首个金额。
  * 异常（2023 合并BS 部分行，如"资产总计""非流动资产合计"）：
    "上期 标签 本期" → 标签在金额之间/之后 → 本期 = 末个金额。
  * 个别字段（2023 短期借款）："上期 — 标签 附注" → 标签在金额之后且当期为"—"
    （空值）→ 返回 None（保持空，不填 0）。
  本脚本用 `extract_current_value` 统一按"标签相对首个金额的位置"判定本期列，
  对上述三种情况均正确。
- 空值规则：标签之后（或标签与金额之间的当期位置）为 "-"/"—"/"－"/"无" 等空值标记，
  或整行无金额 → 返回 None（CSV 留空，不填 0、不取上期数）。
- 商誉：5 年合并资产负债表主表均未列示该行项目 → 留空。
- 短期借款：仅 2022 有值（1,260,943,473.97）；2023 当期为"—"（空）；
  2021/2024/2025 合并资产负债表主表未列示（空）。
- equity = 资产总计 − 负债合计（合并恒等式；含少数股东权益），与"归属于母公司所有者
  权益合计"逐年限满足 equity >= parent_equity（仅作交叉校验）。
- 非经常性损益（明细表）：结构为 各项 → 减：所得税影响额 → 减：少数股东权益影响额
  （税后）→ 合计（税后归母净额）。取"少数股东权益影响额"行**之后首个"合计"行**的金额。
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/600276_HENGRUI"
OUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]

# 各年合并三大表 + 非经常性损益明细表的精确页码（均为单页，次页即母公司表，故只取该页）。
# 单位：5 年合并 BS/IS/CF/NR 均为"元"。
FILES = {
    # 说明：合并利润表标题常落在某页底部，真实 IS 数据在次页；故 is_pages 取
    # [标题页, 次页] 两页区间（均在母公司利润表数据页之前）。extract_current_value
    # 取首个命中行（合并IS 必先于母公司IS），自动规避母公司数据污染。
    # BS 取 [合并BS首页, 次页]：合并资产负债表末段的"归属于母公司所有者权益（或股东
    # 权益）合计"与"所有者权益（或股东权益）合计"常落在合并BS首页之后那一页的顶部
    # （即合并权益段尾部，母公司表之前）；扩展一页可捕获 parent_equity 用于交叉校验，
    # 主字段仍由"首个命中行"锁定为合并值（母公司值在其后，不会误命中）。
    2021: {
        "pdf": "HENGRUI_600276_2021_annual_report.pdf",
        "bs_pages": [121, 122], "is_pages": [123], "cf_pages": [125], "nr_pages": [218],
        "bs_is_cf_unit": 1, "nr_unit": 1,
    },
    2022: {
        "pdf": "HENGRUI_600276_2022_annual_report.pdf",
        "bs_pages": [128, 129], "is_pages": [130, 131], "cf_pages": [132], "nr_pages": [219],
        "bs_is_cf_unit": 1, "nr_unit": 1,
    },
    2023: {
        "pdf": "HENGRUI_600276_2023_annual_report.pdf",
        "bs_pages": [145, 146], "is_pages": [147, 148], "cf_pages": [149], "nr_pages": [245],
        "bs_is_cf_unit": 1, "nr_unit": 1,
    },
    2024: {
        "pdf": "HENGRUI_600276_2024_annual_report.pdf",
        "bs_pages": [144, 145], "is_pages": [146, 147], "cf_pages": [149], "nr_pages": [249],
        "bs_is_cf_unit": 1, "nr_unit": 1,
    },
    2025: {
        "pdf": "HENGRUI_600276_2025_annual_report.pdf",
        "bs_pages": [133, 134], "is_pages": [135, 136], "cf_pages": [137], "nr_pages": [236],
        "bs_is_cf_unit": 1, "nr_unit": 1,
    },
}

# 金额：带千分位、两位小数（元口径），如 39,266,221,700.14；括号负数偶见。
_AMOUNT_RE = re.compile(r"\(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?\)|-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_NIL_TOKENS = {"-", "—", "－", "--", "无", "零", "N/A", "不适用", "nil", "－"}


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
            cur_words = [words[0]]
            for w in words[1:]:
                if abs(w["top"] - cur_top) <= 10:
                    cur_words.append(w)
                else:
                    out.append(" ".join(x["text"] for x in sorted(cur_words, key=lambda x: x["x0"])))
                    cur_words = [w]
                    cur_top = w["top"]
            out.append(" ".join(x["text"] for x in sorted(cur_words, key=lambda x: x["x0"])))
    return out


def parse_num(s: str) -> float | None:
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


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _is_nil(tok: str) -> bool:
    return norm(tok).strip("()") in _NIL_TOKENS


def extract_current_value(rows: list[str], label_options: list[str], *,
                          exclude: list[str] | None = None,
                          mode: str = "in") -> float | None:
    """取含 label 的行，按"标签相对首个金额的位置"返回**本期**金额。

    mode:
    - "in" (默认)：substring 匹配。
    - "startswith"：标签须为行首子串（防子串误匹配，如'负债合计'误命中'流动负债合计'）。

    判定规则：
    1. 行内无金额且无标签相邻空值标记 → 返回 None（空）。
    2. 标签在首个金额**之前** → 本期 = 标签之后**首个**金额（跳过其间空值标记）。
    3. 标签在首个金额**之后**：
       a. 标签之后仍存在金额 → 本期 = 标签之后**末个**金额（如 2023 '资产总计'：[上期][标签][本期]）。
       b. 金额全在标签之前（即上期列）→ 本期 = 标签**紧邻前一个** token：
          - 若为 nil 标记 → None（空，如 2023 '短期借款' 当期"—"）；
          - 否则取该 token（上期与本期之间无空值标记时的兜底）。
    """
    if mode not in ("in", "startswith"):
        raise ValueError(f"mode 必须是 'in' 或 'startswith'，收到 {mode!r}")
    target_norms = sorted({norm(o) for o in label_options}, key=len, reverse=True)
    for raw in rows:
        n = norm(raw)
        if exclude and any(e in n for e in exclude):
            continue
        hit = False
        for t in target_norms:
            hit = n.startswith(t) if mode == "startswith" else (t in n)
            if hit:
                break
        if not hit:
            continue

        toks = raw.split()
        amt_idxs = [i for i, tk in enumerate(toks) if _AMOUNT_RE.fullmatch(tk)]
        # 标签 token 索引
        li = None
        for i, tk in enumerate(toks):
            if any(t in norm(tk) for t in target_norms):
                li = i
                break
        if li is None:
            continue

        if not amt_idxs:
            # 无金额：检查标签相邻 token 是否为空值标记
            for j in (li + 1, li - 1):
                if 0 <= j < len(toks) and _is_nil(toks[j]):
                    return None
            return None

        if li < amt_idxs[0]:
            # 标签在首个金额之前 → 本期 = 标签之后首个金额
            if any(_is_nil(toks[j]) for j in range(li + 1, amt_idxs[0])):
                return None
            for i in amt_idxs:
                if i > li:
                    return parse_num(toks[i])
            return None
        else:
            # 标签在首个金额之后
            after = [i for i in amt_idxs if i > li]
            if after:
                return parse_num(toks[after[-1]])  # 末个金额（本期）
            # 金额全在标签之前：本期 = 标签紧邻前一个 token
            if li - 1 >= 0 and _is_nil(toks[li - 1]):
                return None
            if li - 1 >= 0:
                v = parse_num(toks[li - 1])
                if v is not None:
                    return v
            return None
    return None


def extract_total_row(rows: list[str], label: str, exclude: list[str] | None = None) -> float | None:
    """取含 label 的行（排除 exclude 子串）的本期金额；用于'资产总计'/'负债合计'。"""
    return extract_current_value(rows, [label], exclude=exclude, mode="in")


def extract_parent_equity(rows: list[str]) -> float | None:
    """提取归属于母公司所有者权益合计（仅用于表内 sanity check）。

    恒瑞合并 BS 末段经常把该行拆成两到三行：
    - 2021: 归属于母公司所有者权益（或股东权益）合计 35,002,...
    - 2022: 归属于母公司所有者权益 37,823,... / （或股东权益）合计
    - 2023: 归属于母公司所有 / 者权益（或股东权 40,465,... / 益）合计
    - 2024: 归属于母公司所有者权 45,519,... / 益（或股东权益）合计
    - 2025: 归属于母公司所有者权益（或股东 61,272,...
    """
    fragments = [
        "归属于母公司所有者权益（或股东权益）合计",
        "归属于母公司所有者权益合计",
        "归属于母公司所有者权益",
        "归属于母公司所有者权",
        "归属于母公司所有",
        "者权益（或股东权",
    ]
    for i, raw in enumerate(rows):
        n = norm(raw)
        if "归属于母公司" in n:
            pass
        elif n.startswith("者权益（或股东权") and i > 0 and "归属于母公司所有" in norm(rows[i - 1]):
            pass
        else:
            continue
        if not any(fragment in n for fragment in fragments):
            continue

        window = " ".join(rows[i:min(i + 3, len(rows))])
        m = _AMOUNT_RE.search(window)
        if m:
            return parse_num(m.group())
    return None


def extract_nonrecurring(rows: list[str]) -> float | None:
    """非经常性损益（税后归母净额）：取'少数股东权益影响'行**之后首个'合计'行**金额。"""
    target = "少数股东权益影响"
    for i, raw in enumerate(rows):
        if target in norm(raw):
            for j in range(i + 1, len(rows)):
                if "合计" in norm(rows[j]):
                    m = _AMOUNT_RE.search(rows[j])
                    if m:
                        return parse_num(m.group())
            return None
    return None


def extract_all_fields(pdf_path: Path, cfg: dict) -> dict:
    bs_rows = get_lines_by_y(pdf_path, cfg["bs_pages"])
    parent_bs_pages = list(cfg["bs_pages"])
    if parent_bs_pages[-1] + 1 not in parent_bs_pages:
        parent_bs_pages.append(parent_bs_pages[-1] + 1)
    parent_bs_rows = get_lines_by_y(pdf_path, parent_bs_pages)
    is_rows = get_lines_by_y(pdf_path, cfg["is_pages"])
    cf_rows = get_lines_by_y(pdf_path, cfg["cf_pages"])
    nr_rows = get_lines_by_y(pdf_path, cfg["nr_pages"])

    k = cfg["bs_is_cf_unit"]
    kn = cfg["nr_unit"]

    revenue = extract_current_value(is_rows, ["营业收入", "一、 营业收入"])
    if revenue is not None:
        revenue *= k
    cogs = extract_current_value(is_rows, ["减：营业成本", "营业成本"])
    if cogs is not None:
        cogs *= k
    net_profit = extract_current_value(is_rows, [
        "归属于母公司股东的净利润", "归属于母公司股东",
        # 2024 年该标签在 PDF 中被换行拆分，金额行实为"东的净利润（净亏损以 …"，
        # 而"五、净利润"合计行不含"的净利润"，故以下匹配唯一命中归母行。
        "的净利润（净亏损以",
    ])
    if net_profit is not None:
        net_profit *= k
    cfo = extract_current_value(cf_rows, ["经营活动产生的现金流量净额"])
    if cfo is not None:
        cfo *= k

    accounts_receivable = extract_current_value(bs_rows, ["应收账款"])
    if accounts_receivable is not None:
        accounts_receivable *= k
    inventory = extract_current_value(bs_rows, ["存货"])
    if inventory is not None:
        inventory *= k
    goodwill = extract_current_value(bs_rows, ["商誉"])
    if goodwill is not None:
        goodwill *= k
    current_assets = extract_current_value(bs_rows, ["流动资产合计"])
    if current_assets is not None:
        current_assets *= k
    current_liabilities = extract_current_value(bs_rows, ["流动负债合计"])
    if current_liabilities is not None:
        current_liabilities *= k
    short_borrow = extract_current_value(bs_rows, ["短期借款"])
    if short_borrow is not None:
        short_borrow *= k
    cash = extract_current_value(bs_rows, ["货币资金"])
    if cash is not None:
        cash *= k

    total_assets = extract_total_row(bs_rows, "资产总计")
    if total_assets is not None:
        total_assets *= k
    total_liabilities = extract_total_row(bs_rows, "负债合计", exclude=["流动", "非流动"])
    if total_liabilities is not None:
        total_liabilities *= k

    equity = None
    if total_assets is not None and total_liabilities is not None:
        equity = total_assets - total_liabilities

    # 归母权益（仅交叉校验，不入 CSV 主列）。该行常跨页/跨行拆分，单独读取合并 BS 次页。
    parent_equity = extract_parent_equity(parent_bs_rows)
    if parent_equity is not None:
        parent_equity *= k

    nonrecurring = extract_nonrecurring(nr_rows)
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
    warnings: list[str] = []
    for y in YEARS:
        r = results[y]
        msgs: list[str] = []
        for kk in ["revenue", "cogs", "net_profit", "cfo", "current_assets",
                   "current_liabilities", "cash", "equity"]:
            v = r.get(kk)
            if v is None:
                msgs.append(f"{kk} 为空")
            elif v <= 0:
                msgs.append(f"{kk} 异常: {v}")
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
        print(f"\n========== {y}: {cfg['pdf']} (unit={cfg['bs_is_cf_unit']}) ==========")
        results[y] = extract_all_fields(pdf, cfg)
        r = results[y]
        for k in ["revenue", "cogs", "net_profit", "cfo",
                  "accounts_receivable", "inventory", "goodwill",
                  "current_assets", "current_liabilities", "short_borrow",
                  "cash", "equity", "parent_equity", "nonrecurring"]:
            v = r.get(k)
            print(f"  {k:20s} = {v}")

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

    kept = [r for r in rows[1:] if r[1] != "600276"]

    new_rows = [header]
    for y in YEARS:
        r = results[y]
        cfg = FILES[y]
        def page_span(pages: list[int]) -> str:
            return f"p{pages[0]}" if pages[0] == pages[-1] else f"p{pages[0]}-{pages[-1]}"

        src = (f"合并BS {page_span(r['bs_pages'])};"
               f"合并IS {page_span(r['is_pages'])};"
               f"合并CF {page_span(r['cf_pages'])};"
               f"非经常性损益(明细表) {page_span(r['nr_pages'])}")
        notes = (f"原表单位:BS/IS/CF/NR=元(5年一致,无需换算);"
                 f"net_profit=归属于母公司股东的净利润(归母口径);"
                 f"equity=资产总计−负债合计(合并恒等式,含少数股东权益);"
                 f"parent_equity=归属于母公司所有者权益合计(仅校验,不入CSV主列);"
                 f"2023年合并BS部分行(资产总计/非流动资产合计)列布局翻转(标签居中,本期=末列),"
                 f"已用'标签相对金额位置'法统一判定本期列,其余年份标准(标签在前,本期=首列);"
                 f"短期借款仅2022合并BS主表有值(1,260,943,473.97),2023当期为'—'(空),"
                 f"2021/2024/2025合并BS主表未列示(空);"
                 f"商誉5年合并BS主表均未列示(空,不填0);"
                 f"nonrecurring=非经常性损益明细表合计(税后归母净额,取'少数股东权益影响额'行之后首个'合计'行)")
        vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                   "inventory", "goodwill", "current_assets", "current_liabilities",
                                   "short_borrow", "cash", "equity", "nonrecurring"]]
        new_rows.append([
            "恒瑞医药", "600276", "SSE", str(y),
            *[f"{v:.2f}" if v is not None else "" for v in vals],
            r["pdf"], src, "元", notes,
        ])

    new_rows.extend(kept)

    bad = [(i, len(rr)) for i, rr in enumerate(new_rows[1:], 2) if len(rr) != ncols]
    if bad:
        raise SystemExit(f"CSV 列数异常: {bad}")

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(new_rows)

    print(f"\nCSV -> {OUT_CSV.relative_to(ROOT)}: {len(new_rows)-1} 行 × {ncols} 列")


if __name__ == "__main__":
    main()
