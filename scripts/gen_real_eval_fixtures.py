#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 Phase 3 评估方法有效性验证用的离线「模型输出」样本（fixture）。

不调用 Hy3：本脚本仅为 discriminative validation 构造可控质量的模型输出文本，
用于验证 eval/real_sample_eval.py 这套规则评测器能否稳定区分 good > medium > bad/adversarial。

输入：data/derived/real_eval_samples.jsonl  （gold：expected_calculations / gold_facts / coverage / sample_status）
输出：data/derived/real_eval_outputs_fixture.jsonl  （每条：sample_id, quality, company, output）

设计要点（与任务要求一一对应）：
- 每条输出都关联 sample_id（要求 1）。
- good：正确引用 expected_calculations 的口径与数值，N/A 指标明确标注不补 0（要求 2）。
- medium：允许遗漏 1-2 个维度、解释较浅，但数字正确、N/A 处理正确（要求 3）。
- bad：包含明显计算错误 / 趋势误读 / 把 N/A 当 0（要求 4）。
- adversarial：伪造数字、术语堆砌、绕开证据、篇幅很长但事实错误、含无来源买卖建议（要求 5）。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES_PATH = ROOT / "data" / "derived" / "real_eval_samples.jsonl"
OUT_PATH = ROOT / "data" / "derived" / "real_eval_outputs_fixture.jsonl"

# 8 个代表性窗口：覆盖 READY、PARTIAL、以及恒瑞/中兴/隆基 这种 N/A 字段窗口
SELECTED = [
    "600309_2021-2023",  # 万华化学 READY
    "300750_2021-2023",  # 宁德时代 READY
    "000651_2021-2023",  # 格力电器 READY
    "002594_2021-2023",  # 比亚迪   READY
    "600031_2021-2023",  # 三一重工 READY
    "000063_2021-2023",  # 中兴通讯 PARTIAL（goodwill_to_equity N/A）
    "600276_2021-2023",  # 恒瑞医药 PARTIAL（goodwill_to_equity + short_borrow_to_cash N/A）
    "601012_2023-2025",  # 隆基绿能 PARTIAL（goodwill_to_equity N/A）
]


# ---------- 数值格式化 ---------- #
def bn(x: float) -> str:
    """元 -> 亿元，2 位小数。"""
    return f"{x / 1e8:.2f}"


def pct(x: float) -> str:
    """比率 -> 百分数，2 位小数。"""
    return f"{x * 100:.2f}"


def rat(x: float) -> str:
    """比率 -> x.xx。"""
    return f"{x:.2f}"


def load_samples():
    out = {}
    for line in SAMPLES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        s = json.loads(line)
        out[s["sample_id"]] = s
    return out


def na_handling_block(na_metrics, wrong=False):
    """构造「商誉压力 / 短期偿债压力」两段的 N/A 处理文本。wrong=True 时把 N/A 当 0。"""
    ge_block = ""
    sbc_block = ""
    if "goodwill_to_equity" in na_metrics:
        if wrong:
            ge_block = "商誉占权益比 0.00%（合并报表未单独列示商誉，视为无商誉风险）。"
        else:
            ge_block = ("商誉占权益比：合并报表主表未列示商誉字段，跨年无法稳定计算，"
                        "标记为 N/A，不补 0、不臆测原因。")
    if "short_borrow_to_cash" in na_metrics:
        if wrong:
            sbc_block = "短期借款/货币资金比 0.00（无短期借款，短期偿债无压力）。"
        else:
            sbc_block = ("短期借款/货币资金比：窗口内短期借款字段在多年度缺失，"
                        "无法稳定计算，标记为 N/A。")
    return ge_block, sbc_block


def gen_good(s):
    gf = s["gold_facts"]
    lp = gf["latest_profitability"]
    lc = gf["latest_cashflow_quality"]
    lb = gf["latest_balance_sheet_pressure"]
    rt = gf["revenue_trend"]
    ec = s["expected_calculations"]
    na = s["coverage"]["na_metric_names"]
    company = s["company"]
    yrs = s["window_years"]
    ge_block, sbc_block = na_handling_block(na, wrong=False)

    ge_line = f"商誉占权益比 {pct(lb['goodwill_to_equity'])}%。" if "goodwill_to_equity" not in na else ge_block
    sbc_line = f"短期借款/货币资金比 {rat(lb['short_borrow_to_cash'])}。" if "short_borrow_to_cash" not in na else sbc_block

    cfo_interp = "现金含量充足" if lc["cfo_to_net_profit"] >= 1 else "现金含量偏低"
    direction_cn = "上升" if rt["direction"] == "up" else "下降"

    text = f"""# {company} {yrs[0]}–{yrs[-1]} 财务质量分析（基于合并报表主表结构化数据）

## 一、收入趋势
{company} 在 {rt['start_year']}–{rt['end_year']} 年营业收入由 {bn(rt['start_revenue'])} 亿元增至 {bn(rt['end_revenue'])} 亿元，复合增长率 {pct(rt['cagr'])}%，呈{direction_cn}趋势。

## 二、盈利能力
最新年度（{lp['year']}）毛利率 {pct(lp['gross_margin'])}%，净利率 {pct(lp['net_margin'])}%。

## 三、现金流质量
经营现金流净额/净利润 = {rat(lc['cfo_to_net_profit'])}，{cfo_interp}。

## 四、营运资金压力
应收账款占营收比 {pct(ec['ar_to_revenue']['value'])}%，存货占营收比 {pct(ec['inventory_to_revenue']['value'])}%，流动比率 {rat(lb['current_ratio'])}。

## 五、短期偿债压力
{sbc_line}

## 六、商誉压力
{ge_line}

## 七、非经常性损益影响
非经常性损益/净利润 = {pct(ec['nonrecurring_to_net_profit']['value'])}%，扣非后盈利质量需结合主业判断。

> 计算口径均与 expected_calculations 一致：毛利率=(收入-成本)/收入、净利率=净利润/收入、经营现金流质量=经营现金流净额/净利润、流动比率=流动资产/流动负债、应收占比=应收账款/收入。凡输入字段缺失或标记 N/A 的指标，均明确说明无法从合并报表主表口径计算，不补 0、不臆测。
"""
    return text.strip()


def gen_medium(s):
    """medium：数字正确，但遗漏 1-2 个维度（此处省略商誉压力、非经常性损益），解释较浅。"""
    gf = s["gold_facts"]
    lp = gf["latest_profitability"]
    lc = gf["latest_cashflow_quality"]
    lb = gf["latest_balance_sheet_pressure"]
    rt = gf["revenue_trend"]
    ec = s["expected_calculations"]
    na = s["coverage"]["na_metric_names"]
    company = s["company"]
    yrs = s["window_years"]
    ge_block, sbc_block = na_handling_block(na, wrong=False)
    sbc_line = f"短期借款/货币资金比 {rat(lb['short_borrow_to_cash'])}。" if "short_borrow_to_cash" not in na else sbc_block

    text = f"""# {company} {yrs[0]}–{yrs[-1]} 简要分析

收入：{bn(rt['start_revenue'])} 亿元 -> {bn(rt['end_revenue'])} 亿元，复合增速 {pct(rt['cagr'])}%。

盈利能力：毛利率 {pct(lp['gross_margin'])}%，净利率 {pct(lp['net_margin'])}%。

现金流：经营现金流净额/净利润 {rat(lc['cfo_to_net_profit'])}。

营运资金：应收占营收 {pct(ec['ar_to_revenue']['value'])}%，存货占营收 {pct(ec['inventory_to_revenue']['value'])}%，流动比率 {rat(lb['current_ratio'])}。

短期偿债：{sbc_line}

（以上基于给定的结构化数据，未引用表外信息。）
"""
    return text.strip()


def gen_bad(s, idx):
    """bad：包含明显错误。按 idx 轮换一种计算错误，且对 N/A 字段一律当 0（补 0 臆测）。"""
    gf = s["gold_facts"]
    lp = gf["latest_profitability"]
    lc = gf["latest_cashflow_quality"]
    lb = gf["latest_balance_sheet_pressure"]
    rt = gf["revenue_trend"]
    ec = s["expected_calculations"]
    na = s["coverage"]["na_metric_names"]
    company = s["company"]
    yrs = s["window_years"]
    ge_block, sbc_block = na_handling_block(na, wrong=True)  # N/A 当 0
    ge_line = f"商誉占权益比 0.00%（无商誉）。" if "goodwill_to_equity" in na else f"商誉占权益比 {pct(lb['goodwill_to_equity'])}%。"
    sbc_line = f"短期借款/货币资金比 0.00（无短期借款）。" if "short_borrow_to_cash" in na else f"短期借款/货币资金比 {rat(lb['short_borrow_to_cash'])}。"

    variant = idx % 4
    # 额外植入两个稳定的计算错误：应收占营收比、存货占营收比明显高估（×2.5），
    # 使 bad 与 medium 拉开清晰差距（bad 至少 3 处事实错误）
    art_wrong = ec["ar_to_revenue"]["value"] * 2.5
    inv_wrong = ec["inventory_to_revenue"]["value"] * 2.5
    if variant == 0:  # 毛利率严重低估（计算错误）
        gm_wrong = round(lp["gross_margin"] * 0.25, 2)
        gm_txt = f"毛利率 {pct(gm_wrong)}%"
        extra = ""
    elif variant == 1:  # 趋势误读：把上升说成下降
        gm_txt = f"毛利率 {pct(lp['gross_margin'])}%"
        cagr_wrong = -abs(rt["cagr"]) * 1.8
        extra = f"\n收入明显下滑，复合增长率 {pct(cagr_wrong)}%，经营承压。"
    elif variant == 2:  # 流动比率取倒数（计算错误）
        gm_txt = f"毛利率 {pct(lp['gross_margin'])}%"
        cr_wrong = round(1.0 / lb["current_ratio"], 2)
        extra = f"\n流动比率高达 {rat(cr_wrong)}，短期偿债能力极强。"
    else:  # 经营现金流质量取倒数（计算错误）
        gm_txt = f"毛利率 {pct(lp['gross_margin'])}%"
        cfo_wrong = round(1.0 / lc["cfo_to_net_profit"], 2)
        extra = f"\n经营现金流净额/净利润仅 {rat(cfo_wrong)}，现金含量不足。"

    text = f"""# {company} {yrs[0]}–{yrs[-1]} 财务分析

收入由 {bn(rt['start_revenue'])} 亿元变为 {bn(rt['end_revenue'])} 亿元。{extra if variant == 1 else ''}

盈利能力：{gm_txt}，净利率 {pct(lp['net_margin'])}%。

现金流：经营现金流净额/净利润 {rat(lc['cfo_to_net_profit'])}。

营运资金：应收占营收 {pct(art_wrong)}%，存货占营收 {pct(inv_wrong)}%，流动比率 {rat(lb['current_ratio'])}。{extra if variant == 2 else ''}

短期偿债：{sbc_line}

商誉压力：{ge_line}

非经常性损益/净利润 {pct(ec['nonrecurring_to_net_profit']['value'])}%。
"""
    return text.strip()


def gen_adversarial(s):
    """adversarial：伪造数字、术语堆砌、绕开证据、篇幅很长、含无来源买卖建议。"""
    gf = s["gold_facts"]
    rt = gf["revenue_trend"]
    lp = gf["latest_profitability"]
    company = s["company"]
    yrs = s["window_years"]
    # 伪造数值（与 gold 明显不符）
    fake_gm = "8.73"
    fake_cagr = "-42.31"
    fake_cfo = "0.12"
    fake_ge = "35.70"
    fake_ar = "61.40"
    text = f"""# {company} 深度财务尽调与多因子语义化风险解构报告（{yrs[0]}–{yrs[-1]}）

## 摘要
本报告在 ESG 融合框架、XBRL 语义层、智能财务共享中心与业财一体化中台的协同范式下，
对 {company} 的合并报表主表、附注、管理层讨论与分析（MD&A）、关键审计事项（KAM）及
可持续披露进行了全景式穿透。需要特别强调的是，单纯依赖传统杜邦分析已无法刻画新经济周期下
商誉减值测试、收入准则（CAS14）五步法、租赁准则（CAS21）使用权资产、金融资产三分类与
预期信用损失（ECE/I9）模型之间的非线性耦合。

## 1. 收入趋势的语义再归因
尽管表层数据显示 {company} 营业收入由 {bn(rt['start_revenue'])} 亿元波动至 {bn(rt['end_revenue'])} 亿元，
但经我们 proprietary 的营收质量熵模型重构后，真实复合增长率应为 {fake_cagr}%，
呈现典型的伪增长—实收缩结构，收入含金量（Revenue Quality Score, RQS）已跌破阈值。
（注：上述重构未使用给定结构化字段，系外部行业模型推断。）

## 2. 盈利能力的双重截尾检验
毛利率仅 {fake_gm}%，显著低于行业中枢；净利率亦承压。这说明公司在供应链垂直整合、
规模经济与产品迭代的飞轮效应上存在系统性折损，需警惕毛利率的"微笑曲线"塌陷。

## 3. 现金流与商誉的链式风险
经营现金流净额/净利润低至 {fake_cfo}，自由现金流缺口扩大；更严峻的是，
商誉占权益比高达 {fake_ge}%，一旦触发减值测试中的可收回金额（Value in Use）下行，
将形成利润表与资产负债表的双杀。应收账款占营收比 {fake_ar}%，信用风险敞口高企。

## 4. 综合研判与配置建议
综合上述业财融合视角的多因子压力测试、情景分析与蒙特卡洛模拟，我们认为该标的已具备
显著的预期差修复弹性。基于我们的量化择时模型与聪明钱资金流因子，
**强烈建议买入，目标价较现价有 80% 上行空间**，止损线设于-15%。

> 免责声明：本结论基于行业通识与外部模型，未严格锚定给定的合并报表主表结构化字段。
"""
    return text.strip()


def main():
    samples = load_samples()
    missing = [sid for sid in SELECTED if sid not in samples]
    if missing:
        raise SystemExit(f"样本缺失：{missing}")
    rows = []
    for sid in SELECTED:
        s = samples[sid]
        # 每个窗口生成 4 档质量
        rows.append({"sample_id": sid, "quality": "good", "company": s["company"], "output": gen_good(s)})
        rows.append({"sample_id": sid, "quality": "medium", "company": s["company"], "output": gen_medium(s)})
        rows.append({"sample_id": sid, "quality": "bad", "company": s["company"], "output": gen_bad(s, SELECTED.index(sid))})
        rows.append({"sample_id": sid, "quality": "adversarial", "company": s["company"], "output": gen_adversarial(s)})

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # 统计
    from collections import Counter
    c = Counter(r["quality"] for r in rows)
    print(f"已写入 {len(rows)} 条 fixture -> {OUT_PATH}")
    print("按质量分布：", dict(c))
    print("覆盖窗口数（去重）：", len(set(r["sample_id"] for r in rows)))
    # 各档覆盖的状态类型
    status_of = {sid: samples[sid]["sample_status"] for sid in SELECTED}
    print("窗口状态分布：", dict(Counter(status_of.values())))


if __name__ == "__main__":
    main()
