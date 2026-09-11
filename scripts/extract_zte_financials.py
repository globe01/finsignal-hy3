"""中兴通讯 000063 2021-2025 五年结构化财务字段提取脚本（words 重排版）。

设计原则（与 CATL/隆基/格力/BYD/万华/三一 extract_*.py 同架构）：
- pdfplumber.extract_words + 按 y 坐标 + 容差 10 重新分行，解决"行名+数字"被
  PDF 切到多行的问题。
- 单位口径（逐年度表头核验）：中兴通讯 2021-2025 合并资产负债表 / 合并利润表 /
  合并现金流量表 / 非经常性损益表 表头均为"人民币千元"，**5 年一致，
  全部 ×1000 换算为元**（`bs_is_cf_unit = 1000`、`nr_unit = 1000`）。
  注意：年报"主要会计数据"摘要页（约 p12-13）非经常性损益以"百万元"呈现，
  但本报告取**详细表**（财务报表补充资料，"人民币千元"），与三大表口径一致。
- A/H 股双上市格式特征（与纯 A 股公司不同）：
  - 权益采用"股东权益"表述（非"所有者权益"）。
  - 归母口径行名为"归属于母公司普通股股东权益合计" / "归属于母公司普通股股东"
    （净利润归属段在"按所有权归属分类"之后）。
  - 合并三大表末段"股东权益合计"行存在**列布局翻转**问题：2021 为"标签在前"
    （current=首个金额），2022 起为"金额居中/标签在后"（current=末个金额）；
    直接按首尾取金额会错配。故 **equity 改用 资产总计 − 负债合计 核算**，
    该恒等式 5 年均与报表"股东权益合计"行逐年限相等（已在脚本内交叉校验）。
- 非经常性损益（详细表）：结构为
  "各损益项目 → 税前小计合计 → 减：所得税影响数 → 减：少数股东权益影响数(税后)
  → 税后净额（无标签，紧跟在少数股东影响行之后）"。脚本取"少数股东权益影响
  数/额"行**之后首个金额**即税后归母净额；同时兼容"影响数"与"影响额"两种写法
  （2025 年为"影响额"）。
- 不重构为通用脚本；优先保证可复核、可跑通。
"""
import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/000063_ZTE"
OUT_CSV = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]

# 各年合并三大表 + 非经常性损益详细表的页码区间（覆盖完整合并段；末段权益/净额均在范围内）。
# 单位：5 年合并 BS/IS/CF/NR 均为"人民币千元"。
FILES = {
    2021: {
        "pdf": "ZTE_000063_2021_annual_report.pdf",
        "bs_pages": [143, 144, 145],
        "is_pages": [146, 147],
        "cf_pages": [150, 151],
        "nr_pages": [345, 346],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1000,
    },
    2022: {
        "pdf": "ZTE_000063_2022_annual_report.pdf",
        "bs_pages": [111, 112, 113],
        "is_pages": [114, 115],
        "cf_pages": [118, 119],
        "nr_pages": [245, 246],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1000,
    },
    2023: {
        "pdf": "ZTE_000063_2023_annual_report.pdf",
        "bs_pages": [107, 108, 109],
        "is_pages": [110, 111],
        "cf_pages": [114, 115],
        "nr_pages": [261, 262],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1000,
    },
    2024: {
        "pdf": "ZTE_000063_2024_annual_report.pdf",
        "bs_pages": [108, 109, 110],
        "is_pages": [111, 112],
        "cf_pages": [115, 116],
        "nr_pages": [263, 264],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1000,
    },
    2025: {
        "pdf": "ZTE_000063_2025_annual_report.pdf",
        "bs_pages": [99, 100, 101],
        "is_pages": [102, 103],
        "cf_pages": [106, 107],
        "nr_pages": [247, 248],
        "bs_is_cf_unit": 1000,
        "nr_unit": 1000,
    },
}

# 终止 marker：扫描拼接文本，遇到则截断（只保留合并表，剔除母公司表）。
BS_STOP_MARKERS = ["母公司资产负债表", "母公司利润表", "母公司现金流量表"]
IS_STOP_MARKERS = ["母公司利润表", "母公司现金流量表"]
CF_STOP_MARKERS = ["母公司现金流量表"]

# 金额：必须含千分位（中兴数据为整数千元，如 50,713,310）。
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
    """中兴数字格式：带千分位整数（千元口径），如 50,713,310；括号负数偶见。"""
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
    - "startswith"：防子串误匹配。

    nil 感知：合并报表列布局为 [当年] [上年]（或标签居中），若"标签之后首个 token"
    为空值标记（- / — / 无 / 不适用 等），说明当年未列示，返回 None（保持空值，不填 0、
    不取上年数）。例：ZTE "商誉 - 186,206" 的当年为"-"，应留空而非取上年 186,206。
    """
    if mode not in ("in", "startswith"):
        raise ValueError(f"mode 必须是 'in' 或 'startswith'，收到 {mode!r}")
    _NIL_TOKENS = {"-", "—", "－", "--", "无", "零", "N/A", "不适用", "nil"}
    norm = lambda s: re.sub(r"\s+", "", s).strip()
    target_norms = sorted({norm(o) for o in label_options}, key=len, reverse=True)
    for raw in rows:
        n = norm(raw)
        for t in target_norms:
            hit = n.startswith(t) if mode == "startswith" else (t in n)
            if not hit:
                continue
            # nil 检测：label token 之后的首个 token 是否为空值标记
            toks = raw.split()
            li = None
            for i, tk in enumerate(toks):
                if t in re.sub(r"\s+", "", tk):
                    li = i
                    break
            if li is not None and li + 1 < len(toks):
                nxt = toks[li + 1].strip().lstrip("(").rstrip(")")
                if nxt in _NIL_TOKENS:
                    return None
            m = _AMOUNT_RE.search(raw)
            if m:
                v = parse_num(m.group())
                if v is not None:
                    return v
            break
    return None


def extract_total_row(rows: list[str], label: str, exclude: list[str] | None = None) -> float | None:
    """取含 label 的行、且其 norm 文本不含任一 exclude 子串的首个金额。

    用于'资产总计'/'负债合计'等：'负债合计'是'流动负债合计'/'非流动负债合计'的子串，
    直接用 substring 匹配会误命中小计行；故显式排除流动/非流动子串。
    """
    norm = lambda s: re.sub(r"\s+", "", s)
    for raw in rows:
        n = norm(raw)
        if label in n:
            if exclude and any(e in n for e in exclude):
                continue
            m = _AMOUNT_RE.search(raw)
            if m:
                v = parse_num(m.group())
                if v is not None:
                    return v
    return None


def extract_equity_stated(rows: list[str]) -> float | None:
    """直接取报表'股东权益合计'行金额，作为 equity 的交叉校验基准。

    该行列布局翻转：2021 标签在前→取首个金额；2022 起标签在中/后→取末个金额。
    （实际写入 CSV 的 equity 用 资产总计−负债合计，此处仅校验。）
    """
    norm = lambda s: re.sub(r"\s+", "", s)
    for raw in rows:
        n = norm(raw)
        if "股东权益合计" in n and "归属于母公司" not in n:
            lbl = "股东权益合计"
            idx = n.find(lbl)
            after = n[idx + len(lbl):]
            m = _AMOUNT_RE.search(after)
            if m:
                return parse_num(m.group())
            # 标签在末尾：取之前的首个金额（即当年列）
            before = n[:idx]
            m2 = _AMOUNT_RE.search(before)
            if m2:
                return parse_num(m2.group())
            return None
    return None


def extract_goodwill(rows: list[str]) -> float | None:
    """商誉：合并 BS 行格式为'商誉 [附注号] [当年] [上年]'（与'货币资金 1 ...'同构，
    附注号紧跟标签）。当年值位于 label 之后第 2 个 token（跳过附注号）。

    特殊性：商誉极小（千元级，占资产<0.01%），当年可能为'-'（空）或极小整数（如 17/19 千元，
    无千分位）。通用 _AMOUNT_RE 要求千分位会漏匹配，故此处直接解析该 token：
    '-'/— 等视为空；否则按整数/带逗号数解析（含千元口径，调用方再 ×1000）。
    """
    _NIL = {"-", "—", "－", "--", "无", "零", "N/A", "不适用", "nil"}
    for raw in rows:
        if "商誉" in raw:
            toks = raw.split()
            for i, tk in enumerate(toks):
                if re.sub(r"\s+", "", tk) == "商誉":
                    # 优先取当年列（label 之后第 2 个 token）
                    if i + 2 < len(toks):
                        cur = toks[i + 2].strip().lstrip("(").rstrip(")")
                        if cur in _NIL:
                            return None
                        try:
                            return float(cur.replace(",", ""))
                        except ValueError:
                            pass
                    # 兜底：label 之后首个 token
                    if i + 1 < len(toks):
                        cur = toks[i + 1].strip().lstrip("(").rstrip(")")
                        if cur in _NIL:
                            return None
                        try:
                            return float(cur.replace(",", ""))
                        except ValueError:
                            return None
                    return None
    return None


def extract_nonrecurring(rows: list[str]) -> float | None:
    """非经常性损益（税后归母净额）：取'少数股东权益影响数/额'行**之后首个金额**。

    详细表结构：税前小计合计 → 减：所得税影响数 → 减：少数股东权益影响数(税后)
    → 税后净额（无标签，紧跟在少数股东影响行之后）。兼容"影响数"与"影响额"两种写法。
    """
    norm = lambda s: re.sub(r"\s+", "", s)
    for i, raw in enumerate(rows):
        if "少数股东权益影响" in norm(raw):
            for j in range(i + 1, len(rows)):
                m = _AMOUNT_RE.search(rows[j])
                if m:
                    v = parse_num(m.group())
                    if v is not None:
                        return v
            return None
    return None


def extract_all_fields(pdf_path: Path, cfg: dict) -> dict:
    bs_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["bs_pages"]), BS_STOP_MARKERS)
    is_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["is_pages"]), IS_STOP_MARKERS)
    cf_rows = collect_until_marker(get_lines_by_y(pdf_path, cfg["cf_pages"]), CF_STOP_MARKERS)
    nr_rows = get_lines_by_y(pdf_path, cfg["nr_pages"])

    k = cfg["bs_is_cf_unit"]
    kn = cfg["nr_unit"]

    revenue = extract_field(is_rows, ["营业收入", "一、 营业收入"])
    if revenue is not None:
        revenue *= k
    cogs = extract_field(is_rows, ["营业成本", "减：营业成本"])
    if cogs is not None:
        cogs *= k
    # 归母口径：A/H 股双上市，行名为"归属于母公司普通股股东"（在"按所有权归属分类"段）
    net_profit = extract_field(is_rows, [
        "归属于母公司普通股股东的净利润",
        "归属于母公司普通股股东",
        "归属于母公司股东的净利润",
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
    goodwill = extract_goodwill(bs_rows)
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
    total_assets = extract_total_row(bs_rows, "资产总计", exclude=["流动", "非流动"])
    if total_assets is not None:
        total_assets *= k
    total_liabilities = extract_total_row(bs_rows, "负债合计", exclude=["流动", "非流动"])
    if total_liabilities is not None:
        total_liabilities *= k

    # equity = 资产总计 − 负债合计（避开'股东权益合计'行列布局翻转导致的首尾错配）；
    # 与报表'股东权益合计'行逐年限相等（下方交叉校验）。
    equity = None
    if total_assets is not None and total_liabilities is not None:
        equity = total_assets - total_liabilities

    # 归母权益：'归属于母公司普通股股东权益合计'，amount-first 标签在末，首金额=当年。
    parent_equity = extract_field(bs_rows, ["归属于母公司普通股股东权益合计"], mode="in")
    if parent_equity is not None:
        parent_equity *= k
    equity_stated = extract_equity_stated(bs_rows)
    if equity_stated is not None:
        equity_stated *= k

    # NR 表：取详细表税后归母净额（少数股东影响行之后首个金额）。NR 表单位千元。
    nonrecurring = extract_nonrecurring(nr_rows)
    if nonrecurring is not None:
        nonrecurring *= kn

    return {
        "revenue": revenue, "cogs": cogs, "net_profit": net_profit, "cfo": cfo,
        "accounts_receivable": accounts_receivable, "inventory": inventory,
        "goodwill": goodwill, "current_assets": current_assets,
        "current_liabilities": current_liabilities, "short_borrow": short_borrow,
        "cash": cash, "equity": equity, "parent_equity": parent_equity,
        "equity_stated": equity_stated,
        "nonrecurring": nonrecurring,
        "bs_pages": cfg["bs_pages"], "is_pages": cfg["is_pages"],
        "cf_pages": cfg["cf_pages"], "nr_pages": cfg["nr_pages"],
        "pdf": cfg["pdf"],
    }


def cross_check(results: dict[int, dict]) -> list[str]:
    """表内 sanity check：关键字段非空/非负、equity>=parent_equity、equity=资产-负债。"""
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
        if r["equity"] is not None and r["equity_stated"] is not None:
            if abs(r["equity"] - r["equity_stated"]) > 1:
                msgs.append(f"equity(资产-负债={r['equity']}) != 报表股东权益合计({r['equity_stated']})")
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
                  "cash", "equity", "parent_equity", "equity_stated", "nonrecurring"]:
            v = r.get(k)
            print(f"  {k:20s} = {v}")

    print("\n======== 表内 sanity check（关键字段 + 权益关系 + 恒等式） ========")
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

    # 仅替换中兴通讯行；其他公司保持不动
    kept = [r for r in rows[1:] if r[1] != "000063"]

    new_rows = [header]
    for y in YEARS:
        r = results[y]
        cfg = FILES[y]
        src = (f"合并BS p{r['bs_pages'][0]}-{r['bs_pages'][-1]};"
               f"合并IS p{r['is_pages'][0]}-{r['is_pages'][-1]};"
               f"合并CF p{r['cf_pages'][0]}-{r['cf_pages'][-1]};"
               f"非经常性损益(详细表) p{r['nr_pages'][0]}-{r['nr_pages'][-1]}")
        notes = (f"原表单位:BS/IS/CF/NR=千元(5年一致,已×1000换算为元);"
                 f"net_profit=归属于母公司普通股股东净利润(归母口径,A/H股双上市格式);"
                 f"equity=资产总计−负债合计(合并BS末段'股东权益合计'行列布局在2022年翻转,直接取首尾金额易错位,"
                 f"故用恒等式核算;已与报表'股东权益合计'行逐年限相等校验);"
                 f"parent_equity=归属于母公司普通股股东权益合计(仅校验,不入CSV主列,但equity含少数股东);"
                 f"nonrecurring=非经常性损益合计(税后归母,取详细表'少数股东权益影响数/额'行之后首个金额即减所得税/少数股东影响后净额)")
        vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                   "inventory", "goodwill", "current_assets", "current_liabilities",
                                   "short_borrow", "cash", "equity", "nonrecurring"]]
        new_rows.append([
            "中兴通讯", "000063", "SZSE", str(y),
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
