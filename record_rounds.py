#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出每回合 grid 分布记录（独立工具）。

示例:
  python record_rounds.py
  python record_rounds.py --log Player.log --records-dir records
  python record_rounds.py --last-game-only
  python record_rounds.py --include-item-events
"""

import argparse
import os
import sys

from getlog.constants import CSV_PATH, DEFAULT_GAME_LOG, LOCAL_LOG
from getlog.round_recorder import export_round_records_to_directory


def _project_root() -> str:
    """项目根目录（record_rounds.py 所在目录）。"""
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
        description="导出 BidKing 每回合 grid 分布与游戏结束真实揭晓布局（JSON）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--log", default=None, help="日志文件路径（默认自动查找）")
    parser.add_argument("--csv", default=CSV_PATH, help=f"物品CSV路径（默认: {CSV_PATH}）")
    parser.add_argument(
        "--records-dir",
        default="records",
        help="输出目录（默认: records）",
    )
    parser.add_argument(
        "--include-item-events",
        action="store_true",
        help="包含 S2C_39 实时道具事件触发的中间快照",
    )
    parser.add_argument(
        "--last-game-only",
        action="store_true",
        help="仅导出最后一局（默认导出日志中全部对局）",
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

    # records 目录固定在项目根目录；若传相对路径，也以项目根目录为基准。
    records_dir = args.records_dir
    if not os.path.isabs(records_dir):
        records_dir = os.path.join(_project_root(), records_dir)

    result = export_round_records_to_directory(
        log_path=log_path,
        csv_path=args.csv,
        records_dir=records_dir,
        include_item_events=args.include_item_events,
        last_game_only=args.last_game_only,
    )

    print(
        f"已导出 {result['game_count']} 局记录到目录: {result['records_dir']}\n"
        f"清单文件: {result['manifest']}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
