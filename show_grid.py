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
import json
import os
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from getlog.constants import CSV_PATH, DEFAULT_GAME_LOG, LOCAL_COPY_LOG, LOCAL_LOG
from getlog.grid_view import GridWindow
from getlog.item_db import load_csv
from getlog.models import GameState
from getlog.round_recorder import (
    export_round_records_to_directory,
    format_game_real_time,
    load_round_record_game_for_grid,
)
from getlog.runner import parse_last_game_rounds


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


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


def _record_timestamp_from_name(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    prefix_match = re.match(r"(\d+)_\d{8}_\d{6}_map\d+", name)
    if prefix_match:
        return prefix_match.group(1)
    if "_uid_" in name:
        uid_part = name.split("_uid_", 1)[1]
        digit_groups = re.findall(r"\d+", uid_part)
        if digit_groups:
            return max(digit_groups, key=len)
    prefix_match = re.match(r"(\d+)_map\d+", name)
    if prefix_match:
        return prefix_match.group(1)
    digit_groups = re.findall(r"\d+", name)
    if digit_groups:
        return max(digit_groups, key=len)
    return ""


def _record_option_label(meta: Dict[str, object]) -> str:
    timestamp = str(meta.get("timestamp", "") or "unknown_time")
    real_time = str(meta.get("real_time", "") or "unknown_time")
    map_id = meta.get("map_id", "?")
    game_uid = str(meta.get("game_uid", "") or "")
    ended = "已结束" if meta.get("ended", False) else "未结束"
    return f"{timestamp} | {real_time} | 地图 {map_id} | {ended} | {game_uid}"


def _record_real_time_from_name(filename: str) -> str:
    name = os.path.splitext(os.path.basename(filename))[0]
    match = re.match(r"\d+_(\d{8}_\d{6})_map\d+", name)
    if match:
        packed = match.group(1)
        return (
            f"{packed[0:4]}-{packed[4:6]}-{packed[6:8]} "
            f"{packed[9:11]}:{packed[11:13]}:{packed[13:15]}"
        )
    return ""


def _load_record_options(records_dir: str) -> List[Dict[str, str]]:
    manifest_path = os.path.join(records_dir, "manifest.json")
    options: List[Dict[str, str]] = []
    seen_paths = set()
    if os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            manifest_games = manifest.get("games", [])
            if isinstance(manifest_games, list):
                for idx, game in enumerate(manifest_games):
                    if not isinstance(game, dict):
                        continue
                    filename = str(game.get("file", "") or "")
                    if not filename:
                        continue
                    path = os.path.join(records_dir, filename)
                    if not os.path.exists(path):
                        continue
                    timestamp = str(
                        game.get("timestamp")
                        or _record_timestamp_from_name(filename)
                        or ""
                    )
                    real_time = str(
                        game.get("real_time")
                        or _record_real_time_from_name(filename)
                        or format_game_real_time(timestamp)
                        or ""
                    )
                    meta = {
                        "path": path,
                        "filename": filename,
                        "timestamp": timestamp,
                        "real_time": real_time,
                        "map_id": game.get("map_id", "?"),
                        "game_uid": str(game.get("game_uid", "") or ""),
                        "ended": bool(game.get("ended", False)),
                        "started_seq": str(game.get("started_seq", idx + 1)),
                    }
                    options.append(
                        {
                            "path": path,
                            "label": _record_option_label(meta),
                            "timestamp": timestamp,
                            "real_time": real_time,
                            "started_seq": str(meta["started_seq"]),
                        }
                    )
                    seen_paths.add(os.path.normcase(os.path.abspath(path)))
        except Exception:
            options = []

    if os.path.isdir(records_dir):
        for name in os.listdir(records_dir):
            if not name.endswith(".json") or name == "manifest.json":
                continue
            if not re.search(r"_map\d+_uid_.+\.json$", name):
                continue
            path = os.path.join(records_dir, name)
            norm_path = os.path.normcase(os.path.abspath(path))
            if norm_path in seen_paths:
                continue
            timestamp = _record_timestamp_from_name(name)
            real_time = _record_real_time_from_name(name) or format_game_real_time(timestamp)
            map_match = re.search(r"_map(\d+)", name)
            uid_match = re.search(r"_uid_(.+)\.json$", name)
            meta = {
                "path": path,
                "filename": name,
                "timestamp": timestamp,
                "real_time": real_time,
                "map_id": map_match.group(1) if map_match else "?",
                "game_uid": uid_match.group(1) if uid_match else "",
                "ended": True,
            }
            options.append(
                {
                    "path": path,
                    "label": _record_option_label(meta),
                    "timestamp": timestamp,
                    "real_time": real_time,
                    "started_seq": "0",
                }
            )

    options.sort(
        key=lambda item: (
            str(item.get("timestamp", "")),
            str(item.get("started_seq", "")),
            str(item.get("path", "")),
        ),
        reverse=True,
    )
    return options


def _default_record_option(records_dir: str) -> str:
    options = _load_record_options(records_dir)
    if options:
        return options[0]["path"]
    return ""


def _open_grid(
    log_path: str,
    csv_path: str,
    tail: bool,
    *,
    master: Optional[tk.Misc] = None,
) -> None:
    if tail:
        csv_index, csv_items = load_csv(csv_path)
        GridWindow(
            GameState(),
            csv_index,
            csv_items,
            master=master,
            log_path=log_path,
            recover_live_state=True,
        ).run()
        return

    snapshots, csv_index, csv_items = parse_last_game_rounds(log_path, csv_path)
    if not snapshots:
        raise RuntimeError("未找到任何对局数据，请确认日志文件包含游戏记录。")
    first_state = snapshots[0][1]
    GridWindow(
        first_state,
        csv_index,
        csv_items,
        master=master,
        snapshots=snapshots,
    ).run()


def _open_record_grid(
    record_path: str,
    csv_path: str,
    *,
    master: Optional[tk.Misc] = None,
) -> None:
    snapshots, csv_index, csv_items = load_round_record_game_for_grid(
        record_path, csv_path
    )
    if not snapshots:
        raise RuntimeError("记录文件中没有可回放的快照。")
    first_state = snapshots[0][1]
    GridWindow(
        first_state,
        csv_index,
        csv_items,
        master=master,
        snapshots=snapshots,
    ).run()


def _export_records(
    log_path: str,
    csv_path: str,
    records_dir: str,
    *,
    include_item_events: bool = False,
    last_game_only: bool = False,
) -> dict:
    return export_round_records_to_directory(
        log_path=log_path,
        csv_path=csv_path,
        records_dir=records_dir,
        include_item_events=include_item_events,
        last_game_only=last_game_only,
    )


def _show_start_page(default_log: str, csv_path: str) -> None:
    """exe/无参数启动页：选择日志路径、记录文件路径，以及实时/回放模式。"""
    root = tk.Tk()
    root.title("BidKing 鉴影可视化 - 启动")
    root.configure(bg="#1a1a2e")
    root.resizable(False, False)

    records_dir = os.path.join(_project_root(), "records")
    log_var = tk.StringVar(value=default_log)
    record_var = tk.StringVar(value=_default_record_option(records_dir))
    mode_var = tk.StringVar(value="log_replay")
    record_options = _load_record_options(records_dir)
    record_label_to_path = {item["label"]: item["path"] for item in record_options}
    selected_record_label = tk.StringVar(
        value=next(
            (
                item["label"]
                for item in record_options
                if item["path"] == record_var.get()
            ),
            "",
        )
    )

    frame = tk.Frame(root, bg="#1a1a2e", padx=16, pady=14)
    frame.pack(fill="both", expand=True)

    tk.Label(
        frame,
        text="BidKing 物品格局",
        bg="#1a1a2e",
        fg="#e8e8f0",
        font=("微软雅黑", 14, "bold"),
    ).pack(anchor="w", pady=(0, 12))

    tk.Label(
        frame,
        text="Log 文件路径",
        bg="#1a1a2e",
        fg="#aaaabb",
        font=("微软雅黑", 9),
    ).pack(anchor="w")

    path_row = tk.Frame(frame, bg="#1a1a2e")
    path_row.pack(fill="x", pady=(4, 10))
    tk.Entry(
        path_row,
        textvariable=log_var,
        width=58,
        bg="#252538",
        fg="#ffffff",
        insertbackground="#ffffff",
        relief="flat",
        font=("Consolas", 9),
    ).pack(side="left", fill="x", expand=True, ipady=4)

    def browse_log() -> None:
        chosen = filedialog.askopenfilename(
            title="选择 Player.log",
            filetypes=[("Log files", "*.log"), ("All files", "*.*")],
        )
        if chosen:
            log_var.set(chosen)

    tk.Button(
        path_row,
        text="浏览...",
        command=browse_log,
        bg="#334466",
        fg="#dde8ff",
        relief="flat",
        padx=10,
        pady=3,
        font=("微软雅黑", 9),
    ).pack(side="left", padx=(8, 0))

    tk.Label(
        frame,
        text="回放记录",
        bg="#1a1a2e",
        fg="#aaaabb",
        font=("微软雅黑", 9),
    ).pack(anchor="w")

    record_row = tk.Frame(frame, bg="#1a1a2e")
    record_row.pack(fill="x", pady=(4, 10))
    record_combo = ttk.Combobox(
        record_row,
        textvariable=selected_record_label,
        width=72,
        state="readonly",
        values=[item["label"] for item in record_options],
    )
    record_combo.pack(side="left", fill="x", expand=True, ipady=3)

    def refresh_record_options(select_path: Optional[str] = None) -> None:
        nonlocal record_options, record_label_to_path
        record_options = _load_record_options(records_dir)
        record_label_to_path = {
            item["label"]: item["path"] for item in record_options
        }
        labels = [item["label"] for item in record_options]
        record_combo["values"] = labels
        chosen_path = select_path or record_var.get()
        chosen_label = next(
            (
                item["label"]
                for item in record_options
                if item["path"] == chosen_path
            ),
            labels[0] if labels else "",
        )
        selected_record_label.set(chosen_label)
        record_var.set(record_label_to_path.get(chosen_label, ""))

    def on_record_selected(_event: Optional[tk.Event] = None) -> None:
        record_var.set(record_label_to_path.get(selected_record_label.get(), ""))

    record_combo.bind("<<ComboboxSelected>>", on_record_selected)
    refresh_record_options()

    tk.Button(
        record_row,
        text="刷新",
        command=refresh_record_options,
        bg="#334466",
        fg="#dde8ff",
        relief="flat",
        padx=10,
        pady=3,
        font=("微软雅黑", 9),
    ).pack(side="left", padx=(8, 0))

    mode_box = tk.Frame(frame, bg="#1a1a2e")
    mode_box.pack(fill="x", pady=(0, 14))
    tk.Radiobutton(
        mode_box,
        text="日志回放（解析最后一局，可翻回合）",
        variable=mode_var,
        value="log_replay",
        bg="#1a1a2e",
        fg="#dde8ff",
        selectcolor="#252538",
        activebackground="#1a1a2e",
        activeforeground="#ffffff",
        font=("微软雅黑", 9),
    ).pack(anchor="w")
    tk.Radiobutton(
        mode_box,
        text="实时模式（监听新增日志）",
        variable=mode_var,
        value="tail",
        bg="#1a1a2e",
        fg="#dde8ff",
        selectcolor="#252538",
        activebackground="#1a1a2e",
        activeforeground="#ffffff",
        font=("微软雅黑", 9),
    ).pack(anchor="w")
    tk.Radiobutton(
        mode_box,
        text="记录回放（读取导出的 JSON，可回放历史对局）",
        variable=mode_var,
        value="record_replay",
        bg="#1a1a2e",
        fg="#dde8ff",
        selectcolor="#252538",
        activebackground="#1a1a2e",
        activeforeground="#ffffff",
        font=("微软雅黑", 9),
    ).pack(anchor="w")

    export_bar = tk.Frame(frame, bg="#1a1a2e")
    export_bar.pack(fill="x", pady=(0, 12))
    tk.Label(
        export_bar,
        text=f"记录导出目录: {records_dir}",
        bg="#1a1a2e",
        fg="#8f9bb8",
        font=("微软雅黑", 8),
    ).pack(side="left")

    def export_records(last_game_only: bool) -> None:
        log_path = log_var.get().strip()
        if not os.path.exists(log_path):
            messagebox.showerror("错误", f"找不到日志文件:\n{log_path}")
            return
        if not os.path.exists(csv_path):
            messagebox.showerror("错误", f"找不到物品数据:\n{csv_path}")
            return
        try:
            result = _export_records(
                log_path=log_path,
                csv_path=csv_path,
                records_dir=records_dir,
                include_item_events=False,
                last_game_only=last_game_only,
            )
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        written_files = result.get("files", [])
        if written_files:
            refresh_record_options(select_path=written_files[-1])
            mode_var.set("record_replay")
        messagebox.showinfo(
            "导出完成",
            (
                f"新增导出 {result.get('game_count', 0)} 局记录；"
                f"当前共有 {result.get('total_game_count', result.get('game_count', 0))} 局已结束记录。\n"
                f"目录:\n{result.get('records_dir', records_dir)}"
            ),
        )

    btn_export_all = tk.Button(
        export_bar,
        text="导出全部记录",
        command=lambda: export_records(False),
        bg="#375f3c",
        fg="#ffffff",
        relief="flat",
        padx=10,
        pady=3,
        font=("微软雅黑", 9),
    )
    btn_export_all.pack(side="right")
    tk.Button(
        export_bar,
        text="导出最后一局",
        command=lambda: export_records(True),
        bg="#466a86",
        fg="#ffffff",
        relief="flat",
        padx=10,
        pady=3,
        font=("微软雅黑", 9),
    ).pack(side="right", padx=(0, 8))

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
        bg="#222233",
        fg="#c8d0e8",
        font=("微软雅黑", 8),
        justify="left",
        anchor="w",
        padx=10,
        pady=8,
    ).pack(fill="x", pady=(0, 14))

    def start() -> None:
        if not os.path.exists(csv_path):
            messagebox.showerror("错误", f"找不到物品数据:\n{csv_path}")
            return
        mode = mode_var.get()
        on_record_selected()
        log_path = log_var.get().strip()
        record_path = record_var.get().strip()
        if mode in {"log_replay", "tail"} and not os.path.exists(log_path):
            messagebox.showerror("错误", f"找不到日志文件:\n{log_path}")
            return
        if mode == "record_replay" and not os.path.exists(record_path):
            messagebox.showerror("错误", f"找不到回放记录文件:\n{record_path}")
            return
        try:
            if mode == "record_replay":
                _open_record_grid(record_path, csv_path, master=root)
            else:
                _open_grid(log_path, csv_path, mode == "tail", master=root)
        except Exception as exc:
            messagebox.showerror("启动失败", str(exc), parent=root)

    tk.Button(
        frame,
        text="启动",
        command=start,
        bg="#5566aa",
        fg="#ffffff",
        relief="flat",
        padx=28,
        pady=7,
        font=("微软雅黑", 10, "bold"),
    ).pack(anchor="e")

    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="BidKing 物品格局可视化 — 网格地图展示",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python show_grid.py                        # 自动查找日志
  python show_grid.py --log Player.log       # 指定日志文件
  python show_grid.py --record records/814463323313866_20251022_234843_map2101_uid_2101_814463323313866.json
  python show_grid.py --export-records       # 导出回放记录""",
    )
    parser.add_argument("--log", default=None, help="日志文件路径")
    parser.add_argument(
        "--csv", default=CSV_PATH, help=f"物品 CSV 路径（默认: {CSV_PATH}）"
    )
    parser.add_argument("--record", default=None, help="导出的回放记录 JSON 路径")
    parser.add_argument(
        "--tail",
        action="store_true",
        help="实时监听模式：持续监听日志新增内容并自动刷新界面",
    )
    parser.add_argument(
        "--export-records",
        action="store_true",
        help="将日志导出为可回放的 records/*.json 文件后退出",
    )
    parser.add_argument(
        "--records-dir",
        default=os.path.join(_project_root(), "records"),
        help="导出回放记录的目录（默认: 项目根目录/records）",
    )
    parser.add_argument(
        "--include-item-events",
        action="store_true",
        help="导出时包含实时道具事件触发的中间快照",
    )
    parser.add_argument(
        "--last-game-only", action="store_true", help="导出时仅处理最后一局"
    )
    args = parser.parse_args()

    if len(sys.argv) == 1:
        _show_start_page(_default_log_path(), args.csv)
        return

    csv_path = args.csv
    if not os.path.exists(csv_path):
        print(f"错误: 找不到CSV文件: {csv_path}", file=sys.stderr)
        sys.exit(1)

    if args.record:
        if not os.path.exists(args.record):
            print(f"错误: 找不到回放记录文件: {args.record}", file=sys.stderr)
            sys.exit(1)
        print(f"回放记录: {args.record} ...", file=sys.stderr)
        _open_record_grid(args.record, csv_path)
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

    if args.export_records:
        result = _export_records(
            log_path=log_path,
            csv_path=csv_path,
            records_dir=args.records_dir,
            include_item_events=args.include_item_events,
            last_game_only=args.last_game_only,
        )
        print(
            f"新增导出 {result['game_count']} 局记录；"
            f"当前共有 {result.get('total_game_count', result['game_count'])} 局已结束记录。\n"
            f"目录: {result['records_dir']}\n"
            f"清单文件: {result['manifest']}",
            file=sys.stderr,
        )
        return

    print(f"解析日志: {log_path} ...", file=sys.stderr)

    _open_grid(log_path, csv_path, args.tail)


if __name__ == "__main__":
    main()
