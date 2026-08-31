"""安全公式注册表的唯一读取入口（对应方案 §5.2）。

prompt 侧（告诉模型可用的 formula_id）与评估侧（D2 校验）共用同一份
config/formula_registry.yaml，避免两边各自硬编码导致
「模型用的公式名不在注册表里 → D2 必然判错」这种评测污染。
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Dict, List

import yaml

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config")
_REGISTRY_PATH = os.path.join(_CONFIG, "formula_registry.yaml")


@lru_cache(maxsize=1)
def load_registry() -> Dict[str, dict]:
    with open(_REGISTRY_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["formulas"]


def formula_ids() -> List[str]:
    return list(load_registry().keys())


def available_formula_ids() -> List[str]:
    """当前数据 schema 下真正可复算的公式（available: true）。"""
    return [fid for fid, spec in load_registry().items() if spec.get("available", True)]


def formula_ids_for_signal(signal_type: str) -> List[str]:
    return [fid for fid, spec in load_registry().items()
            if signal_type in (spec.get("signal_types") or [])]


def formula_catalog_text(only_available: bool = True) -> str:
    """生成给模型看的 formula_id 目录，标注操作数与适用信号。"""
    reg = load_registry()
    lines: List[str] = []
    for fid, spec in reg.items():
        if only_available and not spec.get("available", True):
            continue
        sig = "/".join(spec.get("signal_types") or [])
        inputs = ", ".join(spec.get("inputs") or [])
        mode = "需要 t-1 与 t 两个年度" if spec.get("period_mode") == "pair" else "单年度"
        lines.append(
            f"- {fid}：{spec.get('description', '')}｜操作数 metric_key=[{inputs}]"
            f"｜{mode}｜适用 signal_type={sig}"
        )
    return "\n".join(lines)
