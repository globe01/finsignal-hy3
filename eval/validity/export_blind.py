"""盲评数据导出（提交前收口，任务一）。

从一次固定在线评测的 ``cases*.json`` 抽取**盲评**所需材料，供人工标注者独立判断：
1. 匿名 ``case_id``（``case_000`` …，不泄漏样本身份 / 注入类型 / 严重度 / 类别）；
2. 模型实际看到的财务输入文本 ``input_text``（由 ``generator/base.py::company_to_text``
   确定性重建，与扫描 prompt 完全一致，含长文本 / 术语样本的叙述段）；
3. 模型产出的卡片 ``cards``（``cards_full``，即模型自陈输出）。

**绝不**写入：``ground_truth`` / ``gold`` / ``injection`` / ``metrics`` / ``judge`` /
``hy3_judge`` / ``meta``（含 ``meta.inject`` / ``meta.severity`` / ``meta.category``）。
标注者不应在标注前接触任何金标准，否则会被锚定、失去独立性（见 ``docs/annotation_guide.md``）。

用法：
  python -m eval.validity.export_blind \\
      --cases results/online_local/cases_run1.json \\
      --out results/blind/cases_run1_blind.json

产物为真实模型输出，本地留档、不入库（已加入 ``.gitignore`` 的 ``results/blind/``）。
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List, Optional, Tuple

from eval.run_eval import build_dataset
from generator.base import company_to_text

# 盲评输出顶层仅允许这些键；任何金标准 / 评估字段都不允许出现。
_ALLOWED_TOP_KEYS = ("case_id", "input_text", "cards")


def build_company_lookup() -> Dict[Tuple, object]:
    """由 ``build_dataset()`` 构建 (name, inject) -> Company 的查表。

    5 个边界样本同名（均为「阈下边界公司」），仅靠 ``inject``（信号类型）区分，
    故用复合键 ``(name, inject)`` 唯一定位，确保重建出的 ``input_text`` 与在线评测一致。
    """
    lookup: Dict[Tuple, object] = {}
    for case in build_dataset():
        meta = case.get("meta", {})
        key = (meta.get("name"), meta.get("inject"))
        lookup[key] = case["company"]
    return lookup


def export_blind(cases: List[Dict], out_path: Optional[str] = None,
                 company_lookup: Optional[Dict[Tuple, object]] = None) -> List[Dict]:
    """将在线 cases 转为盲评条目列表。

    返回的每个条目只含 ``case_id`` / ``input_text`` / ``cards``，不含任何金标准或评估字段。
    """
    if company_lookup is None:
        company_lookup = build_company_lookup()
    rows: List[Dict] = []
    for ci, c in enumerate(cases):
        case_id = f"case_{ci:03d}"
        meta = c.get("meta", {})
        company = company_lookup.get((meta.get("name"), meta.get("inject")))
        # 找不到对应基底时不编造文本，仅给空串并保留卡片（不应发生：build_dataset 确定性）
        input_text = company_to_text(company) if company is not None else ""
        cards = c.get("cards_full", []) or []
        rows.append({"case_id": case_id, "input_text": input_text, "cards": cards})
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2, default=str)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(
        description="导出盲评数据（匿名 case_id + 财务输入 + 模型卡片，剥离金标准）")
    ap.add_argument("--cases", required=True,
                    help="在线评测 cases json（如 results/online_local/cases_run1.json）")
    ap.add_argument("--out", default=None,
                    help="盲评输出 json 路径（默认打印到 stdout）")
    args = ap.parse_args()
    with open(args.cases, encoding="utf-8") as f:
        cases = json.load(f)
    rows = export_blind(cases, out_path=args.out)
    if not args.out:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
