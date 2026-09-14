#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 3 —— Hy3 真实样本小规模实跑（small-scale real run）。

不提交 API key、不硬编码 key；key 缺失时优雅失败并打印配置说明。

选取代表样本（默认 7 条：4 READY + 3 PARTIAL）：
  READY : 宁德时代 300750_2021-2023、比亚迪 002594_2021-2023、
          万华化学 600309_2021-2023、三一重工 600031_2021-2023
  PARTIAL: 中兴通讯 000063_2021-2023、恒瑞医药 600276_2021-2023、隆基绿能 601012_2023-2025

复用 app/llm.Hy3Client（腾讯云 TokenHub OpenAI 兼容层）生成模型输出，
再用 eval/real_sample_eval.py 的 evaluate_one 评分逻辑做离线评测。

输出：
  - data/derived/hy3_real_outputs.jsonl   每条：sample_id, company, sample_status, prompt, model_output, generated_at, model_name
  - data/derived/hy3_real_eval_results.csv 每条：sample_id, company, sample_status, fact, na, coverage, sourcing, structure, total, deductions

用法：
  .venv/bin/python scripts/run_hy3_real_samples.py --dry-run   # 不调 API，仅校验样本选取与 prompt 构造
  .venv/bin/python scripts/run_hy3_real_samples.py --score-existing  # 不调 API，重评已有输出
  .venv/bin/python scripts/run_hy3_real_samples.py            # 需 .env 配置 HY3_API_KEY，真实生成并评测
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.real_sample_eval import evaluate_one, SAMPLES_PATH  # 纯规则评测逻辑，无外部依赖

OUT_JSONL = ROOT / "data" / "derived" / "hy3_real_outputs.jsonl"
OUT_CSV = ROOT / "data" / "derived" / "hy3_real_eval_results.csv"

# 代表样本：4 READY + 3 PARTIAL
SELECTED = [
    "300750_2021-2023",  # 宁德时代 READY
    "002594_2021-2023",  # 比亚迪   READY
    "600309_2021-2023",  # 万华化学 READY
    "600031_2021-2023",  # 三一重工 READY
    "000063_2021-2023",  # 中兴通讯 PARTIAL（goodwill_to_equity N/A）
    "600276_2021-2023",  # 恒瑞医药 PARTIAL（goodwill+short_borrow N/A）
    "601012_2023-2025",  # 隆基绿能 PARTIAL（goodwill_to_equity N/A）
]


def load_gold():
    return {s["sample_id"]: s for s in (
        json.loads(l) for l in SAMPLES_PATH.read_text(encoding="utf-8").splitlines() if l.strip())}


def build_prompt(s: dict) -> str:
    """把样本自带 user_prompt 与结构化年报数据拼接为发给 Hy3 的完整 prompt。"""
    data_json = json.dumps(s.get("input_financials", {}), ensure_ascii=False, indent=2)
    return (
        s["user_prompt"]
        + "\n\n# 结构化年报数据（合并报表主表口径，单位：元）\n```json\n"
        + data_json
        + "\n```"
    )


def config_help():
    print("=" * 64)
    print("未检测到 Hy3 API Key（HY3_API_KEY 未设置或 .env 缺失）。")
    print("请在本项目根目录创建 .env 并填入：")
    print("  HY3_BASE_URL=https://tokenhub.tencentmaas.com/v1")
    print("  HY3_API_KEY=<你的腾讯云 TokenHub API Key>")
    print("  HY3_MODEL=hy3")
    print("  # 可选：HY3_TEMPERATURE=0.2  HY3_TOP_P=1.0  HY3_TIMEOUT_SECONDS=120")
    print("详见 .env.example。配置后重新运行（去掉 --dry-run）即可生成真实模型输出。")
    print("=" * 64)


def run_dry(selected, samples):
    print("=" * 72)
    print("DRY-RUN：不调用 Hy3，仅校验样本选取与 prompt 构造")
    print("=" * 72)
    print(f"选定样本数：{len(selected)}")
    for sid in selected:
        s = samples[sid]
        print(f"  - {sid:22} {s['company']:6} [{s['sample_status']}]  "
              f"N/A={s['coverage'].get('na_metric_names', []) or '-'}")
    print("-" * 72)
    print("Prompt 预览（首条，截断 600 字符）：")
    preview = build_prompt(samples[selected[0]])
    print(preview[:600] + (" ..." if len(preview) > 600 else ""))
    print("-" * 72)
    print(f"实际运行将写入：\n  {OUT_JSONL}\n  {OUT_CSV}")
    print("DRY-RUN 完成 [PASS]（未产生任何 API 调用，未写入输出文件）")
    return 0


def write_results(results):
    fields = ["sample_id", "company", "sample_status", "fact", "na", "coverage",
              "sourcing", "structure", "total", "deductions"]
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for r in results:
            w.writerow(r)


def evaluate_outputs(outputs, samples):
    results = []
    for o in outputs:
        sid = o["sample_id"]
        s = samples[sid]
        res = evaluate_one(s, o["model_output"])
        results.append({
            "sample_id": sid,
            "company": s["company"],
            "sample_status": s["sample_status"],
            "fact": res["fact"], "na": res["na"], "coverage": res["coverage"],
            "sourcing": res["sourcing"], "structure": res["structure"], "total": res["total"],
            "deductions": " | ".join(res["deductions"]) or "-",
        })
    return results


def run_score_existing(samples):
    if not OUT_JSONL.exists():
        print(f"[ERROR] 找不到已有 Hy3 输出文件：{OUT_JSONL}")
        return 2
    outputs = [json.loads(line) for line in OUT_JSONL.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    missing = [o["sample_id"] for o in outputs if o["sample_id"] not in samples]
    if missing:
        print(f"[ERROR] 输出文件中存在 gold 样本缺失的 sample_id：{missing}")
        return 2
    results = evaluate_outputs(outputs, samples)
    write_results(results)
    for r in results:
        print(f"  {r['sample_id']:22} total={r['total']:.1f}")
    avg = sum(r["total"] for r in results) / len(results) if results else 0.0
    print("=" * 72)
    print(f"已重评已有 Hy3 输出：{len(results)} 条 -> {OUT_CSV}")
    print(f"均分 total = {avg:.2f}")
    print("=" * 72)
    return 0


def run_real(selected, samples):
    # 延迟导入，避免无 key / 无依赖时影响 --dry-run
    try:
        from app.llm import Hy3Client
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] 无法导入 app.llm（依赖 openai/python-dotenv？）：{e}")
        return 2
    try:
        client = Hy3Client()
    except RuntimeError:
        config_help()
        return 2

    outputs = []
    for sid in selected:
        s = samples[sid]
        prompt = build_prompt(s)
        try:
            model_output = client.chat([{"role": "user", "content": prompt}])
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] 样本 {sid} Hy3 调用失败：{e}")
            return 2
        outputs.append({
            "sample_id": sid,
            "company": s["company"],
            "sample_status": s["sample_status"],
            "prompt": prompt,
            "model_output": model_output,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_name": client.model,
        })
        res = evaluate_one(s, model_output)
        print(f"  {sid:22} total={res['total']:.1f}")

    # 写 jsonl
    OUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
    with OUT_JSONL.open("w", encoding="utf-8") as f:
        for o in outputs:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    results = evaluate_outputs(outputs, samples)
    write_results(results)

    avg = sum(r["total"] for r in results) / len(results) if results else 0.0
    print("=" * 72)
    print(f"真实运行完成：生成 {len(outputs)} 条 Hy3 输出 -> {OUT_JSONL}")
    print(f"评测结果 -> {OUT_CSV}")
    print(f"均分 total = {avg:.2f}")
    print("=" * 72)
    return 0


def main():
    ap = argparse.ArgumentParser(description="Hy3 真实样本小规模实跑（Phase 3）")
    ap.add_argument("--dry-run", action="store_true", help="不调用 API，仅校验样本选取与 prompt 构造")
    ap.add_argument("--score-existing", action="store_true", help="不调用 API，仅重评已有 hy3_real_outputs.jsonl")
    args = ap.parse_args()

    samples = load_gold()
    missing = [sid for sid in SELECTED if sid not in samples]
    if missing:
        print(f"[WARN] 以下选定样本在 {SAMPLES_PATH.name} 中缺失，已跳过：{missing}")
    selected = [sid for sid in SELECTED if sid in samples]
    if not selected:
        print("[ERROR] 无可用样本")
        return 2

    if args.dry_run:
        return run_dry(selected, samples)
    if args.score_existing:
        return run_score_existing(samples)
    return run_real(selected, samples)


if __name__ == "__main__":
    raise SystemExit(main())
