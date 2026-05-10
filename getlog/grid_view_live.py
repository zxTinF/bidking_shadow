# -*- coding: utf-8 -*-
"""GridWindow 的实时监听与刷新逻辑。"""

import io
import os
import queue
import threading
import time

from .handlers import handle_s2c33, handle_s2c37, handle_s2c39, handle_s2c45
from .log_parser import extract_event
from .models import GameState
from .runner import parse_last_game_state_from_tail
from .grid_view_shared import (
    CANVAS_MAX_H,
    CANVAS_MAX_W,
    CELL_H,
    CELL_W,
    EMPTY_CELL_VALUE,
    GRID_COLS,
    GRID_ROWS,
)


class GridWindowLiveMixin:
    """处理日志 tail、主线程轮询和重绘刷新。"""

    def _start_live_monitor(self) -> None:
        """启动后台线程，从日志末尾开始监听新增事件。"""
        try:
            self._live_start_pos = os.path.getsize(self._log_path)
        except OSError:
            self._live_start_pos = 0
        t = threading.Thread(target=self._monitor_thread, daemon=True, name="log-tail")
        t.start()

    def _start_live_recovery(self) -> None:
        """后台恢复启动瞬间日志中的最后一局，避免阻塞开窗。"""
        t = threading.Thread(
            target=self._recover_live_state_thread,
            daemon=True,
            name="log-tail-recover",
        )
        t.start()

    def _recover_live_state_thread(self) -> None:
        try:
            state = parse_last_game_state_from_tail(
                self._log_path,
                self.csv_index,
                self.csv_items,
                end_pos=self._live_start_pos,
            )
        except Exception as exc:
            self._queue.put(("recover_error", str(exc)))
            return
        self._queue.put(("recovered", state))

    def _monitor_thread(self) -> None:
        """后台线程：解析增量日志并更新共享状态。"""
        silent = io.StringIO()
        with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(self._live_start_pos, 0)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.3)
                    continue
                result = extract_event(line)
                if not result:
                    continue
                event_type, data = result
                with self._lock:
                    if self._live_recovery_pending:
                        self._live_buffered_events.append((event_type, data))
                        continue
                    msg = self._apply_live_event(event_type, data, silent)
                    if msg:
                        self._queue.put(msg)

    def _apply_live_event(self, event_type: str, data: dict, silent: io.StringIO) -> str:
        """Apply a parsed live event. Caller must hold self._lock."""
        if event_type == "S2C_33_game_start_notify":
            self.state = GameState()
            self._live_game_active = True
            handle_s2c33(data, self.state, self.csv_index, self.csv_items, silent)
            return "new_game"
        if event_type == "S2C_37_game_next_round_notify" and self._live_game_active:
            handle_s2c37(data, self.state, self.csv_index, self.csv_items, silent)
            return "update"
        if event_type == "S2C_39_game_use_item" and self._live_game_active:
            handle_s2c39(data, self.state, self.csv_index, self.csv_items, silent)
            return "update"
        if event_type == "S2C_45_game_over_notify" and self._live_game_active:
            handle_s2c45(data, self.state, self.csv_index, self.csv_items, silent)
            self._live_game_active = False
            return "update"
        return ""

    def _apply_recovered_live_state(self, state) -> bool:
        """Install recovered startup state, then replay live events buffered since open."""
        silent = io.StringIO()
        saw_new_game = False
        with self._lock:
            self.state = state if state is not None else GameState()
            self._live_game_active = bool(self.state.uid)
            buffered = list(self._live_buffered_events)
            self._live_buffered_events.clear()
            self._live_recovery_pending = False
            for event_type, data in buffered:
                msg = self._apply_live_event(event_type, data, silent)
                saw_new_game = saw_new_game or msg == "new_game"
        return saw_new_game

    def _poll_updates(self) -> None:
        """主线程轮询后台信号，并把多次事件合并成一次重绘。"""
        needs_redraw = False
        is_new_game = False
        try:
            while True:
                msg = self._queue.get_nowait()
                if isinstance(msg, tuple):
                    kind, payload = msg
                    if kind == "recovered":
                        is_new_game = self._apply_recovered_live_state(payload) or is_new_game
                        needs_redraw = True
                    elif kind == "recover_error":
                        is_new_game = self._apply_recovered_live_state(None) or is_new_game
                        needs_redraw = True
                    continue
                needs_redraw = True
                if msg == "new_game":
                    is_new_game = True
        except queue.Empty:
            pass
        if needs_redraw:
            with self._lock:
                self._recalc_vis_rows()
                if is_new_game:
                    self._reset_for_new_game()
                else:
                    self.root.title(
                        f"BidKing 鉴影可视化 第 {self.state.current_round} 回合"
                    )
                    self._refresh()
        self.root.after(300, self._poll_updates)

    def _reset_for_new_game(self) -> None:
        """新对局开始时清空手动标注并重置界面。"""
        self._phantom_items.clear()
        self._phantom_draw_state = None
        self._manual_shapes.clear()
        self._autofill_solutions = []
        self._autofill_next_id = 1
        self._secondary_fill_next_id = 1
        for var in self._input_vars.values():
            var.set("")
        self._captured_input_values = {}
        self.root.title(
            f"BidKing 鉴影可视化 第 {self.state.current_round} 回合"
        )
        self._info_text.set(self._info_summary_text())
        cw = GRID_COLS * CELL_W + 1
        ch = GRID_ROWS * CELL_H + 1
        self.canvas.config(
            scrollregion=(0, 0, cw, ch),
            width=min(cw, CANVAS_MAX_W),
            height=min(ch, CANVAS_MAX_H),
        )
        self._refresh()

    def _refresh(self) -> None:
        """常规刷新入口。"""
        confirmed_uids = [u for u, k in self.state.items.items() if k.shape is not None]
        for u in confirmed_uids:
            self._manual_shapes.pop(u, None)
        self._remove_overlapping_phantoms()
        self._apply_scan_history_to_phantoms()
        self._validate_manual_confirmations()
        self._refresh_summary_bars()
        self._draw(update_total=False)

    def _refresh_summary_bars(self) -> None:
        self._info_text.set(self._info_summary_text())
        self._update_total_label()

    def _validate_manual_confirmations(self) -> None:
        item_sources = (self.state.items, self._phantom_items)
        for items in item_sources:
            for uid, k in items.items():
                if k.manual_confirm_item_id is not None:
                    self._valid_manual_confirm_item(uid, k)

    def _update_total_label(self) -> None:
        estimate = self._calc_selected_estimate_price()
        estimate_text = self._estimate_display_text(estimate)
        floor_total = self._calc_grid_floor_price()
        empty_count = self._compute_empty_zone_count()
        if empty_count and empty_count > 0:
            self._total_label.config(
                text=(
                    f"估算总价格: {estimate_text}    "
                    f"保底: ¥{floor_total:,.0f}    "
                    f"空置: {empty_count} 格"
                )
            )
        else:
            self._total_label.config(
                text=(
                    f"估算总价格: {estimate_text}    "
                    f"保底: ¥{floor_total:,.0f}"
                )
            )
