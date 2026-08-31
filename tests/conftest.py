"""pytest 公共配置与夹具。

测试目标：把方案 §4.2 / §5.2 / §5.3 / §5.4 / §6.3 的方法学约束固化为可回归的断言，
使「注入金标准独立于规则」「主指标 micro 聚合」「D2/D3 严格口径」这些修复
不会在后续迭代中被悄悄改回去。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from generator.base import build_record_index, make_clean_company  # noqa: E402


@pytest.fixture()
def clean_company():
    """八类信号均不触发的清洁基底。"""
    return make_clean_company(name="测试清洁公司")


@pytest.fixture()
def record_index(clean_company):
    return build_record_index(clean_company)


def gold(signal_type: str, periods, severity: str = "high") -> dict:
    """构造一条最小金标准条目（与 set_eval 的输入契约一致）。"""
    return {"signal_type": signal_type, "periods": [str(p) for p in periods],
            "severity": severity}
