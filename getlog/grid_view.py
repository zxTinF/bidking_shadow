# -*- coding: utf-8 -*-
"""`GridWindow` 的对外入口。

拆分后外部依然只需要 `from getlog.grid_view import GridWindow`。
内部通过多个 mixin 组合具体能力，避免单文件继续膨胀。
"""

import queue
import threading
import tkinter as tk
from typing import Dict, List, Optional, Tuple

from .grid_view_core import GridWindowCoreMixin
from .grid_view_interaction import GridWindowInteractionMixin
from .grid_view_live import GridWindowLiveMixin
from .grid_view_ui import GridWindowUiMixin
from .models import CsvItem, GameState, ItemKnowledge


class GridWindow(
    GridWindowInteractionMixin,
    GridWindowUiMixin,
    GridWindowLiveMixin,
    GridWindowCoreMixin,
):
    """物品网格可视化主窗口。"""

    def __init__(
        self,
        state: GameState,
        csv_index: Dict[int, CsvItem],
        csv_items: List[CsvItem],
        master: Optional[tk.Misc] = None,
        log_path: Optional[str] = None,
        snapshots: Optional[List[Tuple[str, GameState]]] = None,
        map_category_weights: Optional[Dict[int, float]] = None,
        recover_live_state: bool = False,
    ) -> None:
        """初始化窗口状态，并按模式决定是否启动实时监听。"""
        self.state = state
        self.csv_index = csv_index
        self.csv_items = csv_items
        self._master = master
        self._owns_mainloop = master is None
        self._log_path = log_path
        self._map_category_weights = map_category_weights
        self._snapshots: Optional[List[Tuple[str, GameState]]] = snapshots
        self._snap_idx: int = 0
        if snapshots:
            self.state = snapshots[0][1]
        self._manual_shapes: Dict[str, Tuple[int, int, int, int]] = {}
        self._drag_state: Optional[dict] = None
        self._occupied_for_draw: Optional[set] = None
        self._phantom_items: Dict[str, ItemKnowledge] = {}
        self._phantom_counter: int = 0
        self._phantom_draw_state: Optional[dict] = None
        self._autofill_solutions: List[dict] = []
        self._autofill_next_id: int = 1
        self._secondary_fill_next_id: int = 1
        self._autofill_last_count: int = 0
        self._input_vars: Dict[str, tk.StringVar] = {}
        self._input_labels: Dict[str, str] = {}
        self._captured_input_values: Dict[str, object] = {}
        self._hidden_analysis_cache_key = None
        self._hidden_analysis_cache = None
        self._lock: threading.Lock = threading.Lock()
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._live_game_active: bool = bool(state.uid)
        self._live_recovery_pending: bool = bool(log_path and recover_live_state)
        self._live_buffered_events: List[Tuple[str, dict]] = []
        self._live_start_pos: int = 0
        self._recalc_vis_rows()
        self._build_window()
        if log_path and not snapshots:
            self._start_live_monitor()
            if recover_live_state:
                self._start_live_recovery()
        self.root.after(300, self._poll_updates)
