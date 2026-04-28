#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BidKing 游戏日志解析器 — CLI 入口

用法:
  python parse_log.py                  # 自动查找日志文件，批量处理
  python parse_log.py --tail           # 实时监听模式
  python parse_log.py --log <路径>     # 指定日志文件
  python parse_log.py --output out.txt # 结果写入文件

业务逻辑全部在 getlog/ 包中，本文件仅负责参数解析和运行环境配置。
"""

import argparse
import os
import sys

from getlog.constants import CSV_PATH, DEFAULT_GAME_LOG, LOCAL_LOG
from getlog.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(
        description='BidKing 游戏日志解析器 — 逐回合输出物品判断信息',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python parse_log.py                        # 自动查找日志，批量处理
  python parse_log.py --tail                 # 实时监听游戏日志
  python parse_log.py --log Player.log       # 指定日志文件
  python parse_log.py --output result.txt   # 输出到文件""",
    )
    parser.add_argument(
        '--log', default=None,
        help='日志文件路径（默认：优先 ./Player.log，其次游戏目录）',
    )
    parser.add_argument(
        '--csv', default=CSV_PATH,
        help=f'物品价格 CSV 路径（默认: {CSV_PATH}）',
    )
    parser.add_argument(
        '--tail', action='store_true',
        help='实时监听模式：持续读取追加的日志行',
    )
    parser.add_argument(
        '--output', default=None,
        help='结果输出文件路径（默认输出到控制台）',
    )
    args = parser.parse_args()

    # 确定日志路径
    log_path = args.log
    if log_path is None:
        if os.path.exists(LOCAL_LOG):
            log_path = LOCAL_LOG
        elif os.path.exists(DEFAULT_GAME_LOG):
            log_path = DEFAULT_GAME_LOG
        else:
            print(
                f"错误: 找不到日志文件。请用 --log 参数指定路径。\n"
                f"  尝试过: {LOCAL_LOG}\n"
                f"          {DEFAULT_GAME_LOG}",
                file=sys.stderr,
            )
            sys.exit(1)

    csv_path = args.csv
    if not os.path.exists(csv_path):
        print(f"错误: 找不到CSV文件: {csv_path}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as out_file:
            try:
                run(log_path, csv_path, tail=args.tail, out=out_file)
            except KeyboardInterrupt:
                print("\n已停止监听。", file=sys.stderr)
        print(f"结果已写入: {args.output}", file=sys.stderr)
    else:
        # Windows 控制台：强制 stdout 使用 utf-8 + 行缓冲，确保实时刷新
        if sys.platform == 'win32':
            import io as _io
            sys.stdout = _io.TextIOWrapper(
                sys.stdout.buffer, encoding='utf-8', errors='replace',
                line_buffering=True,
            )
        else:
            sys.stdout.reconfigure(line_buffering=True)
        try:
            run(log_path, csv_path, tail=args.tail, out=sys.stdout)
        except KeyboardInterrupt:
            print("\n已停止监听。", file=sys.stderr)


if __name__ == '__main__':
    main()
