"""命令行入口（对应方案 §13 app/cli.py）。

封装评测编排，支持离线自检与真实 Hy3 扫描：
  python -m app.cli --offline      # 校验指标数学（不调 API）
  python -m app.cli --limit 3       # 真实 Hy3 冒烟（3 个样本）
  python -m app.cli                 # 全量
"""
from __future__ import annotations

import argparse
import sys

from eval.run_eval import main as _run_eval_main


def main() -> None:
    ap = argparse.ArgumentParser(description="FinSignal-Hy3 评测运行器")
    ap.add_argument("--offline", action="store_true", help="离线自检，不调 Hy3")
    ap.add_argument("--limit", type=int, default=None, help="仅跑前 N 个样本")
    args = ap.parse_args()

    argv = ["run_eval"]
    if args.offline:
        argv.append("--offline")
    if args.limit is not None:
        argv += ["--limit", str(args.limit)]
    sys.argv = argv
    _run_eval_main()


if __name__ == "__main__":
    main()
