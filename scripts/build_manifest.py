"""扫描 data/raw/cninfo 下 40 份年报 PDF 封面，生成 data/derived/manifest.csv。

规则（不猜测任何字段）：
- 公司全称使用映射表（均经 PDF 封面文本核验），脚本会校验全称是否出现在封面，不一致则告警。
- report_date 仅接受"披露年"日期（即 year+1），支持阿拉伯数字与中文数字（如 二〇二三年四月）；
  封面无披露日期则留空，notes 写"待补公告日期"。绝不把报告期（当年 12 月）当作披露日期。
- 隆基绿能 2024 使用修订版文件，notes 标注。
"""

import csv
import re
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data/raw/cninfo"
OUT = ROOT / "data/derived/manifest.csv"

# 目录名 -> (公司简称, 公司全称[经封面核验], 交易所)
COMPANIES = {
    "300750_CATL": ("宁德时代", "宁德时代新能源科技股份有限公司", "SZSE"),
    "002594_BYD": ("比亚迪", "比亚迪股份有限公司", "SZSE"),
    "601012_LONGI": ("隆基绿能", "隆基绿能科技股份有限公司", "SSE"),
    "600031_SANY": ("三一重工", "三一重工股份有限公司", "SSE"),
    "000651_GREE": ("格力电器", "珠海格力电器股份有限公司", "SZSE"),
    "600309_WANHUA": ("万华化学", "万华化学集团股份有限公司", "SSE"),
    "000063_ZTE": ("中兴通讯", "中兴通讯股份有限公司", "SZSE"),
    "600276_HENGRUI": ("恒瑞医药", "江苏恒瑞医药股份有限公司", "SSE"),
}

CN_DIGIT = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_MONTH = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
            "十一": 11, "十二": 12}


def parse_cn_date(text: str, expect_year: int):
    """解析中文数字日期，如 二〇二三年四月 -> (2023, 4)。仅接受 expect_year。"""
    for m in re.finditer(r"([〇一二三四五六七八九]{4})年\s*([一二三四五六七八九十]{1,2})月", text):
        year = int("".join(str(CN_DIGIT[c]) for c in m.group(1)))
        if year == expect_year and m.group(2) in CN_MONTH:
            return year, CN_MONTH[m.group(2)]
    return None


def parse_arabic_date(text: str, expect_year: int):
    """解析阿拉伯数字日期 2024 年 03 月 -> (2024, 3)。仅接受 expect_year。"""
    for m in re.finditer(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text):
        if int(m.group(1)) == expect_year and 1 <= int(m.group(2)) <= 12:
            return int(m.group(1)), int(m.group(2))
    return None


rows = []
for folder in sorted(RAW.iterdir()):
    if not folder.is_dir():
        continue
    short, full_name, exchange = COMPANIES[folder.name]
    for pdf in sorted(folder.glob("*.pdf")):
        m = re.match(r"^[A-Z]+_(\d{6})_(\d{4})_annual_report(_revised)?\.pdf$", pdf.name)
        if not m:
            print(f"!! 命名不符规则，跳过: {pdf.name}")
            continue
        stock_code, year = m.group(1), int(m.group(2))
        revised = bool(m.group(3))
        sample_id = f"{short}_{year}"

        # 前 2 页（封面 + 重要提示页），披露日期可能在任一页
        with pdfplumber.open(pdf) as doc:
            cover = "".join((pg.extract_text() or "") + "\n" for pg in doc.pages[:2])

        # 全称核验
        name_ok = full_name.replace(" ", "") in cover.replace(" ", "")
        if not name_ok:
            print(f"!! 全称核验失败 {sample_id}: 封面未找到「{full_name}」，请人工确认")

        # 披露日期：只接受 year+1（年报在次年披露），杜绝把报告期当年日期误当披露日
        report_date = ""
        full_hit = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", cover)
        if full_hit and int(full_hit.group(1)) == year + 1:
            y, mo, d = int(full_hit.group(1)), int(full_hit.group(2)), int(full_hit.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                report_date = f"{y:04d}-{mo:02d}-{d:02d}"
        if not report_date:
            hit = parse_arabic_date(cover, year + 1) or parse_cn_date(cover, year + 1)
            if hit:
                report_date = f"{hit[0]:04d}-{hit[1]:02d}"

        title = f"{full_name}{year}年年度报告"
        if revised:
            title += "（修订版）"

        notes = []
        if revised:
            notes.append("使用修订版")
        if not report_date:
            notes.append("待补公告日期")

        rows.append({
            "sample_id": sample_id,
            "company": short,
            "stock_code": stock_code,
            "exchange": exchange,
            "year": year,
            "report_title": title,
            "source": "CNINFO",
            # Stable official disclosure index; report_title/year identify the specific report.
            "source_url": f"https://www.cninfo.com.cn/new/disclosure/stock?stockCode={stock_code}",
            "local_raw_path": str(pdf.relative_to(ROOT)),
            "report_date": report_date,
            "data_type": "real_raw",
            "notes": "；".join(notes),
        })
        print(f"OK {sample_id}: {report_date or '(无披露日期)'} {'修订版' if revised else ''}")

OUT.parent.mkdir(parents=True, exist_ok=True)
with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)
print(f"\n共 {len(rows)} 条 -> {OUT}")
