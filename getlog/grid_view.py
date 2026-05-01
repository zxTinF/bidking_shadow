# -*- coding: utf-8 -*-
"""
网格可视化窗口

用 tkinter 将当前对局物品按 BoxId / 形状渲染为 10×30 格子地图，
支持鼠标点击弹窗查看该物品的所有候选列表。

布局规则（来自游戏协议）：
  - 网格：10 列 × 最多 30 行
  - BoxId = 行 × 10 + 列（行列均从 0 开始）
  - ItemSlotType XY → X 列宽 × Y 行高
  - 只有品质无大小时默认按 1×1 显示

实时监听模式（log_path 非 None 时激活）：
  - 后台线程从文件当前 EOF 开始 tail，解析新增事件并更新 GameState
  - 通过 queue.SimpleQueue 传信号给 UI 主线程
  - UI 主线程每 300ms 通过 root.after() 轮询队列，按需重绘
  - 新对局开始（S2C_33）时自动清空并重置整个界面
  - 所有状态写入均在 threading.Lock 保护下进行，防止迭代中途修改
"""

import io
import queue
import statistics
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from .constants import CATEGORY_NAMES, fmt_shape
from .handlers import handle_s2c33, handle_s2c37, handle_s2c39, handle_s2c45
from .item_db import (
    candidate_probabilities,
    map_category_ratios,
    probability_source_label,
    query_item,
)
from .log_parser import extract_event
from .models import CsvItem, GameState, ItemKnowledge

# ─── 布局常量 ──────────────────────────────────────────────────────────────

GRID_COLS  = 10       # 游戏地图固定宽度
GRID_ROWS  = 30       # 游戏地图最大高度
VISIBLE_ROWS = 10     # 默认视口显示行数
CELL_SIZE  = 56       # 每格像素边长
CELL_W     = CELL_SIZE
CELL_H     = CELL_SIZE
CANVAS_MAX_W = GRID_COLS * CELL_W + 1
CANVAS_MAX_H = VISIBLE_ROWS * CELL_H + 1

# ─── 品质颜色方案 ──────────────────────────────────────────────────────────

# 背景色（按品质 1-6）
QUALITY_BG: Dict[int, str] = {
    1: '#7a7a8a',   # 灰
    2: '#3a8a4a',   # 绿
    3: '#2060c0',   # 蓝
    4: '#8030b0',   # 紫
    5: '#c07010',   # 橙
    6: '#c02020',   # 红
}
# 文字色（所有品质都用白）
QUALITY_FG: Dict[int, str] = {k: '#ffffff' for k in range(1, 7)}
UNKNOWN_BG = '#7a5c3a'   # 棕黄色：未知品质，与空格子和已知品质都有明显区分
UNKNOWN_FG = '#ffffff'
EMPTY_BG   = '#2a2a3a'
GRID_LINE  = '#505060'

# 第4回合起的空缺区域覆盖层
EMPTY_ZONE_COLOR    = '#cc4400'   # 橘红
EMPTY_ZONE_STIPPLE  = 'gray25'    # 约 25% 覆盖度，模拟半透明
MIN_ROUND_SHOW_EMPTY = 4

# 未知品质物品的手动缩放把手
RESIZE_HANDLE_W     = 8           # 把手条宽度（像素）
RESIZE_HANDLE_COLOR = '#ffffff'

# 手动画框的"幽灵"物品
PHANTOM_BG     = '#0d3a4a'   # 深青蓝，区别于 UNKNOWN_BG(棕黄)
PHANTOM_BORDER = '#00cccc'   # 青色边框
EMPTY_CELL_VALUE = 15_000    # 空格估算单价（¥/格）
HIGH_VALUE_THRESHOLD = 100_000

# ─── 类别缩写（两字显示） ─────────────────────────────────────────────────

_CAT_SHORT: Dict[int, str] = {
    101: '家具', 102: '医药', 103: '时尚', 104: '兵装',
    105: '珠宝', 106: '文物', 107: '数码', 108: '能源',
    109: '食饮', 110: '书画',
}


# ─── 主窗口 ────────────────────────────────────────────────────────────────

class GridWindow:
    """
    BidKing 物品格局可视化窗口。

    Args:
        state      : 解析后的 GameState（含物品知识）
        csv_index  : item_id → CsvItem
        csv_items  : 全量 CsvItem 列表
    """

    def __init__(
        self,
        state: GameState,
        csv_index: Dict[int, CsvItem],
        csv_items: List[CsvItem],
        log_path: Optional[str] = None,
        snapshots: Optional[List[Tuple[str, GameState]]] = None,
        map_category_weights: Optional[Dict[int, float]] = None,
    ) -> None:
        self.state     = state
        self.csv_index = csv_index
        self.csv_items = csv_items
        self._log_path = log_path
        # 地图类别权重入口：category tag -> multiplier，默认由 item_db 使用 1.0。
        self._map_category_weights = map_category_weights

        # 快照回放模式（静态逐回合浏览）
        self._snapshots: Optional[List[Tuple[str, GameState]]] = snapshots
        self._snap_idx: int = 0
        if snapshots:
            self.state = snapshots[0][1]

        # 手动尺寸覆盖：uid → (w, h, display_col, display_row)
        # display_col/row 是用户设定的显示左上角；BoxId 必须在矩形内
        # log 确认形状后自动清除；phantom 项也放在这里
        self._manual_shapes: Dict[str, Tuple[int, int, int, int]] = {}
        # 缩放把手拖动状态
        self._drag_state: Optional[dict] = None
        # _draw() 期间的占位格缓存（单次绘制内复用，避免重复构建）
        self._occupied_for_draw: Optional[set] = None

        # 手动画框的幽灵物品：uid(phantom_N) → ItemKnowledge
        self._phantom_items: Dict[str, ItemKnowledge] = {}
        self._phantom_counter: int = 0
        # 当前正在拖拽画框的状态 {'start_row','start_col','cur_row','cur_col'}
        self._phantom_draw_state: Optional[dict] = None

        # 线程安全：后台线程写 state，主线程读 state
        self._lock:  threading.Lock     = threading.Lock()
        self._queue: queue.SimpleQueue  = queue.SimpleQueue()
        # 'update' = 普通刷新, 'new_game' = 新对局（需重置整个界面）
        self._live_game_active: bool    = bool(state.uid)

        self._recalc_vis_rows()
        self._build_window()

        if log_path and not snapshots:
            self._start_live_monitor()
            self.root.after(300, self._poll_updates)

    # ── 行数计算 ──────────────────────────────────────────────────────────

    def _recalc_vis_rows(self) -> None:
        """网格固定为 10x30；Canvas 视口只显示前 10 行，通过滚动查看其余行。"""
        self.vis_rows = GRID_ROWS

    # ── 形状解析 ──────────────────────────────────────────────────────────

    @staticmethod
    def _shape_wh(shape: Optional[int]) -> Tuple[int, int]:
        """ItemSlotType → (列宽 w, 行高 h)，无形状信息默认 1×1。"""
        if shape is None:
            return 1, 1
        s = str(shape)
        if len(s) == 2:
            return int(s[0]), int(s[1])
        return 1, 1

    # ── 尺寸推断辅助 ──────────────────────────────────────────────────────

    def _effective_shape_wh(self, uid: str, k: ItemKnowledge) -> Tuple[int, int]:
        """返回物品的有效 (w, h)：优先 log 确认形状，其次用户手动，最后默认 1×1。"""
        if k.shape is not None:
            return self._shape_wh(k.shape)
        if uid in self._manual_shapes:
            w, h, _, _ = self._manual_shapes[uid]
            return w, h
        return (1, 1)

    def _effective_display_origin(self, uid: str, k: ItemKnowledge) -> Tuple[int, int]:
        """
        返回物品在网格上显示的左上角 (col, row)。
        有手动覆盖时使用手动值；否则以 BoxId 所在格为左上角（保守默认）。
        """
        if uid in self._manual_shapes:
            _, _, dc, dr = self._manual_shapes[uid]
            return dc, dr
        if k.box_id is None:
            return 0, 0
        return k.box_id % GRID_COLS, k.box_id // GRID_COLS

    def _build_occupied(self, exclude_uid: str = '') -> set:
        """
        返回所有已确认/手动定位/幽灵物品所占据的格子 (row, col) 集合。
        exclude_uid 指定的物品会被排除在外（用于推断该物品自身的可用空间）。
        只包含 box_id_confirmed=True 或已手动设置尺寸/画框的物品，以保证可靠性。
        """
        occupied: set = set()
        for uid, k in self.state.items.items():
            if uid == exclude_uid or k.box_id is None:
                continue
            if not k.box_id_confirmed and uid not in self._manual_shapes:
                continue
            dc, dr = self._effective_display_origin(uid, k)
            w, h = self._effective_shape_wh(uid, k)
            for ddr in range(h):
                for ddc in range(w):
                    occupied.add((dr + ddr, dc + ddc))
        # 幽灵物品（手动画框）
        for phid in self._phantom_items:
            if phid == exclude_uid or phid not in self._manual_shapes:
                continue
            w, h, dc, dr = self._manual_shapes[phid]
            for ddr in range(h):
                for ddc in range(w):
                    occupied.add((dr + ddr, dc + ddc))
        return occupied

    @staticmethod
    def _rect_cells(row: int, col: int, w: int, h: int) -> set:
        """返回矩形覆盖的所有格子坐标 (row, col)。"""
        return {
            (row + ddr, col + ddc)
            for ddr in range(h)
            for ddc in range(w)
        }

    def _rect_overlaps_occupied(
        self,
        row: int,
        col: int,
        w: int,
        h: int,
        exclude_uid: str = '',
    ) -> bool:
        """检查指定矩形是否覆盖已有可靠物品或幽灵物品。"""
        if row < 0 or col < 0 or w <= 0 or h <= 0:
            return True
        if col + w > GRID_COLS or row + h > GRID_ROWS:
            return True
        occupied = self._build_occupied(exclude_uid=exclude_uid)
        return any(cell in occupied for cell in self._rect_cells(row, col, w, h))

    def _query_item_for_grid(
        self,
        uid: str,
        k: ItemKnowledge,
    ) -> Tuple[Optional[CsvItem], int, bool, Optional[float], str]:
        """按当前网格显示约束查询候选，包含手动尺寸、幽灵框和最大尺寸推断。"""
        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return manual_item, 1, True, float(manual_item.base_value), "手动确认"

        effective_shape = k.shape
        max_shape: Optional[Tuple[int, int]] = None

        if k.shape is None:
            if uid in self._manual_shapes:
                mw, mh, _, _ = self._manual_shapes[uid]
                effective_shape = mw * 10 + mh
            elif k.box_id is not None:
                max_w, max_h = self._compute_max_size(uid, k)
                if max_w < GRID_COLS or max_h < GRID_ROWS:
                    max_shape = (max_w, max_h)

        return query_item(
            effective_shape, k.quality, k.categories, k.item_cid,
            self.csv_index, self.csv_items,
            k.excluded_categories, k.excluded_qualities,
            max_shape_wh=max_shape,
            map_category_weights=self._map_category_weights,
            map_id=self.state.map_id,
        )

    def _valid_manual_confirm_item(self, uid: str, k: ItemKnowledge) -> Optional[CsvItem]:
        """
        返回当前仍然有效的手动确认候选；若已与新约束冲突会自动撤销。
        冲突判定基于当前网格约束筛出的候选集合。
        """
        cid = k.manual_confirm_item_id
        if not cid:
            return None
        item = self.csv_index.get(cid)
        if item is None:
            k.manual_confirm_item_id = None
            return None
        candidates = self._candidate_items_for_grid(uid, k)
        if any(c.item_id == cid for c in candidates):
            return item
        k.manual_confirm_item_id = None
        return None

    def _candidate_items_for_grid(self, uid: str, k: ItemKnowledge) -> List[CsvItem]:
        """返回与当前网格约束一致的候选物品列表。"""
        if k.item_cid and k.item_cid in self.csv_index:
            return [self.csv_index[k.item_cid]]

        candidates = list(self.csv_items)
        if k.shape is not None:
            candidates = [i for i in candidates if i.shape == k.shape]
        elif uid in self._manual_shapes:
            mw, mh, _, _ = self._manual_shapes[uid]
            virtual_shape = mw * 10 + mh
            candidates = [i for i in candidates if i.shape == virtual_shape]
        elif k.box_id is not None:
            max_w, max_h = self._compute_max_size(uid, k)
            if max_w < GRID_COLS or max_h < GRID_ROWS:
                def _shape_fits(shape: int) -> bool:
                    ss = str(shape)
                    if len(ss) == 2:
                        return int(ss[0]) <= max_w and int(ss[1]) <= max_h
                    return False
                candidates = [i for i in candidates if _shape_fits(i.shape)]

        if k.quality is not None:
            candidates = [i for i in candidates if i.quality == k.quality]
        if k.excluded_qualities:
            candidates = [i for i in candidates if i.quality not in k.excluded_qualities]
        if k.categories:
            with_cat = [
                i for i in candidates
                if all(c in i.category_tags for c in k.categories)
            ]
            if with_cat:
                candidates = with_cat
        if k.excluded_categories:
            candidates = [
                i for i in candidates
                if not any(c in k.excluded_categories for c in i.category_tags)
            ]
        return candidates

    def _display_quality(self, uid: str, k: ItemKnowledge) -> Optional[int]:
        """返回用于显示的品质；候选品质唯一时也补齐显示颜色。"""
        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return manual_item.quality
        if k.quality is not None:
            return k.quality
        candidates = self._candidate_items_for_grid(uid, k)
        qualities = {item.quality for item in candidates}
        if len(qualities) == 1:
            return next(iter(qualities))
        best, _count, unique, _est, _label = self._query_item_for_grid(uid, k)
        if unique and best is not None:
            return best.quality
        return None

    def _display_price_value(self, uid: str, k: ItemKnowledge) -> Optional[float]:
        """返回当前格子的精确价或期望价，用于高价值标识。"""
        if k.price is not None and k.item_cid:
            return float(k.price)
        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return float(manual_item.base_value)
        best, _count, unique, est, _label = self._query_item_for_grid(uid, k)
        if best is None:
            return None
        if unique:
            return float(best.base_value)
        return est

    def _calc_grid_total_price(self) -> float:
        """计算网格总价，纳入手动尺寸和手动画框物品。"""
        total = 0.0
        item_sources = (self.state.items, self._phantom_items)
        for items in item_sources:
            for uid, k in items.items():
                if k.price is not None and k.item_cid:
                    total += k.price
                    continue
                best, _count, unique, est, _label = self._query_item_for_grid(uid, k)
                if best is None:
                    continue
                if unique:
                    total += best.base_value
                elif est is not None:
                    total += est
        return total

    def _info_summary_text(self) -> str:
        """顶部状态栏：显示当前局面物品格数、均格和已知橙/红数量。"""
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())

        total_cells = 0
        item_count = 0
        q5_count = 0
        q6_count = 0
        q5_cells = 0
        q6_cells = 0
        unknown_cells = 0
        for uid, k in item_rows:
            if k.box_id is None:
                continue
            w, h = self._effective_shape_wh(uid, k)
            cells = w * h
            total_cells += cells
            item_count += 1
            q = self._display_quality(uid, k)
            if q == 5:
                q5_count += 1
                q5_cells += cells
            elif q == 6:
                q6_count += 1
                q6_cells += cells
            elif q is None:
                unknown_cells += cells

        avg_cells = total_cells / item_count if item_count else 0.0
        top_cats = ""
        category_ratios = map_category_ratios(self.state.map_id)
        if not category_ratios and self._map_category_weights:
            # 回退：若没有地图根图数据，则使用传入的类别倍率入口。
            total_weight = sum(
                w for w in self._map_category_weights.values() if w > 0
            )
            if total_weight > 0:
                category_ratios = {
                    cid: w / total_weight
                    for cid, w in self._map_category_weights.items()
                    if w > 0
                }
        if category_ratios:
            ranked = sorted(
                category_ratios.items(),
                key=lambda kv: kv[1],
                reverse=True,
            )
            top_parts: List[str] = []
            for cid, ratio in ranked[:3]:
                pct = ratio * 100.0
                cat_short = _CAT_SHORT.get(cid, CATEGORY_NAMES.get(cid, str(cid))[:2])
                top_parts.append(f"{cat_short}{pct:.0f}%")
            top_cats = "   类别TOP3: " + " / ".join(top_parts)
        return (
            f"地图: {self.state.map_id}   第 {self.state.current_round} 回合   "
            f"已知物品: {len(self.state.items)} 件   "
            f"当前物品总格数: {total_cells}   平均格数: {avg_cells:.2f}   "
            f"已知橙: {q5_count} 件 {q5_cells}格   "
            f"已知红: {q6_count} 件 {q6_cells}格   "
            f"未知: {unknown_cells}格"
            f"{top_cats}"
        )

    def _compute_empty_zone_count(self) -> Optional[int]:
        """
        计算第4回合后已知区域内的空格数（不被任何物品/幽灵占据）。
        优先复用 _draw() 期间的共享占位图缓存，避免重复构建。
        """
        if self.state.current_round < MIN_ROUND_SHOW_EMPTY:
            return None
        max_box_id = self._empty_zone_max_box_id()
        if max_box_id < 0:
            return None
        occupied = self._occupied_for_draw if self._occupied_for_draw is not None \
            else self._build_occupied()
        count = 0
        for bid in range(min(max_box_id, GRID_COLS * GRID_ROWS - 1) + 1):
            row = bid // GRID_COLS
            col = bid % GRID_COLS
            if (row, col) not in occupied:
                count += 1
        return count

    def _remove_overlapping_phantoms(self) -> None:
        """删除与 log 已确认物品重叠的幽灵物品。"""
        confirmed_occ: set = set()
        for uid, k in self.state.items.items():
            if k.box_id is None or not k.box_id_confirmed:
                continue
            dc, dr = self._effective_display_origin(uid, k)
            w, h = self._effective_shape_wh(uid, k)
            for ddr in range(h):
                for ddc in range(w):
                    confirmed_occ.add((dr + ddr, dc + ddc))
        to_del = []
        for phid in self._phantom_items:
            if phid not in self._manual_shapes:
                continue
            w, h, dc, dr = self._manual_shapes[phid]
            if any((dr + ddr, dc + ddc) in confirmed_occ
                   for ddr in range(h) for ddc in range(w)):
                to_del.append(phid)
        for phid in to_del:
            self._phantom_items.pop(phid, None)
            self._manual_shapes.pop(phid, None)

    def _apply_scan_history_to_phantoms(self) -> None:
        """将全量扫描产生的负向约束同步到手动画框物品。"""
        for phid, pk in self._phantom_items.items():
            for scan_type, value, hit_uids in self.state._scan_history:
                if phid in hit_uids:
                    continue
                if scan_type == 'category':
                    pk.excluded_categories.add(value)
                else:
                    pk.excluded_qualities.add(value)

    def _create_phantom(self, row: int, col: int, w: int, h: int) -> bool:
        """在 (row, col) 以 (w, h) 大小创建一个幽灵物品，并应用当前扫描历史约束。"""
        if self._rect_overlaps_occupied(row, col, w, h):
            return False
        phid = f'phantom_{self._phantom_counter}'
        self._phantom_counter += 1
        pk = ItemKnowledge(uid=phid)
        pk.box_id = row * GRID_COLS + col
        pk.box_id_confirmed = True   # 用户明确指定了位置
        self._phantom_items[phid] = pk
        self._manual_shapes[phid] = (w, h, col, row)
        self._apply_scan_history_to_phantoms()
        return True

    def _compute_max_size(self, uid: str, k: ItemKnowledge) -> Tuple[int, int]:
        """
        推断物品最大可能尺寸 (w, h)，以 BoxId 所在格为锚点，
        向四个方向扫描空闲格子后取上界（不考虑矩形性约束，为保守上界）。
        """
        if k.box_id is None:
            return GRID_COLS, GRID_ROWS
        brow = k.box_id // GRID_COLS
        bcol = k.box_id % GRID_COLS

        # 复用或重建占位图（排除自身）
        if self._occupied_for_draw is not None:
            dc0, dr0 = self._effective_display_origin(uid, k)
            w0, h0 = self._effective_shape_wh(uid, k)
            own = frozenset(
                (dr0 + ddr, dc0 + ddc) for ddr in range(h0) for ddc in range(w0)
            )
            occupied = self._occupied_for_draw - own
        else:
            occupied = self._build_occupied(exclude_uid=uid)

        # 四方向独立扫描（以 BoxId 为锚点）
        def _scan_right() -> int:
            n = 0
            for c in range(bcol, GRID_COLS):
                if (brow, c) in occupied:
                    break
                n += 1
            return n - 1  # 不含 bcol 自身

        def _scan_left() -> int:
            n = 0
            for c in range(bcol, -1, -1):
                if (brow, c) in occupied:
                    break
                n += 1
            return n - 1

        def _scan_down() -> int:
            n = 0
            for r in range(brow, GRID_ROWS):
                if (r, bcol) in occupied:
                    break
                n += 1
            return n - 1

        def _scan_up() -> int:
            n = 0
            for r in range(brow, -1, -1):
                if (r, bcol) in occupied:
                    break
                n += 1
            return n - 1

        right_ext = _scan_right()
        left_ext  = _scan_left()
        down_ext  = _scan_down()
        up_ext    = _scan_up()

        max_w = max(1, left_ext + 1 + right_ext)
        max_h = max(1, up_ext  + 1 + down_ext)
        return max_w, max_h

    def _empty_zone_max_box_id(self) -> int:
        """第4回合后橘红覆盖层的最大 BoxId（含），无确认物品时返回 -1。"""
        max_box_id = -1
        for k in self.state.items.values():
            if k.box_id is None or not k.box_id_confirmed:
                continue
            max_box_id = max(max_box_id, k.box_id)
        return max_box_id

    # ── 实时监听 ──────────────────────────────────────────────────────────

    def _start_live_monitor(self) -> None:
        """启动后台 daemon 线程，从日志文件当前末尾开始 tail。"""
        t = threading.Thread(target=self._monitor_thread, daemon=True, name='log-tail')
        t.start()

    def _monitor_thread(self) -> None:
        """
        后台线程：从文件 EOF 开始监听新增行，解析事件并更新 self.state。
        状态修改均在 self._lock 保护下进行；事件信号写入 self._queue。
        """
        silent = io.StringIO()
        with open(self._log_path, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)   # 直接跳到文件末尾，只处理新增内容
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
                    if event_type == 'S2C_33_game_start_notify':
                        self.state = GameState()
                        self._live_game_active = True
                        handle_s2c33(data, self.state, self.csv_index, self.csv_items, silent)
                        self._queue.put('new_game')

                    elif event_type == 'S2C_37_game_next_round_notify' and self._live_game_active:
                        handle_s2c37(data, self.state, self.csv_index, self.csv_items, silent)
                        self._queue.put('update')

                    elif event_type == 'S2C_39_game_use_item' and self._live_game_active:
                        handle_s2c39(data, self.state, self.csv_index, self.csv_items, silent)
                        self._queue.put('update')

                    elif event_type == 'S2C_45_game_over_notify' and self._live_game_active:
                        handle_s2c45(data, self.state, self.csv_index, self.csv_items, silent)
                        self._live_game_active = False
                        self._queue.put('update')

    def _poll_updates(self) -> None:
        """
        主线程定时任务（每 300ms）：消费队列中的信号并按需刷新 UI。
        多个信号合并为一次绘制，避免短时间内多次重绘。
        """
        needs_redraw = False
        is_new_game  = False
        try:
            while True:
                msg = self._queue.get_nowait()
                needs_redraw = True
                if msg == 'new_game':
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

        # 继续调度下一次轮询
        self.root.after(300, self._poll_updates)

    def _reset_for_new_game(self) -> None:
        """新对局开始：清空幽灵、更新标题、重建 Canvas。"""
        # 新对局：清空所有手动注释
        self._phantom_items.clear()
        self._phantom_draw_state = None
        self._manual_shapes.clear()

        self.root.title(
            f"BidKing 可视化鉴影 "
            f"第 {self.state.current_round} 回合  ● LIVE"
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
        """普通刷新：更新信息栏、总价标签、重绘 Canvas。"""
        # log 已确认形状的物品，手动尺寸覆盖自动失效
        confirmed_uids = [u for u, k in self.state.items.items() if k.shape is not None]
        for u in confirmed_uids:
            self._manual_shapes.pop(u, None)
        # 删除与已确认 log 物品重叠的幽灵
        self._remove_overlapping_phantoms()
        # 道具/英雄全量扫描更新后，同步手动画框物品的排除约束
        self._apply_scan_history_to_phantoms()
        # 新约束可能与手动确认冲突，冲突时自动撤销确认
        self._validate_manual_confirmations()

        self._info_text.set(self._info_summary_text())
        self._update_total_label()
        self._draw()

    def _validate_manual_confirmations(self) -> None:
        """校验所有物品的手动候选确认，冲突时自动清除。"""
        item_sources = (self.state.items, self._phantom_items)
        for items in item_sources:
            for uid, k in items.items():
                if k.manual_confirm_item_id is not None:
                    self._valid_manual_confirm_item(uid, k)

    def _update_total_label(self) -> None:
        """更新估算总价标签（含空置格价值）。"""
        total = self._calc_grid_total_price()
        empty_count = self._compute_empty_zone_count()
        if empty_count and empty_count > 0:
            self._total_label.config(
                text=(
                    f"估算总价  ¥{total:,.0f}"
                    f"    空置 {empty_count} 格"
                )
            )
        else:
            self._total_label.config(text=f"估算总价  ¥{total:,.0f}")

    # ── 界面构建 ──────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        live_tag = "  ● LIVE" if self._log_path else ""
        self.root = tk.Tk()
        self.root.title(
            f"BidKing 鉴影可视化"
            f"第 {self.state.current_round} 回合{live_tag}"
        )
        self.root.configure(bg='#1a1a2e')

        self._build_info_bar()
        self._build_legend()
        self._build_canvas()
        if self._snapshots:
            self._build_nav_bar()
        self._draw()

    def _build_info_bar(self) -> None:
        bar = tk.Frame(self.root, bg='#1a1a2e', pady=4)
        bar.pack(fill='x', padx=8)

        # StringVar 方便后续 _refresh() 直接更新，无需重建 Label
        self._info_text = tk.StringVar(value=self._info_summary_text())
        tk.Label(
            bar,
            textvariable=self._info_text,
            bg='#1a1a2e', fg='#ccccdd',
            font=('微软雅黑', 10),
            wraplength=CANVAS_MAX_W - 20,
            justify='left',
        ).pack(side='left')

        if self._log_path:
            tk.Label(
                bar, text=" ● LIVE ",
                bg='#c03030', fg='#ffffff',
                font=('微软雅黑', 9, 'bold'),
                relief='flat', padx=4,
            ).pack(side='right', padx=8)

    def _build_legend(self) -> None:
        bar = tk.Frame(self.root, bg='#222233', pady=5)
        bar.pack(fill='x', padx=8)

        # 只保留未知品质色块，已知品质直接通过格子颜色区分。
        tk.Label(bar, text=" 未知 ", bg=UNKNOWN_BG, fg='#ffffff',
                 font=('微软雅黑', 8), relief='flat', padx=2).pack(side='left', padx=(6, 2))

        # 右侧：估算总价
        total = self._calc_grid_total_price()
        self._total_label = tk.Label(
            bar,
            text=f"估算总价  ¥{total:,.0f}",
            bg='#222233', fg='#e8d080',
            font=('微软雅黑', 10, 'bold'),
        )
        self._total_label.pack(side='right', padx=12)

        tk.Label(
            bar,
            text="点击格子查看候选；弹窗内双击行可确认",
            bg='#222233', fg='#555577',
            font=('微软雅黑', 8),
        ).pack(side='right', padx=8)

    def _build_nav_bar(self) -> None:
        """快照导航栏：上一步 / 当前位置 / 下一步（仅快照模式显示）。"""
        bar = tk.Frame(self.root, bg='#161625', pady=6)
        bar.pack(fill='x', padx=8)

        btn_cfg = dict(
            font=('微软雅黑', 9, 'bold'), relief='flat',
            padx=14, pady=4, cursor='hand2',
        )
        self._btn_prev = tk.Button(
            bar, text="◀  上一步",
            bg='#334466', fg='#aabbdd',
            command=self._snap_prev, **btn_cfg,
        )
        self._btn_prev.pack(side='left', padx=8)

        self._nav_label = tk.StringVar()
        tk.Label(
            bar, textvariable=self._nav_label,
            bg='#161625', fg='#ddddee',
            font=('微软雅黑', 10, 'bold'),
        ).pack(side='left', expand=True)

        self._btn_next = tk.Button(
            bar, text="下一步  ▶",
            bg='#334466', fg='#aabbdd',
            command=self._snap_next, **btn_cfg,
        )
        self._btn_next.pack(side='right', padx=8)

        self._update_nav_label()

        # 键盘快捷键
        self.root.bind('<Left>',  lambda _: self._snap_prev())
        self.root.bind('<Right>', lambda _: self._snap_next())

    def _update_nav_label(self) -> None:
        if not self._snapshots:
            return
        label, _ = self._snapshots[self._snap_idx]
        total = len(self._snapshots)
        self._nav_label.set(f"{label}   ({self._snap_idx + 1} / {total})")
        # 边界禁用按钮
        self._btn_prev.config(
            state='normal' if self._snap_idx > 0 else 'disabled',
            bg='#334466' if self._snap_idx > 0 else '#222233',
        )
        self._btn_next.config(
            state='normal' if self._snap_idx < len(self._snapshots) - 1 else 'disabled',
            bg='#334466' if self._snap_idx < len(self._snapshots) - 1 else '#222233',
        )

    def _snap_goto(self, idx: int) -> None:
        """跳转到指定快照索引并刷新界面。"""
        if not self._snapshots or not (0 <= idx < len(self._snapshots)):
            return
        self._snap_idx = idx
        self.state = self._snapshots[idx][1]
        self._recalc_vis_rows()
        # 不同快照可能有不同的确认物品，删除与新快照冲突的幽灵
        self._remove_overlapping_phantoms()

        # 更新窗口标题、信息栏、画布
        label, _ = self._snapshots[idx]
        self.root.title(f"BidKing 物品格局  —  对局 {self.state.uid}  {label}")
        self._refresh()
        self._update_nav_label()

        # 调整 Canvas 滚动区域以匹配新行数
        cw = GRID_COLS * CELL_W + 1
        ch = GRID_ROWS * CELL_H + 1
        self.canvas.config(scrollregion=(0, 0, cw, ch))

    def _snap_prev(self) -> None:
        self._snap_goto(self._snap_idx - 1)

    def _snap_next(self) -> None:
        self._snap_goto(self._snap_idx + 1)

    def _build_canvas(self) -> None:
        outer = tk.Frame(self.root, bg='#1a1a2e')
        outer.pack(fill='both', expand=True, anchor='w', padx=8, pady=(4, 8))

        cw = GRID_COLS * CELL_W + 1
        ch = GRID_ROWS * CELL_H + 1

        v_sb = tk.Scrollbar(outer, orient='vertical')

        self.canvas = tk.Canvas(
            outer,
            width=min(cw, CANVAS_MAX_W),
            height=min(ch, CANVAS_MAX_H),
            scrollregion=(0, 0, cw, ch),
            yscrollcommand=v_sb.set,
            bg=EMPTY_BG,
            highlightthickness=0,
            takefocus=1,
        )
        v_sb.config(command=self.canvas.yview)
        self.canvas.pack(side='left', fill='y', expand=True)
        v_sb.pack(side='left', fill='y')
        self.canvas.bind('<Button-1>',        self._on_click)
        self.canvas.bind('<B1-Motion>',       self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_drag_end)
        self.canvas.bind('<Button-3>',        self._on_right_click)  # 右键删除幽灵

        # 鼠标滚轮滚动画布内容（Windows）
        self.canvas.bind('<Enter>', self._bind_mousewheel)
        self.canvas.bind('<Leave>', self._unbind_mousewheel)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.focus_set()
        self.canvas.bind_all('<MouseWheel>', self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all('<MouseWheel>')

    def _on_mousewheel(self, event: tk.Event) -> str:
        """光标在画布上时滚动 Canvas 内容。"""
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'

    # ── 绘制 ──────────────────────────────────────────────────────────────

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete('all')

        # 构建共享占位格缓存，供本次绘制所有 _compute_max_size 调用复用
        self._occupied_for_draw = self._build_occupied()

        # ── 1. 空格子背景 + BoxId 标注 ──────────────────────────────────
        for row in range(self.vis_rows):
            for col in range(GRID_COLS):
                x1, y1 = col * CELL_W, row * CELL_H
                x2, y2 = x1 + CELL_W, y1 + CELL_H
                canvas.create_rectangle(
                    x1, y1, x2, y2,
                    fill=EMPTY_BG, outline=GRID_LINE, width=1,
                )
                bid = row * GRID_COLS + col
                canvas.create_text(
                    x1 + 4, y1 + 3, text=str(bid),
                    anchor='nw', fill='#404050', font=('Consolas', 7),
                )

        # ── 1.5  第4回合后的空缺提示（橘红半透明覆盖） ──────────────────
        if self.state.current_round >= MIN_ROUND_SHOW_EMPTY:
            max_box_id = self._empty_zone_max_box_id()
            if max_box_id >= 0:
                for bid in range(min(max_box_id, GRID_COLS * GRID_ROWS - 1) + 1):
                    row = bid // GRID_COLS
                    col = bid % GRID_COLS
                    if (row, col) not in self._occupied_for_draw:
                        x1 = col * CELL_W
                        y1 = row * CELL_H
                        canvas.create_rectangle(
                            x1, y1, x1 + CELL_W, y1 + CELL_H,
                            fill=EMPTY_ZONE_COLOR,
                            stipple=EMPTY_ZONE_STIPPLE,
                            outline='',
                        )

        # ── 2. 物品格子（log 数据）────────────────────────────────────────
        for uid, k in self.state.items.items():
            if k.box_id is None:
                continue
            self._draw_item(uid, k)

        # ── 3. 幽灵物品格子（手动画框）────────────────────────────────────
        for phid, pk in self._phantom_items.items():
            if phid in self._manual_shapes:
                self._draw_item(phid, pk)

        # ── 4. 正在拖拽画框的预览虚线框 ────────────────────────────────────
        if self._phantom_draw_state:
            pds = self._phantom_draw_state
            sr, sc = pds['start_row'], pds['start_col']
            cr, cc = pds['cur_row'],   pds['cur_col']
            min_r, max_r = min(sr, cr), max(sr, cr)
            min_c, max_c = min(sc, cc), max(sc, cc)
            preview_w = max_c - min_c + 1
            preview_h = max_r - min_r + 1
            preview_invalid = self._rect_overlaps_occupied(
                min_r, min_c, preview_w, preview_h,
            )
            preview_color = '#cc4444' if preview_invalid else PHANTOM_BORDER
            px1 = min_c * CELL_W + 1
            py1 = min_r * CELL_H + 1
            px2 = (max_c + 1) * CELL_W - 1
            py2 = (max_r + 1) * CELL_H - 1
            canvas.create_rectangle(
                px1, py1, px2, py2,
                fill='', outline=preview_color, width=2, dash=(6, 3),
            )
            # 显示将要创建的大小
            canvas.create_text(
                (px1 + px2) / 2, (py1 + py2) / 2,
                text=f"{preview_w}x{preview_h}" + (" 重叠" if preview_invalid else ""),
                fill=preview_color, font=('微软雅黑', 10, 'bold'),
            )

        # 每次绘制后同步更新估价标签（含空置格估算，趁缓存还在）
        if hasattr(self, '_total_label'):
            self._update_total_label()

        # 绘制完成，释放缓存
        self._occupied_for_draw = None

    def _draw_item(self, uid: str, k: ItemKnowledge) -> None:
        canvas = self.canvas
        col, row = self._effective_display_origin(uid, k)  # 手动 or BoxId 默认左上角
        w, h = self._effective_shape_wh(uid, k)

        # 超出可视范围则跳过
        if row >= self.vis_rows or col + w > GRID_COLS:
            return

        x1 = col * CELL_W + 2
        y1 = row * CELL_H + 2
        x2 = (col + w) * CELL_W - 2
        y2 = (row + h) * CELL_H - 2
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        is_phantom = uid in self._phantom_items
        q   = self._display_quality(uid, k) or 0
        bg  = PHANTOM_BG if is_phantom else QUALITY_BG.get(q, UNKNOWN_BG)
        fg  = QUALITY_FG.get(q, UNKNOWN_FG)
        tag = f'item_{uid}'
        price_value = self._display_price_value(uid, k)
        is_high_value = (
            price_value is not None and price_value >= HIGH_VALUE_THRESHOLD
        )

        # 外边框：幽灵=青色，手动调整=黄色，普通=白色
        if is_phantom:
            border_color = PHANTOM_BORDER
            border_width = 2
        elif is_high_value:
            border_color = '#ffd34d'
            border_width = 3
        elif uid in self._manual_shapes:
            border_color = '#ffdd00'
            border_width = 2
        else:
            border_color = '#ffffff'
            border_width = 1
        canvas.create_rectangle(
            x1 - border_width, y1 - border_width,
            x2 + border_width, y2 + border_width,
            fill=border_color, outline='', tags=(tag,),
        )
        canvas.create_rectangle(
            x1, y1, x2, y2,
            fill=bg, outline='', tags=(tag,),
        )

        # 构建显示文字
        lines = self._item_text_lines(uid, k)
        text = "\n".join(lines)

        canvas.create_text(
            cx, cy, text=text,
            fill=fg,
            font=('微软雅黑', 8),
            justify='center', anchor='center',
            tags=(tag,),
        )

        if is_high_value:
            canvas.create_rectangle(
                x2 - 31, y1, x2, y1 + 14,
                fill='#ffd34d', outline='', tags=(tag,),
            )
            canvas.create_text(
                x2 - 15, y1 + 7,
                text="10万+", fill='#3a2600',
                font=('微软雅黑', 7, 'bold'),
                tags=(tag,),
            )

        # ── 缩放把手：四边（所有 log 未确认形状的物品均可手动调整） ────────
        if k.shape is None:
            hw = RESIZE_HANDLE_W
            hc = RESIZE_HANDLE_COLOR
            pad = 4
            stipple = 'gray50'
            # 东侧（改宽）
            canvas.create_rectangle(
                x2 - hw, y1 + pad, x2, y2 - pad,
                fill=hc, outline='', stipple=stipple, tags=(tag,),
            )
            # 西侧（改宽，同时移动左边界）
            canvas.create_rectangle(
                x1, y1 + pad, x1 + hw, y2 - pad,
                fill=hc, outline='', stipple=stipple, tags=(tag,),
            )
            # 南侧（改高）
            canvas.create_rectangle(
                x1 + pad, y2 - hw, x2 - pad, y2,
                fill=hc, outline='', stipple=stipple, tags=(tag,),
            )
            # 北侧（改高，同时移动上边界）
            canvas.create_rectangle(
                x1 + pad, y1, x2 - pad, y1 + hw,
                fill=hc, outline='', stipple=stipple, tags=(tag,),
            )
            # 四角实心方块（让角落更易点击）
            for cx2, cy2 in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
                canvas.create_rectangle(
                    cx2 - hw // 2, cy2 - hw // 2,
                    cx2 + hw // 2, cy2 + hw // 2,
                    fill=hc, outline='', tags=(tag,),
                )

    def _item_text_lines(self, uid: str, k: ItemKnowledge) -> List[str]:
        """
        生成格子内显示的文字行（最多 3 行）。
          - 第一行：类型/大小并排显示
          - 唯一确定：显示物品名 + 价格
          - 多候选：显示 N个候选 + 估算价
          - 无匹配：显示 无匹配
        """
        lines: List[str] = []

        # 第 1 行：类型和形状并排显示，节省小格子里的垂直空间
        type_text = ""
        if uid in self._phantom_items:
            type_text = "手动"
        elif k.categories:
            type_text = "/".join(_CAT_SHORT.get(c, str(c)) for c in sorted(k.categories))

        if k.shape:
            shape_text = ""
        elif uid in self._manual_shapes:
            mw, mh, mdc, mdr = self._manual_shapes[uid]
            shape_text = f"{mw}x{mh}*"   # * 表示手动设置
        else:
            shape_text = "?x?"

        header_text = f"{type_text} {shape_text}".strip()
        if header_text:
            lines.append(header_text)

        # 第 2-3 行：识别结果（传入负向约束 + 形状参与过滤）
        best, count, unique, est, _label = self._query_item_for_grid(uid, k)

        def _short(name: str, max_len: int = 5) -> str:
            return name[:max_len] + "…" if len(name) > max_len else name

        if k.price is not None and k.item_cid:
            # 精确已知（200021 或游戏结束揭晓）
            name = (self.csv_index[k.item_cid].name
                    if k.item_cid in self.csv_index else f"CID={k.item_cid}")
            lines.append(_short(name))
            mark = "★" if k.price >= HIGH_VALUE_THRESHOLD else ""
            lines.append(f"{mark}¥{k.price:,}")
        elif best:
            if unique:
                lines.append(_short(best.name))
                mark = "★" if best.base_value >= HIGH_VALUE_THRESHOLD else ""
                lines.append(f"{mark}¥{best.base_value:,}")
            else:
                lines.append(f"{count}个候选")
                if est is not None:
                    mark = "★" if est >= HIGH_VALUE_THRESHOLD else ""
                    lines.append(f"{mark}¥{est:.0f}")
        else:
            lines.append("无匹配")

        return lines

    # ── 点击事件 ──────────────────────────────────────────────────────────

    def _on_click(self, event: tk.Event) -> None:
        cx = int(self.canvas.canvasx(event.x))
        cy = int(self.canvas.canvasy(event.y))

        # 1. 优先检测缩放把手（鼠标按下即进入 resize 模式）
        rh = self._find_resize_handle_at(cx, cy)
        if rh:
            uid, direction = rh
            self._start_drag(uid, direction, cx, cy)
            return

        col = cx // CELL_W
        row = cy // CELL_H
        if not (0 <= col < GRID_COLS and 0 <= row < self.vis_rows):
            return

        uid = self._find_item_at(row, col)

        # 2. 有物品 → 弹窗
        if uid is not None:
            k = self._phantom_items.get(uid) or self.state.items.get(uid)
            if k:
                self._show_popup(uid, k, event.x_root, event.y_root)
            return

        # 3. 空格 → 开始画幽灵框（松手时创建）
        self._phantom_draw_state = {
            'start_row': row, 'start_col': col,
            'cur_row':   row, 'cur_col':   col,
        }

    # ── 缩放把手拖动 ──────────────────────────────────────────────────────

    def _on_right_click(self, event: tk.Event) -> None:
        """右键点击幽灵物品 → 删除该幽灵。"""
        cx = int(self.canvas.canvasx(event.x))
        cy = int(self.canvas.canvasy(event.y))
        col = cx // CELL_W
        row = cy // CELL_H
        if not (0 <= col < GRID_COLS and 0 <= row < self.vis_rows):
            return
        uid = self._find_item_at(row, col)
        if uid and uid in self._phantom_items:
            self._phantom_items.pop(uid, None)
            self._manual_shapes.pop(uid, None)
            self._draw()

    def _find_resize_handle_at(self, cx: int, cy: int) -> Optional[Tuple[str, str]]:
        """
        检测 (cx, cy) 是否落在某个缩放把手上（shape 未知的物品四边均有把手）。
        返回 (uid, 'n'|'s'|'e'|'w')，无命中返回 None。
        优先级：角落 > 边 > 主体（防止误触弹窗）。
        """
        HZ = RESIZE_HANDLE_W + 2
        for uid, k in self.state.items.items():
            if k.shape is not None or k.box_id is None:
                continue
            dc, dr = self._effective_display_origin(uid, k)
            w, h   = self._effective_shape_wh(uid, k)
            x1 = dc * CELL_W + 2
            y1 = dr * CELL_H + 2
            x2 = (dc + w) * CELL_W - 2
            y2 = (dr + h) * CELL_H - 2
            in_x = x1 <= cx <= x2
            in_y = y1 <= cy <= y2
            # 南侧
            if y2 - HZ <= cy <= y2 and in_x:
                return uid, 's'
            # 北侧
            if y1 <= cy <= y1 + HZ and in_x:
                return uid, 'n'
            # 东侧
            if x2 - HZ <= cx <= x2 and in_y:
                return uid, 'e'
            # 西侧
            if x1 <= cx <= x1 + HZ and in_y:
                return uid, 'w'
        return None

    def _start_drag(self, uid: str, direction: str, cx: int, cy: int) -> None:
        k = self.state.items.get(uid)
        if not k:
            return
        w, h   = self._effective_shape_wh(uid, k)
        dc, dr = self._effective_display_origin(uid, k)
        self._drag_state = {
            'uid':       uid,
            'direction': direction,   # 'n'|'s'|'e'|'w'
            'start_cx':  cx,
            'start_cy':  cy,
            'orig_w':    w,
            'orig_h':    h,
            'orig_dc':   dc,   # 拖拽起始显示列
            'orig_dr':   dr,   # 拖拽起始显示行
        }

    def _on_drag(self, event: tk.Event) -> None:  # noqa: C901
        """
        拖拽分三模式：
          resize  → 四方向拖动已有物品的边界
          phantom → 拖动画出新幽灵物品的范围（预览虚线框）
        
          e/s：右/下边界移动，左上角不动；BoxId 必须保留在矩形内。
          w/n：左/上边界移动，右下角不动；同样保证 BoxId 在矩形内。
        碰撞检测：只对"新增"的格列/行检查是否与已确认物品重叠。
        """
        # ── 画框模式 ──────────────────────────────────────────────────
        if self._phantom_draw_state is not None:
            cx = int(self.canvas.canvasx(event.x))
            cy = int(self.canvas.canvasy(event.y))
            col = max(0, min(cx // CELL_W, GRID_COLS - 1))
            row = max(0, min(cy // CELL_H, self.vis_rows - 1))
            pds = self._phantom_draw_state
            if row != pds['cur_row'] or col != pds['cur_col']:
                pds['cur_row'] = row
                pds['cur_col'] = col
                self._draw()
            return

        # ── 缩放把手模式 ──────────────────────────────────────────────
        if not self._drag_state:
            return
        ds  = self._drag_state
        uid = ds['uid']
        k   = self.state.items.get(uid)
        if not k:
            return

        cx = int(self.canvas.canvasx(event.x))
        cy = int(self.canvas.canvasy(event.y))
        dx_cells = (cx - ds['start_cx']) / CELL_W
        dy_cells = (cy - ds['start_cy']) / CELL_H

        w0, h0   = ds['orig_w'], ds['orig_h']
        dc0, dr0 = ds['orig_dc'], ds['orig_dr']
        direction = ds['direction']

        brow = (k.box_id // GRID_COLS) if k.box_id is not None else dr0
        bcol = (k.box_id % GRID_COLS)  if k.box_id is not None else dc0

        occ = self._build_occupied(exclude_uid=uid)

        # ── 各方向计算 ────────────────────────────────────────────────────
        if direction == 'e':
            # 右边界移动，左上角(dc0,dr0)不变
            delta = round(dx_cells)
            # 扩张时检查新列
            if delta > 0:
                max_ext = 0
                for c in range(dc0 + w0, GRID_COLS):
                    if any((dr0 + r, c) in occ for r in range(h0)):
                        break
                    max_ext += 1
                delta = min(delta, max_ext)
            # 缩短时保证 BoxId 不越出右边界
            new_w = max(bcol - dc0 + 1, max(1, w0 + delta))
            new_h = h0
            new_dc, new_dr = dc0, dr0

        elif direction == 'w':
            # 左边界移动，右边(dc0+w0-1)不变
            delta = round(dx_cells)   # 向左为负
            if delta < 0:             # 扩张（往左）
                max_ext = 0
                for c in range(dc0 - 1, -1, -1):
                    if any((dr0 + r, c) in occ for r in range(h0)):
                        break
                    max_ext += 1
                delta = max(delta, -max_ext)
            raw_dc = dc0 + delta
            # 不能越过 BoxId（BoxId 必须在矩形内）
            new_dc = max(0, min(raw_dc, bcol))
            new_w  = max(1, dc0 + w0 - new_dc)
            new_h  = h0
            new_dr = dr0

        elif direction == 's':
            # 下边界移动，左上角不变
            delta = round(dy_cells)
            if delta > 0:
                max_ext = 0
                for r in range(dr0 + h0, GRID_ROWS):
                    if any((r, dc0 + c) in occ for c in range(w0)):
                        break
                    max_ext += 1
                delta = min(delta, max_ext)
            new_h  = max(brow - dr0 + 1, max(1, h0 + delta))
            new_w  = w0
            new_dc, new_dr = dc0, dr0

        elif direction == 'n':
            # 上边界移动，下边(dr0+h0-1)不变
            delta = round(dy_cells)   # 向上为负
            if delta < 0:
                max_ext = 0
                for r in range(dr0 - 1, -1, -1):
                    if any((r, dc0 + c) in occ for c in range(w0)):
                        break
                    max_ext += 1
                delta = max(delta, -max_ext)
            raw_dr = dr0 + delta
            new_dr = max(0, min(raw_dr, brow))
            new_h  = max(1, dr0 + h0 - new_dr)
            new_w  = w0
            new_dc = dc0
        else:
            return

        # 网格边界最终夹紧
        new_dc = max(0, min(new_dc, GRID_COLS - 1))
        new_dr = max(0, min(new_dr, GRID_ROWS - 1))
        new_w  = max(1, min(new_w, GRID_COLS - new_dc))
        new_h  = max(1, min(new_h, GRID_ROWS - new_dr))

        new_shape = (new_w, new_h, new_dc, new_dr)
        if self._manual_shapes.get(uid) != new_shape:
            self._manual_shapes[uid] = new_shape
            self._draw()

    def _on_drag_end(self, event: tk.Event) -> None:
        if self._phantom_draw_state is not None:
            pds = self._phantom_draw_state
            sr, sc = pds['start_row'], pds['start_col']
            cr, cc = pds['cur_row'],   pds['cur_col']
            min_r, max_r = min(sr, cr), max(sr, cr)
            min_c, max_c = min(sc, cc), max(sc, cc)
            self._create_phantom(min_r, min_c, max_c - min_c + 1, max_r - min_r + 1)
            self._phantom_draw_state = None
            self._draw()
        elif self._drag_state:
            self._drag_state = None

    def _find_item_at(self, row: int, col: int) -> Optional[str]:
        """返回覆盖 (row, col) 的物品 UID（含幽灵），无则 None。"""
        for uid, k in self.state.items.items():
            if k.box_id is None:
                continue
            sc, sr = self._effective_display_origin(uid, k)
            w, h = self._effective_shape_wh(uid, k)
            if sr <= row < sr + h and sc <= col < sc + w:
                return uid
        # 幽灵物品（始终有 _manual_shapes 记录）
        for phid in self._phantom_items:
            if phid not in self._manual_shapes:
                continue
            w, h, dc, dr = self._manual_shapes[phid]
            if dr <= row < dr + h and dc <= col < dc + w:
                return phid
        return None

    # ── 候选弹窗 ──────────────────────────────────────────────────────────

    def _show_popup(
        self, uid: str, k: ItemKnowledge,
        mouse_x: int = 200, mouse_y: int = 200,
    ) -> None:
        popup = tk.Toplevel(self.root)
        popup.title(f"物品候选  BoxId={k.box_id}")
        popup.transient(self.root)
        popup.configure(bg='#f5f5f8')

        # 弹窗尺寸
        pw, ph = 560, 360

        # 让 tkinter 先完成布局，再读屏幕尺寸
        popup.update_idletasks()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()

        # 以鼠标位置为中心弹出，并确保不超出屏幕边界
        ox = mouse_x - pw // 2
        oy = mouse_y - ph // 2
        if ox + pw > sw:
            ox = max(0, sw - pw)
        if oy + ph > sh:
            oy = max(0, sh - ph)
        ox = max(0, ox)
        oy = max(0, oy)

        popup.geometry(f"{pw}x{ph}+{ox}+{oy}")
        popup.grab_set()

        # ── 标题行 ──────────────────────────────────────────────────────
        hdr_parts = []
        if k.shape:
            hdr_parts.append(f"形状: {fmt_shape(k.shape)}")
        elif uid in self._manual_shapes:
            mw, mh, mdc, mdr = self._manual_shapes[uid]
            tag = "手动画框" if uid in self._phantom_items else "手动设置"
            hdr_parts.append(f"形状: {mw}x{mh}（{tag}，精确匹配）")
        elif k.box_id is not None:
            max_w, max_h = self._compute_max_size(uid, k)
            if max_w < GRID_COLS or max_h < GRID_ROWS:
                hdr_parts.append(f"形状: ≤{max_w}x{max_h}（推断上界，非精确）")
        display_quality = self._display_quality(uid, k)
        if display_quality:
            if k.quality is not None:
                hdr_parts.append(f"品质: Q{k.quality}")
            else:
                hdr_parts.append(f"品质: Q{display_quality}（唯一补齐）")
        if k.categories:
            cats = " / ".join(CATEGORY_NAMES.get(c, str(c)) for c in sorted(k.categories))
            hdr_parts.append(f"类别: {cats}")
        if k.item_cid:
            hdr_parts.append(f"CID: {k.item_cid}")
        hdr_text = "    |    ".join(hdr_parts) if hdr_parts else "（属性未知）"

        q = display_quality or 0
        hdr_bg = QUALITY_BG.get(q, '#888888')
        tk.Label(
            popup, text=hdr_text,
            bg=hdr_bg, fg='#ffffff',
            font=('微软雅黑', 10, 'bold'),
            pady=6, padx=10, anchor='w',
        ).pack(fill='x')

        # ── 过滤候选（含负向约束 + 最大尺寸推断） ──────────────────────────
        candidates = self._candidate_items_for_grid(uid, k)
        candidates.sort(key=lambda i: -i.base_value)
        candidate_probs = candidate_probabilities(
            candidates,
            map_category_weights=self._map_category_weights,
            map_id=self.state.map_id,
        )
        prob_source = probability_source_label(candidates, self.state.map_id)

        # ── 统计摘要 ────────────────────────────────────────────────────
        n = len(candidates)
        if n > 1:
            prices = [i.base_value for i in candidates]
            min_p, max_p = min(prices), max(prices)
            _best, _count, _unique, weighted_est, weighted_label = self._query_item_for_grid(uid, k)
            weighted_text = (
                f"{weighted_label}: ¥{weighted_est:,.0f}    "
                if weighted_est is not None else ""
            )
            stat_text = (
                f"共 {n} 个候选    "
                f"{weighted_text}"
                f"范围: ¥{min_p:,} ~ ¥{max_p:,}    概率: {prob_source}"
            )
        elif n == 1:
            stat_text = (
                f"唯一确定: {candidates[0].name}    "
                f"¥{candidates[0].base_value:,}    概率: {prob_source}"
            )
        else:
            stat_text = "无匹配候选"

        tk.Label(
            popup, text=stat_text,
            bg='#ebebf0', fg='#444455',
            font=('微软雅黑', 9), pady=4, padx=8, anchor='w',
        ).pack(fill='x')

        # ── 已排除类别 ──────────────────────────────────────────────────
        if k.excluded_categories:
            excl_names = " / ".join(
                CATEGORY_NAMES.get(c, str(c)) for c in sorted(k.excluded_categories)
            )
            tk.Label(
                popup,
                text=f"  已排除类别（{len(k.excluded_categories)}个）: {excl_names}",
                bg='#f5e8e8', fg='#883333',
                font=('微软雅黑', 8), pady=3, padx=10, anchor='w',
            ).pack(fill='x')

        # ── 候选列表 ────────────────────────────────────────────────────
        frame = tk.Frame(popup, bg='#f5f5f8')
        frame.pack(fill='both', expand=True, padx=8, pady=(5, 3))

        cols_def = [
            ('名称',   125, 'w'),
            ('品质',    45, 'center'),
            ('形状',    50, 'center'),
            ('类别',   120, 'w'),
            ('概率',    65, 'e'),
            ('价格',    75, 'e'),
        ]
        tree = ttk.Treeview(
            frame,
            columns=[c[0] for c in cols_def],
            show='headings',
            height=10,
        )
        style = ttk.Style()
        style.configure('Treeview', font=('微软雅黑', 9), rowheight=20)
        style.configure('Treeview.Heading', font=('微软雅黑', 9, 'bold'))

        for col_name, width, anchor in cols_def:
            tree.heading(col_name, text=col_name)
            tree.column(col_name, width=width, anchor=anchor, minwidth=40)

        vsb = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True)

        # 用颜色区分价格高低（高于中位价的标黄，最高价标橙）
        median_val = statistics.median([i.base_value for i in candidates]) if n > 1 else 0
        top_val    = candidates[0].base_value if candidates else 0
        tree.tag_configure('top',    background='#ffe4b0')
        tree.tag_configure('valuable', background='#ffd6d6')
        tree.tag_configure('high',   background='#fffff0')
        tree.tag_configure('normal', background='#ffffff')
        tree.tag_configure('confirmed', background='#d9f7d9')

        iid_to_item: Dict[str, CsvItem] = {}
        selected_iid: Optional[str] = None
        confirmed_cid = k.manual_confirm_item_id
        for item in candidates:
            cat_str = " / ".join(
                CATEGORY_NAMES.get(c, str(c)) for c in item.category_tags
            )
            if confirmed_cid and item.item_id == confirmed_cid:
                tag = 'confirmed'
            elif item.base_value >= HIGH_VALUE_THRESHOLD:
                tag = 'valuable'
            elif item.base_value == top_val and n > 1:
                tag = 'top'
            elif item.base_value >= median_val:
                tag = 'high'
            else:
                tag = 'normal'
            iid = tree.insert('', 'end', values=(
                item.name,
                f"Q{item.quality}",
                fmt_shape(item.shape),
                cat_str,
                f"{candidate_probs.get(item.item_id, 0.0) * 100:.2f}%",
                f"¥{item.base_value:,}",
            ), tags=(tag,))
            iid_to_item[iid] = item
            if confirmed_cid and item.item_id == confirmed_cid:
                selected_iid = iid

        status_var = tk.StringVar(value="双击候选可确认；确认后将用于价格/估算/品质显示。")

        def _update_status_from_item(item: CsvItem, confirmed: bool = False) -> None:
            if confirmed:
                status_var.set(
                    f"已确认：{item.name}  Q{item.quality}  ¥{item.base_value:,}"
                )
            else:
                status_var.set(
                    f"当前选择：{item.name}  Q{item.quality}  ¥{item.base_value:,}"
                )

        def _on_select(_event: tk.Event) -> None:
            sel = tree.selection()
            if not sel:
                return
            item = iid_to_item.get(sel[0])
            if item:
                _update_status_from_item(item, confirmed=False)

        def _confirm_selected(_event: Optional[tk.Event] = None) -> None:
            sel = tree.selection()
            if not sel:
                return
            item = iid_to_item.get(sel[0])
            if item is None:
                return
            k.manual_confirm_item_id = item.item_id
            self._refresh()
            popup.destroy()

        def _clear_confirmation() -> None:
            if k.manual_confirm_item_id is None:
                popup.destroy()
                return
            k.manual_confirm_item_id = None
            self._refresh()
            popup.destroy()

        tree.bind('<<TreeviewSelect>>', _on_select)
        tree.bind('<Double-1>', _confirm_selected)
        if selected_iid is not None:
            tree.selection_set(selected_iid)
            tree.focus(selected_iid)
            tree.see(selected_iid)
            confirmed_item = iid_to_item.get(selected_iid)
            if confirmed_item:
                _update_status_from_item(confirmed_item, confirmed=True)
        elif candidates:
            first = tree.get_children()[0]
            tree.selection_set(first)
            tree.focus(first)

        # ── 关闭按钮 ────────────────────────────────────────────────────
        tk.Label(
            popup, textvariable=status_var,
            bg='#eef3ff', fg='#334466',
            font=('微软雅黑', 9), pady=4, padx=10, anchor='w',
        ).pack(fill='x', padx=8, pady=(0, 3))

        btn_frame = tk.Frame(popup, bg='#f5f5f8')
        btn_frame.pack(pady=4)
        tk.Button(
            btn_frame, text="确认所选后选项",
            command=_confirm_selected,
            font=('微软雅黑', 9), relief='flat',
            bg='#2f8f46', fg='white', padx=10, pady=4,
            cursor='hand2',
        ).pack(side='left', padx=4)
        tk.Button(
            btn_frame, text="取消确认",
            command=_clear_confirmation,
            font=('微软雅黑', 9), relief='flat',
            bg='#8f5f2f', fg='white', padx=10, pady=4,
            cursor='hand2',
        ).pack(side='left', padx=4)
        tk.Button(
            btn_frame, text="  关  闭  ",
            command=popup.destroy,
            font=('微软雅黑', 9), relief='flat',
            bg='#5566aa', fg='white', padx=10, pady=4,
            cursor='hand2',
        ).pack(side='left', padx=4)

    # ── 启动 ──────────────────────────────────────────────────────────────

    def run(self) -> None:
        """启动 tkinter 主循环（阻塞直到窗口关闭）。"""
        self.root.mainloop()
