#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
读取最后一局的最后一轮数据，并导出 JSON。

示例:
  python read_last_round.py
  python read_last_round.py --log Player.log
  python read_last_round.py --output last_round.json
"""

import argparse
import os
import sys

from getlog.constants import CSV_PATH, DEFAULT_GAME_LOG, LOCAL_LOG
from getlog.round_recorder import export_last_round_from_log


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _resolve_log_path(arg_log: str) -> str:
    if arg_log:
        return arg_log
    if os.path.exists(LOCAL_LOG):
        return LOCAL_LOG
    if os.path.exists(DEFAULT_GAME_LOG):
        return DEFAULT_GAME_LOG
    raise FileNotFoundError(
        f"找不到日志文件。请用 --log 指定路径。\n"
        f"尝试过: {LOCAL_LOG}\n"
        f"        {DEFAULT_GAME_LOG}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="读取最后一局最后一轮数据并导出 JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log", default=None, help="日志文件路径（默认自动查找）")
    parser.add_argument("--csv", default=CSV_PATH, help=f"物品CSV路径（默认: {CSV_PATH}）")
    parser.add_argument(
        "--output",
        default=os.path.join("records", "last_round.json"),
        help="输出 JSON 路径（默认: records/last_round.json）",
    )
    args = parser.parse_args()

    try:
        log_path = _resolve_log_path(args.log)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.csv):
        print(f"错误: 找不到CSV文件: {args.csv}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output
    if not os.path.isabs(output_path):
        output_path = os.path.join(_project_root(), output_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    data = export_last_round_from_log(log_path, args.csv, output_path)
    print(
        f"已导出最后一轮数据:\n"
        f"  对局: {data.get('game_uid', '')}\n"
        f"  地图: {data.get('map_id', 0)}\n"
        f"  最后完成回合: {data.get('last_completed_round')}\n"
        f"  文件: {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()

