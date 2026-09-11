"""从宁德时代 2021-2025 年报 PDF 提取合并三大报表关键行项目，生成
data/derived/real_financials_2021_2025.csv 中的宁德时代样板行。

设计原则（宁缺毋滥）：
- 只从「合并资产负债表 / 合并利润表 / 合并现金流量表 / 非经常性损益表」四个固定区域提取；
- 每个区域动态读取「单位：X」行，统一换算为元；原始值与单位、页码全部记录；
- 内置交叉校验：本年期初=上年期末、本期披露上年数=上年本期数、归母净利=净利润-少数股东损益；
- 任何提取失败的字段留空，绝不猜测。
"""

import csv
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "data/raw/cninfo/300750_CATL"
OUT = ROOT / "data/derived/real_financials_2021_2025.csv"

YEARS = [2021, 2022, 2023, 2024, 2025]
PDF = {y: PDF_DIR / f"CATL_300750_{y}_annual_report.pdf" for y in YEARS}

UNIT_MULT = {"元": 1, "千元": 1_000, "万元": 10_000, "百万元": 1_000_000, "亿元": 100_000_000}
NUM = r"-?[\d,]+(?:\.\d+)?"

# 各字段匹配的正则片段（兼容 2021 序号行名与 2025 版式）
PATTERNS = {
    # 合并资产负债表
    "cash": r"货币资金",
    "accounts_receivable": r"应收账款",
    "inventory": r"存货",
    "goodwill": r"商誉",
    "current_assets": r"流动资产合计",
    "current_liabilities": r"流动负债合计",
    "short_borrow": r"短期借款",
    "equity": r"所有者权益合计",
    "equity_parent": r"归属于母公司所有者权益合计",
    "total_assets": r"资产总计",
    # 合并利润表
    "revenue": r"其中：营业收入",
    "cogs": r"其中：营业成本",
    "net_profit_total": r"(?:[一二三四五]、)?净利润（净亏损[^）]*）?",
    "minority_pl": r"(?:（[一二]）|[12][.．])?少数股东损益",
    "net_profit": r"(?:（[一二]）|[12][.．])?归属于母公司(?:所有者|股东)的净利润",
    # 合并现金流量表
    "cfo": r"经营活动产生的现金流量净额",
}


def load_pages(pdf_path):
    with pdfplumber.open(pdf_path) as doc:
        return [(i + 1, pg.extract_text() or "") for i, pg in enumerate(doc.pages)]


def find_section(pages, start_key, end_key):
    """合并区域文本。结束页保留 end_key 之前的文本（合并表尾部常与母公司表标题同页）。"""
    start_idx = None
    for i, (pno, t) in enumerate(pages):
        if start_key in t and ("单位：" in t or "编制单位" in t):
            start_idx = i
            break
    if start_idx is None:
        return None, []
    texts, pagenos = [pages[start_idx][1]], [pages[start_idx][0]]
    for i in range(start_idx + 1, len(pages)):
        pno, t = pages[i]
        if end_key in t:
            head = t.split(end_key)[0]
            if head.strip():
                texts.append(head)
                pagenos.append(pno)
            break
        texts.append(t)
        pagenos.append(pno)
    return "\n".join(texts), pagenos


def detect_unit(section_text):
    m = re.search(r"单位：\s*(百万元|万元|千元|亿元|元)", section_text)
    return m.group(1) if m else None


def grab(section_text, key):
    """按 PATTERNS[key] 匹配行首，取第一列数字（本期/期末）与第二列（上期/期初）。"""
    pat = re.compile(rf"^[ \t]*(?:{PATTERNS[key]})\s+({NUM})(?:\s+({NUM}))?", re.M)
    m = pat.search(section_text)
    if not m:
        print(f"    !! 未找到行: {key} ({PATTERNS[key]})")
        return None, None
    return m.group(1), m.group(2)


def to_yuan(raw, unit):
    if raw is None or unit is None:
        return None
    return float(raw.replace(",", "")) * UNIT_MULT[unit]


def main():
    results = {}

    for y in YEARS:
        print(f"\n######## {y} 年报 ########")
        pages = load_pages(PDF[y])

        # --- 合并资产负债表 ---
        bs, bs_pages = find_section(pages, "合并资产负债表", "母公司资产负债表")
        bs_unit = detect_unit(bs) if bs else None
        row = {"bs_unit": bs_unit, "bs_pages": bs_pages}
        print(f"  [BS] 页 {bs_pages} 单位={bs_unit}")
        for k in ["cash", "accounts_receivable", "inventory", "goodwill", "current_assets",
                  "current_liabilities", "short_borrow", "equity", "equity_parent", "total_assets"]:
            raw, raw_prev = grab(bs, k)
            row[k] = to_yuan(raw, bs_unit)
            row[k + "_prev"] = to_yuan(raw_prev, bs_unit)

        # --- 合并利润表 ---
        isc, is_pages = find_section(pages, "合并利润表", "母公司利润表")
        is_unit = detect_unit(isc) if isc else None
        row["is_unit"], row["is_pages"] = is_unit, is_pages
        print(f"  [IS] 页 {is_pages} 单位={is_unit}")
        for k in ["revenue", "cogs", "net_profit_total", "minority_pl", "net_profit"]:
            raw, raw_prev = grab(isc, k)
            row[k] = to_yuan(raw, is_unit)
            row[k + "_prev"] = to_yuan(raw_prev, is_unit)

        # --- 合并现金流量表 ---
        cf, cf_pages = find_section(pages, "合并现金流量表", "母公司现金流量表")
        cf_unit = detect_unit(cf) if cf else None
        row["cf_unit"], row["cf_pages"] = cf_unit, cf_pages
        raw, raw_prev = grab(cf, "cfo")
        row["cfo"] = to_yuan(raw, cf_unit)
        row["cfo_prev"] = to_yuan(raw_prev, cf_unit)
        print(f"  [CF] 页 {cf_pages} 单位={cf_unit}")

        # --- 非经常性损益表（第二节，约前 60 页内；跨 2 页）---
        nr_text, nr_pages = None, []
        for i, (pno, t) in enumerate(pages):
            if "非经常性损益项目及金额" in t and "单位：" in t and pno < 60:
                nr_pages = [pno]
                nr_text = t
                if i + 1 < len(pages):
                    nr_pages.append(pages[i + 1][0])
                    nr_text += "\n" + pages[i + 1][1]
                break
        nr_unit = detect_unit(nr_text) if nr_text else None
        row["nr_unit"], row["nr_pages"] = nr_unit, nr_pages
        if nr_text:
            m = re.search(rf"^[ \t]*合计\s+({NUM})(?:\s+({NUM}))?", nr_text, re.M)
            if m:
                row["nonrecurring"] = to_yuan(m.group(1), nr_unit)
                row["nonrecurring_prev"] = to_yuan(m.group(2), nr_unit)
            else:
                row["nonrecurring"] = None
                print("    !! 未找到非经常性损益合计行")
        print(f"  [NR] 页 {nr_pages} 单位={nr_unit}")

        results[y] = row

    # ---------- 交叉校验 ----------
    # 舍入容差：年报报表单位为万元(保留2位小数, 精度100元)或千元(整数, 精度1000元)。
    # 跨年对比两侧精度不同，最坏舍入差约 1100 元；表内勾稽差也在该量级。超过容差才视为真实差异。
    TOL = 2000.0
    print("\n======== 交叉校验（容差 %.0f 元，覆盖千元/万元表舍入）========" % TOL)
    warnings = []
    for y in YEARS:
        row = results[y]
        msgs = []
        # 1) 归母 = 净利润 - 少数股东损益（表内勾稽）
        if None not in (row.get("net_profit"), row.get("net_profit_total"), row.get("minority_pl")):
            diff = row["net_profit"] - (row["net_profit_total"] - row["minority_pl"])
            if abs(diff) > TOL:
                msgs.append(f"归母≠净利-少数: diff={diff:,.2f}")
        # 2) 跨年: 本年期初 = 上年期末
        if y > 2021:
            prev = results[y - 1]
            for k in ["cash", "accounts_receivable", "inventory", "goodwill", "current_assets",
                      "current_liabilities", "short_borrow", "equity", "total_assets"]:
                if row.get(k + "_prev") is not None and prev.get(k) is not None:
                    diff = row[k + "_prev"] - prev[k]
                    if abs(diff) > TOL:
                        msgs.append(f"BS期初≠上年期末 {k}: {row[k+'_prev']:,.0f} vs {prev[k]:,.0f} (差 {diff:,.0f})")
            for k in ["revenue", "net_profit", "cfo"]:
                if row.get(k + "_prev") is not None and prev.get(k) is not None:
                    diff = row[k + "_prev"] - prev[k]
                    if abs(diff) > TOL:
                        msgs.append(f"{k} 上年披露≠上年原报: {row[k+'_prev']:,.0f} vs {prev[k]:,.0f} (差 {diff:,.0f})")
            # 非经常性损益上年披露 vs 上年原报
            if row.get("nonrecurring_prev") is not None and prev.get("nonrecurring") is not None:
                diff = row["nonrecurring_prev"] - prev["nonrecurring"]
                if abs(diff) > TOL:
                    msgs.append(f"nonrecurring 上年披露≠上年原报: 差 {diff:,.0f}")
        print(f"  {y}: {'OK' if not msgs else 'CHECK'}")
        warnings.extend(f"[{y}] {m}" for m in msgs)

    if warnings:
        print("\n---- 警告明细 ----")
        for w in warnings:
            print(" ", w)

    # ---------- 打印提取结果 ----------
    print("\n======== 提取结果（单位：元）========")
    for y in YEARS:
        row = results[y]
        print(f"--- {y} ---")
        for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable", "inventory",
                  "goodwill", "current_assets", "current_liabilities", "short_borrow", "cash",
                  "equity", "nonrecurring"]:
            v = row.get(k)
            print(f"  {k:22s} {('%22.2f' % v) if v is not None else '                 (空)'}")

    # ---------- 写 CSV（宁德时代 5 行 + 7 家占位行）----------
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = ["company", "stock_code", "exchange", "year", "revenue", "cogs", "net_profit", "cfo",
              "accounts_receivable", "inventory", "goodwill", "current_assets", "current_liabilities",
              "short_borrow", "cash", "equity", "nonrecurring", "source_report",
              "source_table_or_page", "unit", "notes"]
    placeholders = ["比亚迪", "隆基绿能", "三一重工", "格力电器", "万华化学", "中兴通讯", "恒瑞医药"]
    # 从 manifest 读占位行的 stock_code / exchange / source_report（PDF 文件名）
    manifest = {}
    if (ROOT / "data/derived/manifest.csv").exists():
        with open(ROOT / "data/derived/manifest.csv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                manifest[(r["company"], r["year"])] = {
                    "stock_code": r["stock_code"],
                    "exchange": r["exchange"],
                    "source_report": Path(r["local_raw_path"]).name,
                }
    blank_13 = [""] * 13   # revenue..nonrecurring 共 13 字段
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for y in YEARS:
            r = results[y]
            src = (f"合并资产负债表p{','.join(map(str, r['bs_pages']))};"
                   f"合并利润表p{','.join(map(str, r['is_pages']))};"
                   f"合并现金流量表p{','.join(map(str, r['cf_pages']))};"
                   f"非经常性损益表p{','.join(map(str, r['nr_pages']))}")
            notes = (f"原表单位:BS={r['bs_unit']},IS={r['is_unit']},CF={r['cf_unit']},NR={r['nr_unit']};"
                     f"net_profit=归属于母公司股东的净利润;equity=所有者权益合计(含少数股东);"
                     f"nonrecurring=非经常性损益合计(税后归母)")
            vals = [r.get(k) for k in ["revenue", "cogs", "net_profit", "cfo", "accounts_receivable",
                                       "inventory", "goodwill", "current_assets", "current_liabilities",
                                       "short_borrow", "cash", "equity", "nonrecurring"]]
            w.writerow(["宁德时代", "300750", "SZSE", y]
                       + [f"{v:.2f}" if v is not None else "" for v in vals]
                       + [f"CATL_300750_{y}_annual_report.pdf", src, "元", notes])
        for c in placeholders:
            for y in YEARS:
                info = manifest.get((c, str(y)), {})
                w.writerow([c, info.get("stock_code", ""), info.get("exchange", ""), y]
                           + blank_13
                           + [info.get("source_report", ""), "", "元", "待提取"])
    print(f"\nCSV -> {OUT}")
    if warnings:
        print("!! 存在校验警告，请人工复核后再提交")


if __name__ == "__main__":
    main()
