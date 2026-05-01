#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BidKing 物品格局可视化

解析日志文件的最后一局，用 10×30 网格地图直观展示物品分布。
每个格子按品质着色，显示类别/价格信息，点击可弹出候选物品详情；弹窗内可双击某行确认该候选为当前格物品（用于估算与展示）。

用法:
  python show_grid.py                  # 自动查找日志（优先 ./Player.log）
  python show_grid.py --log <路径>     # 指定日志文件
  python show_grid.py --csv <路径>     # 指定物品 CSV 文件
"""

import argparse
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from getlog.constants import CSV_PATH, DEFAULT_GAME_LOG, LOCAL_COPY_LOG, LOCAL_LOG
from getlog.grid_view import GridWindow
from getlog.runner import parse_last_game, parse_last_game_rounds


def _default_log_path() -> str:
    """默认优先使用游戏本地日志路径，其次才使用项目目录中的日志副本。"""
    candidates = [
        DEFAULT_GAME_LOG,
        os.path.join(os.getcwd(), LOCAL_LOG),
        os.path.join(os.getcwd(), LOCAL_COPY_LOG),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return DEFAULT_GAME_LOG


def _open_grid(log_path: str, csv_path: str, tail: bool) -> None:
    if tail:
        state, csv_index, csv_items = parse_last_game(log_path, csv_path)
        if state is None:
            from getlog.models import GameState
            state = GameState()
        GridWindow(state, csv_index, csv_items, log_path=log_path).run()
        return

    snapshots, csv_index, csv_items = parse_last_game_rounds(log_path, csv_path)
    if not snapshots:
        raise RuntimeError("未找到任何对局数据，请确认日志文件包含游戏记录。")
    first_state = snapshots[0][1]
    GridWindow(first_state, csv_index, csv_items, snapshots=snapshots).run()


def _show_start_page(default_log: str, csv_path: str) -> None:
    """exe/无参数启动页：选择日志路径和实时/回放模式。"""
    root = tk.Tk()
    root.title("BidKing 鉴影可视化 - 启动")
    root.configure(bg='#1a1a2e')
    root.resizable(False, False)

    log_var = tk.StringVar(value=default_log)
    mode_var = tk.StringVar(value='replay')

    frame = tk.Frame(root, bg='#1a1a2e', padx=16, pady=14)
    frame.pack(fill='both', expand=True)

    tk.Label(
        frame, text="BidKing 物品格局",
        bg='#1a1a2e', fg='#e8e8f0',
        font=('微软雅黑', 14, 'bold'),
    ).pack(anchor='w', pady=(0, 12))

    tk.Label(
        frame, text="Log 文件路径",
        bg='#1a1a2e', fg='#aaaabb',
        font=('微软雅黑', 9),
    ).pack(anchor='w')

    path_row = tk.Frame(frame, bg='#1a1a2e')
    path_row.pack(fill='x', pady=(4, 10))
    tk.Entry(
        path_row, textvariable=log_var, width=58,
        bg='#252538', fg='#ffffff', insertbackground='#ffffff',
        relief='flat', font=('Consolas', 9),
    ).pack(side='left', fill='x', expand=True, ipady=4)

    def browse_log() -> None:
        chosen = filedialog.askopenfilename(
            title="选择 Player.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if chosen:
            log_var.set(chosen)

    tk.Button(
        path_row, text="浏览...",
        command=browse_log,
        bg='#334466', fg='#dde8ff',
        relief='flat', padx=10, pady=3,
        font=('微软雅黑', 9),
    ).pack(side='left', padx=(8, 0))

    mode_box = tk.Frame(frame, bg='#1a1a2e')
    mode_box.pack(fill='x', pady=(0, 14))
    tk.Radiobutton(
        mode_box, text="回放模式（解析最后一局，可翻回合）",
        variable=mode_var, value='replay',
        bg='#1a1a2e', fg='#dde8ff', selectcolor='#252538',
        activebackground='#1a1a2e', activeforeground='#ffffff',
        font=('微软雅黑', 9),
    ).pack(anchor='w')
    tk.Radiobutton(
        mode_box, text="实时模式（监听新增日志）",
        variable=mode_var, value='tail',
        bg='#1a1a2e', fg='#dde8ff', selectcolor='#252538',
        activebackground='#1a1a2e', activeforeground='#ffffff',
        font=('微软雅黑', 9),
    ).pack(anchor='w')

    tips = (
        "操作提示：\n"
        "  · 左键点击物品：弹出候选列表（概率、加权估价等）\n"
        "  · 候选弹窗：单击一行预览；双击该行，或点「确认所选后选项」，将物品设为「手动确认」\n"
        "    （确认后参与网格总价估算与品质显示；若日志已给出精确 ItemCid 仍以日志为准）\n"
        "  · 左键在空白格按住拖动：新增手动画框\n"
        "  · 未知大小物品可拖动四边白色把手调整大小\n"
        "  · 右键点击手动画框：删除该画框\n"
        "  · 鼠标滚轮：上下浏览 10×30 表格"
    )
    tk.Label(
        frame,
        text=tips,
        bg='#222233', fg='#c8d0e8',
        font=('微软雅黑', 8),
        justify='left', anchor='w',
        padx=10, pady=8,
    ).pack(fill='x', pady=(0, 14))

    def start() -> None:
        log_path = log_var.get().strip()
        if not os.path.exists(log_path):
            messagebox.showerror("错误", f"找不到日志文件:\n{log_path}")
            return
        if not os.path.exists(csv_path):
            messagebox.showerror("错误", f"找不到物品数据:\n{csv_path}")
            return
        tail = mode_var.get() == 'tail'
        root.destroy()
        try:
            _open_grid(log_path, csv_path, tail)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc))

    tk.Button(
        frame, text="启动",
        command=start,
        bg='#5566aa', fg='#ffffff',
        relief='flat', padx=28, pady=7,
        font=('微软雅黑', 10, 'bold'),
    ).pack(anchor='e')

    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description='BidKing 物品格局可视化 — 网格地图展示',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python show_grid.py                        # 自动查找日志
  python show_grid.py --log Player.log       # 指定日志文件""",
    )
    parser.add_argument('--log', default=None, help='日志文件路径')
    parser.add_argument('--csv', default=CSV_PATH, help=f'物品 CSV 路径（默认: {CSV_PATH}）')
    parser.add_argument('--tail', action='store_true',
                        help='实时监听模式：持续监听日志新增内容并自动刷新界面')
    args = parser.parse_args()

    if len(sys.argv) == 1:
        _show_start_page(_default_log_path(), args.csv)
        return

    # 确定日志路径
    log_path = args.log
    if log_path is None:
        if os.path.exists(DEFAULT_GAME_LOG):
            log_path = DEFAULT_GAME_LOG
        elif os.path.exists(LOCAL_LOG):
            log_path = LOCAL_LOG
        elif os.path.exists(LOCAL_COPY_LOG):
            log_path = LOCAL_COPY_LOG
        else:
            print(
                f"错误: 找不到日志文件。请用 --log 参数指定路径。\n"
                f"  尝试过: {DEFAULT_GAME_LOG}\n  {LOCAL_LOG}\n  {LOCAL_COPY_LOG}",
                file=sys.stderr,
            )
            sys.exit(1)

    csv_path = args.csv
    if not os.path.exists(csv_path):
        print(f"错误: 找不到CSV文件: {csv_path}", file=sys.stderr)
        sys.exit(1)

    print(f"解析日志: {log_path} ...", file=sys.stderr)

    _open_grid(log_path, csv_path, args.tail)


if __name__ == '__main__':
    main()
