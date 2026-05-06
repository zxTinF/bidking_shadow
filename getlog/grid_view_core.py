# -*- coding: utf-8 -*-
"""GridWindow core logic."""

from typing import Dict, List, Optional, Tuple

from .constants import CATEGORY_NAMES
from .grid_view_shared import (
    EMPTY_CELL_VALUE,
    GRID_COLS,
    GRID_ROWS,
    MIN_ROUND_SHOW_EMPTY,
    _CAT_SHORT,
)
from .item_db import map_category_ratios, query_item
from .models import CsvItem, ItemKnowledge

try:
    from .hidden_layout import (
        analyze_hidden_q56,
        build_observed_item,
        possible_cells_for_candidate_shapes,
    )
except Exception:
    analyze_hidden_q56 = None
    build_observed_item = None
    possible_cells_for_candidate_shapes = None


class GridWindowCoreMixin:
    """Methods that do not directly build Tk widgets."""

    def _recalc_vis_rows(self) -> None:
        self.vis_rows = GRID_ROWS

    @staticmethod
    def _shape_wh(shape: Optional[int]) -> Tuple[int, int]:
        if shape is None:
            return 1, 1
        s = str(shape)
        if len(s) == 2 and s.isdigit():
            return int(s[0]), int(s[1])
        return 1, 1

    def _effective_shape_wh(self, uid: str, k: ItemKnowledge) -> Tuple[int, int]:
        if k.shape is not None:
            return self._shape_wh(k.shape)
        if uid in self._manual_shapes:
            w, h, _, _ = self._manual_shapes[uid]
            return w, h
        return 1, 1

    def _effective_display_origin(self, uid: str, k: ItemKnowledge) -> Tuple[int, int]:
        if uid in self._manual_shapes:
            _, _, dc, dr = self._manual_shapes[uid]
            return dc, dr
        if k.box_id is None:
            return 0, 0
        return k.box_id % GRID_COLS, k.box_id // GRID_COLS

    def _shape_fits_at_display_origin(
        self, uid: str, k: ItemKnowledge, w: int, h: int
    ) -> bool:
        col, row = self._effective_display_origin(uid, k)
        return not self._rect_overlaps_occupied(row, col, w, h, exclude_uid=uid)

    def _build_occupied(self, exclude_uid: str = "") -> set:
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
        return {(row + ddr, col + ddc) for ddr in range(h) for ddc in range(w)}

    def _rect_overlaps_occupied(
        self,
        row: int,
        col: int,
        w: int,
        h: int,
        exclude_uid: str = "",
    ) -> bool:
        if row < 0 or col < 0 or w <= 0 or h <= 0:
            return True
        if col + w > GRID_COLS or row + h > GRID_ROWS:
            return True
        occupied = self._build_occupied(exclude_uid=exclude_uid)
        return any(cell in occupied for cell in self._rect_cells(row, col, w, h))

    def _is_high_quality_range(self, k: ItemKnowledge) -> bool:
        return False

    def _effective_quality_for_constraints(self, k: ItemKnowledge) -> Optional[int]:
        if k.manual_quality in (5, 6):
            return k.manual_quality
        if self._is_high_quality_range(k):
            return None
        if k.quality is not None:
            return k.quality
        return k.manual_quality

    def _query_item_for_grid(
        self, uid: str, k: ItemKnowledge
    ) -> Tuple[Optional[CsvItem], int, bool, Optional[float], str]:
        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return manual_item, 1, True, float(manual_item.base_value), "手动确认"

        effective_shape = k.shape
        max_shape: Optional[Tuple[int, int]] = None
        effective_quality = self._effective_quality_for_constraints(k)

        if k.shape is None:
            if uid in self._manual_shapes:
                mw, mh, _, _ = self._manual_shapes[uid]
                effective_shape = mw * 10 + mh
            elif k.box_id is not None:
                max_w, max_h = self._compute_max_size(uid, k)
                if max_w < GRID_COLS or max_h < GRID_ROWS:
                    max_shape = (max_w, max_h)

        return query_item(
            effective_shape,
            effective_quality,
            k.categories,
            k.item_cid,
            self.csv_index,
            self.csv_items,
            k.excluded_categories,
            k.excluded_qualities,
            max_shape_wh=max_shape,
            map_category_weights=self._map_category_weights,
            map_id=self.state.map_id,
        )

    def _valid_manual_confirm_item(
        self, uid: str, k: ItemKnowledge
    ) -> Optional[CsvItem]:
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
            if k.box_id_confirmed:

                def _shape_fits(shape: int) -> bool:
                    ss = str(shape)
                    if len(ss) != 2 or not ss.isdigit():
                        return False
                    w = int(ss[0])
                    h = int(ss[1])
                    return self._shape_fits_at_display_origin(uid, k, w, h)

                candidates = [i for i in candidates if _shape_fits(i.shape)]
            else:
                max_w, max_h = self._compute_max_size(uid, k)
                if max_w < GRID_COLS or max_h < GRID_ROWS:

                    def _shape_fits(shape: int) -> bool:
                        ss = str(shape)
                        if len(ss) == 2 and ss.isdigit():
                            return int(ss[0]) <= max_w and int(ss[1]) <= max_h
                        return False

                    candidates = [i for i in candidates if _shape_fits(i.shape)]

        effective_quality = self._effective_quality_for_constraints(k)
        if effective_quality is not None:
            candidates = [i for i in candidates if i.quality == effective_quality]
        if k.excluded_qualities:
            candidates = [
                i for i in candidates if i.quality not in k.excluded_qualities
            ]
        if k.categories:
            with_cat = [
                i
                for i in candidates
                if all(c in i.category_tags for c in k.categories)
            ]
            if with_cat:
                candidates = with_cat
        if k.excluded_categories:
            candidates = [
                i
                for i in candidates
                if not any(c in k.excluded_categories for c in i.category_tags)
            ]
        return candidates

    def _display_quality(self, uid: str, k: ItemKnowledge) -> Optional[int]:
        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return manual_item.quality
        if k.manual_quality is not None:
            return k.manual_quality
        if k.quality is not None and not self._is_high_quality_range(k):
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
        empty_count = self._compute_empty_zone_count() or 0
        total += empty_count * EMPTY_CELL_VALUE
        return total

    def _query_floor_price_for_grid(self, uid: str, k: ItemKnowledge) -> Optional[float]:
        if k.price is not None and k.item_cid:
            return float(k.price)
        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return float(manual_item.base_value)
        candidates = self._candidate_items_for_grid(uid, k)
        if not candidates:
            return None
        return float(min(item.base_value for item in candidates))

    def _calc_grid_floor_price(self) -> float:
        total = 0.0
        item_sources = (self.state.items, self._phantom_items)
        for items in item_sources:
            for uid, k in items.items():
                price = self._query_floor_price_for_grid(uid, k)
                if price is not None:
                    total += price
        return total

    def _possible_high_quality_cells(self) -> Tuple[int, int]:
        q5 = 0
        q6 = 0
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())
        for uid, k in item_rows:
            if k.box_id is None:
                continue
            w, h = self._effective_shape_wh(uid, k)
            cells = w * h
            q = self._display_quality(uid, k)
            if q == 5:
                q5 += cells
            elif q == 6:
                q6 += cells
        return q5, q6

    def _known_layout_cells_count(self) -> int:
        total = 0
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())
        for uid, k in item_rows:
            if k.box_id is None:
                continue
            w, h = self._effective_shape_wh(uid, k)
            total += w * h
        return total

    def _estimated_item_cells_range(self) -> Optional[Tuple[int, int]]:
        total = self._known_layout_cells_count()
        return total, total

    def _stack_estimated_total_cells_range(self) -> Optional[Tuple[int, int]]:
        total_cells = self._known_layout_cells_count()
        empty_count = self._compute_empty_zone_count()
        if empty_count is None:
            return total_cells, total_cells
        est = total_cells + empty_count
        return est, est

    def _hidden_analysis_signature(self):
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())
        item_sig = []
        for uid, k in sorted(item_rows, key=lambda pair: pair[0]):
            item_sig.append(
                (
                    uid,
                    k.box_id,
                    k.box_id_confirmed,
                    k.shape,
                    k.quality,
                    k.manual_quality,
                    k.item_cid,
                    k.price,
                    tuple(sorted(k.categories)),
                    tuple(sorted(k.excluded_categories)),
                    tuple(sorted(k.excluded_qualities)),
                )
            )
        manual_sig = tuple(
            sorted(
                (uid, w, h, dc, dr)
                for uid, (w, h, dc, dr) in self._manual_shapes.items()
            )
        )
        return (
            self.state.uid,
            self.state.map_id,
            self.state.current_round,
            tuple(item_sig),
            manual_sig,
        )

    def _hidden_analysis(self):
        if (
            analyze_hidden_q56 is None
            or build_observed_item is None
            or possible_cells_for_candidate_shapes is None
        ):
            return None
        if self.state.current_round < 4:
            return None
        cache_key = self._hidden_analysis_signature()
        if getattr(self, "_hidden_analysis_cache_key", None) == cache_key:
            return getattr(self, "_hidden_analysis_cache", None)
        stack_range = self._stack_estimated_total_cells_range()
        if stack_range is None:
            return None

        known_items = []
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())
        for uid, k in item_rows:
            if k.box_id is None:
                continue
            width, height = self._effective_shape_wh(uid, k)
            col, row = self._effective_display_origin(uid, k)
            possible_cells = None
            effective_quality = k.manual_quality if k.manual_quality is not None else k.quality
            if effective_quality in (1, 2, 3, 4):
                candidates = self._candidate_items_for_grid(uid, k)
                candidate_shapes = {item.shape for item in candidates}
                if candidate_shapes:
                    possible_cells = possible_cells_for_candidate_shapes(
                        row=row,
                        col=col,
                        box_id_confirmed=k.box_id_confirmed,
                        shapes=candidate_shapes,
                    )
            known_items.append(
                build_observed_item(
                    uid=uid,
                    row=row,
                    col=col,
                    width=width,
                    height=height,
                    box_id_confirmed=k.box_id_confirmed,
                    quality=effective_quality,
                    item_cid=k.item_cid,
                    categories=k.categories,
                    excluded_categories=k.excluded_categories,
                    excluded_qualities=k.excluded_qualities,
                    possible_cells=possible_cells,
                )
            )

        analysis = analyze_hidden_q56(
            map_id=self.state.map_id,
            round_no=self.state.current_round,
            estimated_total_cells=stack_range[0],
            known_items=tuple(known_items),
            csv_items=self.csv_items,
            map_category_weights=self._map_category_weights,
        )
        self._hidden_analysis_cache_key = cache_key
        self._hidden_analysis_cache = analysis
        return analysis

    def _info_summary_text(self) -> str:
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())
        total_cells = 0
        item_count = 0
        quality_stats = {quality: {"count": 0, "cells": 0} for quality in range(1, 7)}

        for uid, k in item_rows:
            if k.box_id is None:
                continue
            w, h = self._effective_shape_wh(uid, k)
            cells = w * h
            total_cells += cells
            item_count += 1
            quality = self._display_quality(uid, k)
            if quality in quality_stats:
                quality_stats[quality]["count"] += 1
                quality_stats[quality]["cells"] += cells

        avg_cells = total_cells / item_count if item_count else 0.0
        top_cats = "类别TOP5: -"
        category_ratios = map_category_ratios(self.state.map_id)
        if not category_ratios and self._map_category_weights:
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
            ranked = sorted(category_ratios.items(), key=lambda kv: kv[1], reverse=True)
            top_parts: List[str] = []
            for cid, ratio in ranked[:5]:
                pct = ratio * 100.0
                cat_short = _CAT_SHORT.get(cid, CATEGORY_NAMES.get(cid, str(cid))[:2])
                top_parts.append(f"{cat_short}{pct:.0f}%")
            top_cats = "类别TOP5: " + " / ".join(top_parts)

        stack_range = self._stack_estimated_total_cells_range()
        stack_est_total = stack_range[0] if stack_range is not None else total_cells
        low_mid_cells = sum(quality_stats[q]["cells"] for q in (1, 2, 3, 4))

        def _quality_text(label: str, quality: int) -> str:
            count = quality_stats[quality]["count"]
            cells = quality_stats[quality]["cells"]
            avg = cells / count if count else 0.0
            return f"已知{label}: {count}件 {cells}格 {avg:.2f}均格"

        lines = [
            f"地图: {self.state.map_id}   第 {self.state.current_round} 回合   {top_cats}",
            (
                f"已知物品: {len(self.state.items)} 件   "
                f"目前物品占格: {total_cells}   "
                f"平均格数: {avg_cells:.2f}   "
                f"估计总格数: {stack_est_total}   "
                f"白绿蓝紫总格数: {low_mid_cells}"
            ),
            "   ".join((_quality_text("白", 1), _quality_text("绿", 2))),
            "   ".join((_quality_text("蓝", 3), _quality_text("紫", 4))),
            "   ".join((_quality_text("红", 6), _quality_text("金", 5))),
        ]

        hidden = self._hidden_analysis()
        if hidden is not None and hidden.snapshot.forced_hidden_cells:
            hidden_text = (
                f"隐藏下界: {len(hidden.snapshot.forced_hidden_cells)}格   "
                f"区域: {len(hidden.regions)}   "
                f"方案: {len(hidden.plans)}"
            )
            if (
                hidden.conservative_value is not None
                and hidden.expected_value is not None
                and hidden.aggressive_value is not None
            ):
                hidden_text += (
                    f"   估值: ¥{hidden.conservative_value:,.0f}"
                    f" ~ ¥{hidden.aggressive_value:,.0f}"
                    f" (期望 ¥{hidden.expected_value:,.0f})"
                )
            lines.append(hidden_text)
        return "\n".join(lines)

    def _compute_empty_zone_count(self) -> Optional[int]:
        if self.state.current_round < MIN_ROUND_SHOW_EMPTY:
            return None
        max_box_id = self._empty_zone_max_box_id()
        if max_box_id < 0:
            return None
        occupied = (
            self._occupied_for_draw
            if self._occupied_for_draw is not None
            else self._build_occupied()
        )
        count = 0
        for bid in range(min(max_box_id, GRID_COLS * GRID_ROWS - 1) + 1):
            row = bid // GRID_COLS
            col = bid % GRID_COLS
            if (row, col) not in occupied:
                count += 1
        return count

    def _remove_overlapping_phantoms(self) -> None:
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
            if any(
                (dr + ddr, dc + ddc) in confirmed_occ
                for ddr in range(h)
                for ddc in range(w)
            ):
                to_del.append(phid)
        for phid in to_del:
            self._phantom_items.pop(phid, None)
            self._manual_shapes.pop(phid, None)

    def _apply_scan_history_to_phantoms(self) -> None:
        for phid, pk in self._phantom_items.items():
            for scan_type, value, hit_uids in self.state._scan_history:
                if phid in hit_uids:
                    continue
                if scan_type == "category":
                    pk.excluded_categories.add(value)
                else:
                    pk.excluded_qualities.add(value)

    def _create_phantom(self, row: int, col: int, w: int, h: int) -> bool:
        if self._rect_overlaps_occupied(row, col, w, h):
            return False
        phid = f"phantom_{self._phantom_counter}"
        self._phantom_counter += 1
        pk = ItemKnowledge(uid=phid)
        pk.box_id = row * GRID_COLS + col
        pk.box_id_confirmed = True
        self._phantom_items[phid] = pk
        self._manual_shapes[phid] = (w, h, col, row)
        self._apply_scan_history_to_phantoms()
        return True

    def _compute_max_size(self, uid: str, k: ItemKnowledge) -> Tuple[int, int]:
        if k.box_id is None:
            return GRID_COLS, GRID_ROWS
        brow = k.box_id // GRID_COLS
        bcol = k.box_id % GRID_COLS
        if self._occupied_for_draw is not None:
            dc0, dr0 = self._effective_display_origin(uid, k)
            w0, h0 = self._effective_shape_wh(uid, k)
            own = frozenset(
                (dr0 + ddr, dc0 + ddc) for ddr in range(h0) for ddc in range(w0)
            )
            occupied = self._occupied_for_draw - own
        else:
            occupied = self._build_occupied(exclude_uid=uid)

        def _scan_right() -> int:
            n = 0
            for c in range(bcol, GRID_COLS):
                if (brow, c) in occupied:
                    break
                n += 1
            return n - 1

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
        left_ext = _scan_left()
        down_ext = _scan_down()
        up_ext = _scan_up()
        max_w = max(1, left_ext + 1 + right_ext)
        max_h = max(1, up_ext + 1 + down_ext)
        return max_w, max_h

    def _empty_zone_max_box_id(self) -> int:
        max_box_id = -1
        for k in self.state.items.values():
            if k.box_id is None or not k.box_id_confirmed:
                continue
            max_box_id = max(max_box_id, k.box_id)
        return max_box_id
