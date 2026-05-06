# -*- coding: utf-8 -*-
"""GridWindow 的实时监听与刷新逻辑。"""

import io
import queue
import threading
import time

from .handlers import handle_s2c33, handle_s2c37, handle_s2c39, handle_s2c45
from .log_parser import extract_event
from .models import GameState
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
        t = threading.Thread(target=self._monitor_thread, daemon=True, name="log-tail")
        t.start()

    def _monitor_thread(self) -> None:
        """后台线程：解析增量日志并更新共享状态。"""
        silent = io.StringIO()
        with open(self._log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
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
                    if event_type == "S2C_33_game_start_notify":
                        self.state = GameState()
                        self._live_game_active = True
                        handle_s2c33(
                            data, self.state, self.csv_index, self.csv_items, silent
                        )
                        self._queue.put("new_game")
                    elif (
                        event_type == "S2C_37_game_next_round_notify"
                        and self._live_game_active
                    ):
                        handle_s2c37(
                            data, self.state, self.csv_index, self.csv_items, silent
                        )
                        self._queue.put("update")
                    elif (
                        event_type == "S2C_39_game_use_item" and self._live_game_active
                    ):
                        handle_s2c39(
                            data, self.state, self.csv_index, self.csv_items, silent
                        )
                        self._queue.put("update")
                    elif (
                        event_type == "S2C_45_game_over_notify"
                        and self._live_game_active
                    ):
                        handle_s2c45(
                            data, self.state, self.csv_index, self.csv_items, silent
                        )
                        self._live_game_active = False
                        self._queue.put("update")

    def _poll_updates(self) -> None:
        """主线程轮询后台信号，并把多次事件合并成一次重绘。"""
        needs_redraw = False
        is_new_game = False
        try:
            while True:
                msg = self._queue.get_nowait()
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
                    self._refresh()
        self.root.after(300, self._poll_updates)

    def _reset_for_new_game(self) -> None:
        """新对局开始时清空手动标注并重置界面。"""
        self._phantom_items.clear()
        self._phantom_draw_state = None
        self._manual_shapes.clear()
        self.root.title(
            f"BidKing 鉴影可视化 第 {self.state.current_round} 回合  ● LIVE"
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
        self._draw()

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
        total = self._calc_grid_total_price()
        floor_total = self._calc_grid_floor_price()
        empty_count = self._compute_empty_zone_count()
        if empty_count and empty_count > 0:
            self._total_label.config(
                text=f"估算总价: ¥{total:,.0f}    保底总价: ¥{floor_total:,.0f}    空置: {empty_count} 格"
            )
        else:
            self._total_label.config(
                text=f"估算总价: ¥{total:,.0f}    保底总价: ¥{floor_total:,.0f}"
            )
