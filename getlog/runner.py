# -*- coding: utf-8 -*-
"""
主运行循环

run() 是整个解析流程的入口：
  1. 加载 CSV 物品数据库
  2. 逐行迭代日志文件
  3. 提取事件并分发到对应的 S2C 处理器
  4. tail 模式下支持"追赶"已有内容后输出当前快照

该模块不持有任何业务状态，所有状态由 GameState 实例承载。
"""

import copy
import io
import os
import sys
from typing import Dict, List, Optional, Tuple

from .handlers import handle_s2c33, handle_s2c37, handle_s2c39, handle_s2c45
from .item_db import load_csv
from .log_parser import extract_event, iter_log_lines
from .models import CsvItem, GameState
from .renderer import print_catchup_summary


_GAME_START_MARKER = b"S2C_33_game_start_notify"


def _read_last_game_text(log_path: str, end_pos: Optional[int] = None) -> str:
    """
    Read only the tail segment containing the last game start event.

    This keeps GUI startup responsive for large Player.log files. If the last
    game is unusually large, the window grows until it either finds the last
    start marker or reaches the beginning of the file.
    """
    file_size = os.path.getsize(log_path)
    limit = file_size if end_pos is None else max(0, min(int(end_pos), file_size))
    if limit <= 0:
        return ""

    chunk_size = 1024 * 1024
    with open(log_path, "rb") as f:
        while True:
            start = max(0, limit - chunk_size)
            f.seek(start)
            data = f.read(limit - start)
            marker_at = data.rfind(_GAME_START_MARKER)
            if marker_at >= 0:
                line_start = data.rfind(b"\n", 0, marker_at)
                if line_start >= 0:
                    marker_at = line_start + 1
                return data[marker_at:].decode("utf-8", errors="replace")
            if start == 0:
                return data.decode("utf-8", errors="replace")
            chunk_size = min(chunk_size * 2, limit)


def parse_last_game_state_from_tail(
    log_path: str,
    csv_index: Dict[int, CsvItem],
    csv_items: List[CsvItem],
    *,
    end_pos: Optional[int] = None,
) -> Optional[GameState]:
    """Parse the last game by first seeking backward to the last start event."""
    text = _read_last_game_text(log_path, end_pos=end_pos)
    if not text:
        return None

    silent = io.StringIO()
    state: Optional[GameState] = None
    cur_state = GameState()
    game_active = False

    for line in text.splitlines():
        result = extract_event(line)
        if not result:
            continue
        event_type, data = result

        if event_type == 'S2C_33_game_start_notify':
            cur_state = GameState()
            game_active = True
            handle_s2c33(data, cur_state, csv_index, csv_items, silent)

        elif event_type == 'S2C_37_game_next_round_notify' and game_active:
            handle_s2c37(data, cur_state, csv_index, csv_items, silent)

        elif event_type == 'S2C_39_game_use_item' and game_active:
            handle_s2c39(data, cur_state, csv_index, csv_items, silent)

        elif event_type == 'S2C_45_game_over_notify' and game_active:
            handle_s2c45(data, cur_state, csv_index, csv_items, silent)
            state = cur_state
            game_active = False

    if game_active:
        state = cur_state

    return state


def run(
    log_path: str,
    csv_path: str,
    tail: bool = False,
    out=sys.stdout,
) -> None:
    """
    解析日志文件并将结果写入 out。

    Args:
        log_path : 日志文件路径（Player.log）
        csv_path : 物品价格 CSV 路径（item_prices.csv）
        tail     : True=实时监听模式，False=批量处理模式
        out      : 输出流，默认 sys.stdout，可重定向到文件

    tail 模式行为：
        - 启动时"静默追赶"已有日志（更新状态但不输出）
        - 读到 EOF 后判断：
            * 对局进行中 → 调用 print_catchup_summary 输出当前快照
            * 无进行中对局 → 提示等待新对局
        - 此后持续监听新增行，实时输出
    """
    print(f"加载物品数据库: {csv_path}", file=sys.stderr)
    csv_index, csv_items = load_csv(csv_path)
    print(f"  已加载 {len(csv_items)} 件物品", file=sys.stderr)

    print(f"读取日志: {log_path}", file=sys.stderr)
    if tail:
        print("  (实时监听模式，Ctrl+C 退出)", file=sys.stderr)

    state = GameState()
    game_active = False
    catching_up = tail              # tail 模式：先静默追赶
    silent_out = io.StringIO()      # 追赶期间丢弃输出

    for line in iter_log_lines(log_path, tail=tail):
        if line is None:            # EOF 信号
            if catching_up:
                catching_up = False
                if game_active:
                    print_catchup_summary(state, csv_index, csv_items, out)
                else:
                    print("\n  等待新对局开始... (Ctrl+C 退出)", file=out)
                out.flush()
            continue

        result = extract_event(line)
        if not result:
            continue
        event_type, data = result

        cur_out = silent_out if catching_up else out

        if event_type == 'S2C_33_game_start_notify':
            state = GameState()
            game_active = True
            handle_s2c33(data, state, csv_index, csv_items, cur_out)

        elif event_type == 'S2C_37_game_next_round_notify' and game_active:
            handle_s2c37(data, state, csv_index, csv_items, cur_out)

        elif event_type == 'S2C_39_game_use_item' and game_active:
            handle_s2c39(data, state, csv_index, csv_items, cur_out)

        elif event_type == 'S2C_45_game_over_notify' and game_active:
            handle_s2c45(data, state, csv_index, csv_items, cur_out)
            game_active = False


def parse_last_game(
    log_path: str,
    csv_path: str,
) -> Tuple[Optional[GameState], Dict[int, CsvItem], List[CsvItem]]:
    """
    静默解析日志文件，返回最后一局的游戏状态及物品数据库。

    不产生任何控制台输出，适合配合 GUI 可视化使用。

    Returns:
        (state, csv_index, csv_items)
        state     : 最后一局的 GameState（无对局时为 None）
        csv_index : item_id → CsvItem
        csv_items : 全量 CsvItem 列表
    """
    csv_index, csv_items = load_csv(csv_path)
    state = parse_last_game_state_from_tail(log_path, csv_index, csv_items)
    return state, csv_index, csv_items


def parse_last_game_rounds(
    log_path: str,
    csv_path: str,
) -> Tuple[List[Tuple[str, GameState]], Dict[int, CsvItem], List[CsvItem]]:
    """
    解析日志文件中最后一局，在每个回合边界截取状态快照。

    快照时机：
      - S2C_33 处理完毕  → 快照"第 1 回合"（游戏刚开始）
      - S2C_37 处理完毕  → 快照"第 N 回合"（含上一回合的道具/技能结果）
      - S2C_39 处理完毕  → 快照"第 N 回合（道具）"（实时道具揭示后）
      - S2C_45 处理完毕  → 快照"游戏结束"（保留结束前最后一轮推断）

    Returns:
        snapshots : [(label, GameState_deepcopy), ...]
                    按时间顺序排列，每项是该时间点的状态深拷贝
        csv_index : item_id → CsvItem
        csv_items : 全量 CsvItem 列表
    """
    csv_index, csv_items = load_csv(csv_path)
    silent = io.StringIO()

    # 记录所有局的快照列表，最终取最后一局
    all_games: List[List[Tuple[str, GameState]]] = []
    cur_snapshots: List[Tuple[str, GameState]] = []
    cur_state = GameState()
    game_active = False

    for line in iter_log_lines(log_path, tail=False):
        if line is None:
            break
        result = extract_event(line)
        if not result:
            continue
        event_type, data = result

        if event_type == 'S2C_33_game_start_notify':
            cur_state = GameState()
            cur_snapshots = []
            game_active = True
            handle_s2c33(data, cur_state, csv_index, csv_items, silent)
            cur_snapshots.append(('第 1 回合', copy.deepcopy(cur_state)))

        elif event_type == 'S2C_37_game_next_round_notify' and game_active:
            handle_s2c37(data, cur_state, csv_index, csv_items, silent)
            cur_snapshots.append(
                (f'第 {cur_state.current_round} 回合', copy.deepcopy(cur_state))
            )

        elif event_type == 'S2C_39_game_use_item' and game_active:
            handle_s2c39(data, cur_state, csv_index, csv_items, silent)
            # 道具使用后追加快照，注明"（道具）"以便区分同回合普通快照
            cur_snapshots.append(
                (f'第 {cur_state.current_round} 回合（道具）', copy.deepcopy(cur_state))
            )

        elif event_type == 'S2C_45_game_over_notify' and game_active:
            handle_s2c45(data, cur_state, csv_index, csv_items, silent)
            cur_snapshots.append(('游戏结束', copy.deepcopy(cur_state)))
            game_active = False
            all_games.append(cur_snapshots)

    # 若最后一局未结束，也保留当前快照序列
    if game_active and cur_snapshots:
        all_games.append(cur_snapshots)

    if not all_games:
        return [], csv_index, csv_items

    return all_games[-1], csv_index, csv_items
