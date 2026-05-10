# -*- coding: utf-8 -*-
"""GridWindow core logic."""

import math
import time
from typing import Dict, List, Optional, Set, Tuple

from .constants import CATEGORY_NAMES
from .grid_view_shared import (
    GRID_COLS,
    GRID_ROWS,
    MIN_ROUND_SHOW_EMPTY,
    _CAT_SHORT,
)
from .item_db import (
    MAP_TO_TIER_NEST,
    candidate_probabilities,
    map_category_ratios,
    normalize_map_id,
    query_item,
)
from .models import CsvItem, ItemKnowledge
from .posterior_estimator import (
    WeightedValue,
    price_likelihood,
)

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

    ESTIMATE_METHOD_EXPECTED = "1.候选价值期望"
    ESTIMATE_METHOD_VARIANCE_ADJUSTED = "2.方差校正期望"
    ESTIMATE_METHOD_TRIMMED_MEAN = "3.截尾均值估价"
    ESTIMATE_METHOD_85_PERCENT = "4.85%期望估价"
    ESTIMATE_METHODS = (
        ESTIMATE_METHOD_EXPECTED,
        ESTIMATE_METHOD_VARIANCE_ADJUSTED,
        ESTIMATE_METHOD_TRIMMED_MEAN,
        ESTIMATE_METHOD_85_PERCENT,
    )

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
        if uid in self._manual_shapes:
            w, h, _, _ = self._manual_shapes[uid]
            return w, h
        if k.shape is not None:
            return self._shape_wh(k.shape)
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
        return (
            any(pk is k for pk in self._phantom_items.values())
            and k.manual_quality is None
            and k.quality is None
            and k.manual_confirm_item_id is None
        )

    def _is_gold_red_candidate_range(self, uid: str, k: ItemKnowledge) -> bool:
        candidates = self._candidate_items_for_grid(uid, k)
        if not candidates:
            return False
        qualities = {item.quality for item in candidates}
        return bool(qualities) and qualities.issubset({5, 6})

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

        if self._is_high_quality_range(k):
            candidates = self._candidate_items_for_grid(uid, k)
            if not candidates:
                return None, 0, False, None, ""
            best = max(candidates, key=lambda item: item.base_value)
            if len(candidates) == 1:
                return best, 1, True, None, ""
            probs = candidate_probabilities(
                candidates,
                self._map_category_weights,
                self.state.map_id,
            )
            est = sum(
                item.base_value * probs.get(item.item_id, 0.0)
                for item in candidates
            )
            return best, len(candidates), False, est, "Q5/Q6权重价"

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
        if self._is_high_quality_range(k):
            candidates = [i for i in candidates if i.quality in (5, 6)]
        elif effective_quality is not None:
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

    def _gold_count_target(self) -> Optional[int]:
        values = self._resolved_gold_input_values()
        target = values.get("gold_count") if isinstance(values, dict) else None
        return target if isinstance(target, int) and target >= 0 else None

    def _gold_cells_target(self) -> Optional[int]:
        values = self._resolved_gold_input_values()
        target = values.get("gold_total_cells") if isinstance(values, dict) else None
        return target if isinstance(target, int) and target >= 0 else None

    def _gold_avg_target(self) -> Optional[float]:
        values = self._resolved_gold_input_values()
        target = values.get("gold_avg_cells") if isinstance(values, dict) else None
        return target if isinstance(target, (int, float)) and target > 0 else None

    def _resolved_gold_input_values(
        self,
        values: Optional[Dict[str, object]] = None,
    ) -> Dict[str, object]:
        raw = dict(
            values
            if values is not None
            else getattr(self, "_captured_input_values", {})
        )

        count = raw.get("gold_count")
        cells = raw.get("gold_total_cells")
        avg = raw.get("gold_avg_cells")
        count = count if isinstance(count, int) and count >= 0 else None
        cells = cells if isinstance(cells, int) and cells >= 0 else None
        avg = float(avg) if isinstance(avg, (int, float)) and avg > 0 else None

        if count is not None and cells is not None and avg is None and count > 0:
            avg = cells / count
        elif count is not None and avg is not None and cells is None:
            cells = int(round(count * avg))
        elif cells is not None and avg is not None and count is None:
            count = int(round(cells / avg))

        return {
            "gold_count": count,
            "gold_total_cells": cells,
            "gold_avg_cells": avg,
        }

    def _gold_target_constraints(
        self,
        known_count: int,
        known_cells: int,
        optional_cells: List[int],
    ) -> Optional[List[Tuple[Optional[int], Optional[int]]]]:
        explicit_count = self._gold_count_target()
        explicit_cells = self._gold_cells_target()
        avg = self._gold_avg_target()
        if explicit_count is None and explicit_cells is None and avg is None:
            return None

        min_count = known_count
        max_count = known_count + len(optional_cells)
        min_cells = known_cells
        max_cells = known_cells + sum(optional_cells)

        def _avg_matches(count: int, cells: int) -> bool:
            if avg is None:
                return True
            if count <= 0:
                return False
            return abs((cells / count) - avg) <= 0.02

        constraints: List[Tuple[Optional[int], Optional[int]]] = []
        if explicit_count is not None and explicit_cells is not None:
            if (
                min_count <= explicit_count <= max_count
                and min_cells <= explicit_cells <= max_cells
                and _avg_matches(explicit_count, explicit_cells)
            ):
                constraints.append((explicit_count, explicit_cells))
            return constraints

        if explicit_count is not None:
            if not (min_count <= explicit_count <= max_count):
                return []
            if avg is None:
                constraints.append((explicit_count, None))
            else:
                for cells in range(min_cells, max_cells + 1):
                    if _avg_matches(explicit_count, cells):
                        constraints.append((explicit_count, cells))
            return constraints

        if explicit_cells is not None:
            if not (min_cells <= explicit_cells <= max_cells):
                return []
            if avg is None:
                constraints.append((None, explicit_cells))
            else:
                for count in range(min_count, max_count + 1):
                    if _avg_matches(count, explicit_cells):
                        constraints.append((count, explicit_cells))
            return constraints

        for count in range(min_count, max_count + 1):
            for cells in range(min_cells, max_cells + 1):
                if _avg_matches(count, cells):
                    constraints.append((count, cells))
        return constraints

    def _clear_autofill_phantoms(self) -> None:
        for uid in [uid for uid in self._phantom_items if uid.startswith("autofill_")]:
            self._phantom_items.pop(uid, None)
            self._manual_shapes.pop(uid, None)

    def _clear_autofill_view(self) -> None:
        self._clear_autofill_phantoms()
        self._autofill_solutions = []
        self._autofill_next_id = 1
        self._secondary_fill_next_id = 1
        self._refresh_autofill_view_with_estimate()

    def _empty_zone_cells(self) -> Set[Tuple[int, int]]:
        max_box_id = self._empty_zone_max_box_id()
        if self.state.current_round < MIN_ROUND_SHOW_EMPTY or max_box_id < 0:
            return set()
        occupied = self._build_occupied()
        cells = set()
        for bid in range(min(max_box_id, GRID_COLS * GRID_ROWS - 1) + 1):
            row = bid // GRID_COLS
            col = bid % GRID_COLS
            if (row, col) not in occupied:
                cells.add((row, col))
        return cells

    def _high_quality_shape_priors(self) -> Dict[Tuple[int, int], Dict[str, float]]:
        candidates = [item for item in self.csv_items if item.quality in (5, 6)]
        probs = candidate_probabilities(
            candidates,
            self._map_category_weights,
            self.state.map_id,
        )
        priors: Dict[Tuple[int, int], Dict[str, float]] = {}
        for item in candidates:
            w, h = self._shape_wh(item.shape)
            if w <= 0 or h <= 0 or w > GRID_COLS or h > GRID_ROWS:
                continue
            entry = priors.setdefault((w, h), {"total": 0.0, "q5": 0.0, "q6": 0.0})
            weight = probs.get(item.item_id, 0.0) or 1.0e-9
            entry["total"] += weight
            if item.quality == 5:
                entry["q5"] += weight
            else:
                entry["q6"] += weight
        if not priors:
            priors[(1, 1)] = {"total": 1.0, "q5": 0.5, "q6": 0.5}
        return priors

    def _fit_rectangles_for_cells(
        self,
        cells: Set[Tuple[int, int]],
        priors: Dict[Tuple[int, int], Dict[str, float]],
        choice_id: int = 1,
    ) -> List[Tuple[int, int, int, int]]:
        solutions = self._autofill_rectangles_for_cells(cells, priors)
        if not solutions:
            return []
        idx = max(1, choice_id) - 1
        idx = min(idx, len(solutions) - 1)
        return solutions[idx]

    def _autofill_rectangles_for_cells(
        self,
        cells: Set[Tuple[int, int]],
        priors: Dict[Tuple[int, int], Dict[str, float]],
        max_solutions: int = 12,
    ) -> List[List[Tuple[int, int, int, int]]]:
        if not cells:
            return []
        def _shape_sort_key(wh: Tuple[int, int]) -> Tuple[int, int, int, float, float]:
            w, h = wh
            prior = priors[wh]
            return (
                w * h,
                max(w, h),
                min(w, h),
                prior["total"],
                prior["q5"] + prior["q6"],
            )

        shapes = sorted(priors, key=_shape_sort_key, reverse=True)
        deadline = time.monotonic() + 0.35
        candidates: List[List[Tuple[int, int, int, int]]] = []
        seen_solutions: Set[Tuple[Tuple[int, int, int, int], ...]] = set()

        def _fits(row: int, col: int, w: int, h: int, remaining: Set[Tuple[int, int]]) -> bool:
            return all((row + dr, col + dc) in remaining for dr in range(h) for dc in range(w))

        def _solution_key(rects: List[Tuple[int, int, int, int]]) -> Tuple[Tuple[int, int, int, int], ...]:
            return tuple(sorted(rects))

        def _add_solution(rects: List[Tuple[int, int, int, int]]) -> None:
            key = _solution_key(rects)
            if key in seen_solutions:
                return
            seen_solutions.add(key)
            candidates.append(list(key))

        def _solution_score(rects: List[Tuple[int, int, int, int]]) -> tuple:
            prior_score = 0.0
            block_profile = []
            for _row, _col, w, h in rects:
                prior_score += priors.get((w, h), {}).get("total", 0.0)
                block_profile.append((w * h, max(w, h), min(w, h)))
            block_profile.sort(reverse=True)
            return (-len(rects), tuple(block_profile), prior_score)

        def _scan_cells(mode: str) -> List[Tuple[int, int]]:
            ordered = list(cells)
            if mode == "row_br":
                return sorted(ordered, reverse=True)
            if mode == "col_tl":
                return sorted(ordered, key=lambda rc: (rc[1], rc[0]))
            if mode == "col_br":
                return sorted(ordered, key=lambda rc: (rc[1], rc[0]), reverse=True)
            if mode == "center":
                cr = (min(r for r, _c in ordered) + max(r for r, _c in ordered)) / 2
                cc = (min(c for _r, c in ordered) + max(c for _r, c in ordered)) / 2
                return sorted(ordered, key=lambda rc: (abs(rc[0] - cr) + abs(rc[1] - cc), rc[0], rc[1]))
            if mode == "edge":
                cr = (min(r for r, _c in ordered) + max(r for r, _c in ordered)) / 2
                cc = (min(c for _r, c in ordered) + max(c for _r, c in ordered)) / 2
                return sorted(ordered, key=lambda rc: (abs(rc[0] - cr) + abs(rc[1] - cc), rc[0], rc[1]), reverse=True)
            return sorted(ordered)

        def _shape_orders() -> List[List[Tuple[int, int]]]:
            orders: List[List[Tuple[int, int]]] = [
                shapes,
                sorted(shapes, key=lambda wh: (wh[0] * wh[1], wh[0], wh[1], priors[wh]["total"]), reverse=True),
                sorted(shapes, key=lambda wh: (wh[0] * wh[1], wh[1], wh[0], priors[wh]["total"]), reverse=True),
                sorted(shapes, key=lambda wh: (wh[0] * wh[1], priors[wh]["q5"], priors[wh]["total"]), reverse=True),
                sorted(shapes, key=lambda wh: (wh[0] * wh[1], priors[wh]["q6"], priors[wh]["total"]), reverse=True),
            ]
            deduped: List[List[Tuple[int, int]]] = []
            seen_orders: Set[Tuple[Tuple[int, int], ...]] = set()
            for order in orders:
                key = tuple(order)
                if key in seen_orders:
                    continue
                seen_orders.add(key)
                deduped.append(order)
            return deduped

        def _placements_for_anchor(
            anchor: Tuple[int, int],
            shape_order: List[Tuple[int, int]],
            remaining: Set[Tuple[int, int]],
            placement_mode: str,
        ) -> List[Tuple[int, int, int, int]]:
            anchor_row, anchor_col = anchor
            options = []
            for w, h in shape_order:
                for row in range(anchor_row - h + 1, anchor_row + 1):
                    for col in range(anchor_col - w + 1, anchor_col + 1):
                        if row < 0 or col < 0:
                            continue
                        if _fits(row, col, w, h, remaining):
                            options.append((row, col, w, h))

            def _placement_key(rect: Tuple[int, int, int, int]) -> tuple:
                row, col, w, h = rect
                shape_priority = (
                    w * h,
                    max(w, h),
                    min(w, h),
                    priors[(w, h)]["total"],
                    priors[(w, h)]["q5"] + priors[(w, h)]["q6"],
                )
                if placement_mode == "br":
                    placement = (row, col)
                elif placement_mode == "center":
                    placement = (-abs((row + (h - 1) / 2) - anchor_row), -abs((col + (w - 1) / 2) - anchor_col))
                else:
                    placement = (-row, -col)
                return shape_priority + placement

            return sorted(options, key=_placement_key, reverse=True)

        def _greedy_solution(
            scan_order: List[Tuple[int, int]],
            shape_order: List[Tuple[int, int]],
            placement_mode: str,
        ) -> List[Tuple[int, int, int, int]]:
            remaining = set(cells)
            rects: List[Tuple[int, int, int, int]] = []
            while remaining and time.monotonic() <= deadline:
                anchor = next((cell for cell in scan_order if cell in remaining), None)
                if anchor is None:
                    anchor = min(remaining)
                options = _placements_for_anchor(
                    anchor,
                    shape_order,
                    remaining,
                    placement_mode,
                )
                if options:
                    row, col, w, h = options[0]
                else:
                    row, col = anchor
                    w, h = 1, 1
                rects.append((row, col, w, h))
                rect_cells = {
                    (row + dr, col + dc)
                    for dr in range(h)
                    for dc in range(w)
                }
                remaining -= rect_cells
            return sorted(rects) if not remaining else []

        scan_modes = ("row_tl", "row_br", "col_tl", "col_br", "center", "edge")
        placement_modes = ("tl", "br", "center")
        for scan_mode in scan_modes:
            scan_order = _scan_cells(scan_mode)
            for shape_order in _shape_orders():
                for placement_mode in placement_modes:
                    if time.monotonic() > deadline:
                        break
                    solution = _greedy_solution(
                        scan_order,
                        shape_order,
                        placement_mode,
                    )
                    if solution:
                        _add_solution(solution)

        if not candidates:
            self._autofill_last_count = 0
            return []

        ranked = sorted(candidates, key=_solution_score, reverse=True)
        selected = [ranked.pop(0)]

        def _distance(a: List[Tuple[int, int, int, int]], b: List[Tuple[int, int, int, int]]) -> int:
            aset = set(a)
            bset = set(b)
            return len(aset.symmetric_difference(bset))

        while ranked and len(selected) < max_solutions:
            best_idx = max(
                range(len(ranked)),
                key=lambda idx: (
                    min(_distance(ranked[idx], chosen) for chosen in selected),
                    _solution_score(ranked[idx]),
                ),
            )
            selected.append(ranked.pop(best_idx))

        self._autofill_last_count = len(selected)
        return selected

    def _known_gold_stats(self) -> Tuple[int, int]:
        count = 0
        cells = 0
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(
            (uid, k)
            for uid, k in self._phantom_items.items()
            if not uid.startswith("autofill_")
        )
        for uid, k in item_rows:
            if k.box_id is None:
                continue
            if self._display_quality(uid, k) == 5:
                w, h = self._effective_shape_wh(uid, k)
                count += 1
                cells += w * h
        return count, cells

    def _assign_autofill_manual_qualities(
        self,
        uids: List[str],
        priors: Dict[Tuple[int, int], Dict[str, float]],
    ) -> None:
        known_count, known_cells = self._known_gold_stats()
        optional_cells = []
        for uid in uids:
            w, h, _col, _row = self._manual_shapes[uid]
            optional_cells.append(w * h)
        constraints = self._gold_target_constraints(
            known_count,
            known_cells,
            optional_cells,
        )
        if constraints is None:
            return
        if not constraints:
            for uid in uids:
                self._phantom_items[uid].manual_quality = 6
            return

        states: Dict[Tuple[int, int], Tuple[float, int]] = {
            (known_count, known_cells): (0.0, 0)
        }
        for idx, uid in enumerate(uids):
            w, h, _col, _row = self._manual_shapes[uid]
            prior = priors.get((w, h), {"q5": 0.5, "q6": 0.5})
            area = w * h
            next_states: Dict[Tuple[int, int], Tuple[float, int]] = {}
            for (q5_count, q5_cells), (score, mask) in states.items():
                q6_key = (q5_count, q5_cells)
                q6_score = score + prior.get("q6", 0.0)
                if q6_score > next_states.get(q6_key, (float("-inf"), 0))[0]:
                    next_states[q6_key] = (q6_score, mask)

                q5_key = (q5_count + 1, q5_cells + area)
                q5_score = score + prior.get("q5", 0.0)
                if q5_score > next_states.get(q5_key, (float("-inf"), 0))[0]:
                    next_states[q5_key] = (q5_score, mask | (1 << idx))
            states = next_states

        best_mask = None
        best_score = float("-inf")
        for (q5_count, q5_cells), (score, mask) in states.items():
            if any(
                (count is None or q5_count == count)
                and (cells is None or q5_cells == cells)
                for count, cells in constraints
            ) and score > best_score:
                best_score = score
                best_mask = mask
        if best_mask is None:
            return
        for idx, uid in enumerate(uids):
            self._phantom_items[uid].manual_quality = 5 if best_mask & (1 << idx) else 6

    def _create_autofill_phantoms(
        self,
        rects: List[Tuple[int, int, int, int]],
        priors: Dict[Tuple[int, int], Dict[str, float]],
    ) -> List[str]:
        created: List[str] = []
        for row, col, w, h in rects:
            uid = f"autofill_{self._phantom_counter}"
            self._phantom_counter += 1
            pk = ItemKnowledge(uid=uid)
            pk.box_id = row * GRID_COLS + col
            pk.box_id_confirmed = True
            self._phantom_items[uid] = pk
            self._manual_shapes[uid] = (w, h, col, row)
            created.append(uid)
        self._apply_scan_history_to_phantoms()
        self._assign_autofill_manual_qualities(created, priors)
        return created

    def _current_autofill_rects(self) -> List[Tuple[int, int, int, int]]:
        rects: List[Tuple[int, int, int, int]] = []
        for uid in sorted(self._phantom_items):
            if not uid.startswith("autofill_") or uid not in self._manual_shapes:
                continue
            w, h, col, row = self._manual_shapes[uid]
            rects.append((row, col, w, h))
        return sorted(rects)

    @staticmethod
    def _autofill_solution_key(
        rects: List[Tuple[int, int, int, int]]
    ) -> Tuple[Tuple[int, int, int, int], ...]:
        return tuple(sorted(rects))

    def _autofill_rects_match_gold_constraints(
        self,
        rects: List[Tuple[int, int, int, int]],
    ) -> bool:
        known_count, known_cells = self._known_gold_stats()
        optional_cells = [w * h for _row, _col, w, h in rects]
        constraints = self._gold_target_constraints(
            known_count,
            known_cells,
            optional_cells,
        )
        if constraints is None:
            return True
        if not constraints:
            return False

        possible: Set[Tuple[int, int]] = {(known_count, known_cells)}
        for cells in optional_cells:
            possible |= {
                (count + 1, total_cells + cells)
                for count, total_cells in possible
            }
        return any(
            (target_count is None or count == target_count)
            and (target_cells is None or total_cells == target_cells)
            for count, total_cells in possible
            for target_count, target_cells in constraints
        )

    def _rank_secondary_autofill_solutions(
        self,
        rect_solutions: List[List[Tuple[int, int, int, int]]],
        current_rects: List[Tuple[int, int, int, int]],
        priors: Dict[Tuple[int, int], Dict[str, float]],
    ) -> List[List[Tuple[int, int, int, int]]]:
        current_key = self._autofill_solution_key(current_rects)
        current_count = len(current_rects)
        current_cells = sum(w * h for _row, _col, w, h in current_rects)
        max_count = min(current_cells, max(current_count + 6, current_count * 2))
        seen: Set[Tuple[Tuple[int, int, int, int], ...]] = set()

        selected: List[List[Tuple[int, int, int, int]]] = []
        for rects in rect_solutions:
            if len(rects) > max_count:
                continue
            key = self._autofill_solution_key(rects)
            if key == current_key or key in seen:
                continue
            if not self._autofill_rects_match_gold_constraints(rects):
                continue
            seen.add(key)
            selected.append(rects)

        current_set = set(current_key)

        def _score(rects: List[Tuple[int, int, int, int]]) -> tuple:
            rect_set = set(rects)
            distance = len(current_set.symmetric_difference(rect_set))
            area_profile = sorted((w * h, max(w, h), min(w, h)) for _r, _c, w, h in rects)
            prior_score = sum(priors.get((w, h), {}).get("total", 0.0) for _r, _c, w, h in rects)
            same_count_bonus = 1 if len(rects) == current_count else 0
            return (same_count_bonus, -len(rects), distance, tuple(reversed(area_profile)), prior_score)

        return sorted(selected, key=_score, reverse=True)

    def _try_secondary_autofill_high_quality(self) -> None:
        current_rects = self._current_autofill_rects()
        if not current_rects:
            self._refresh_autofill_view_without_estimate()
            return

        cells: Set[Tuple[int, int]] = set()
        for row, col, w, h in current_rects:
            cells.update(self._rect_cells(row, col, w, h))
        if not cells:
            self._refresh_autofill_view_without_estimate()
            return

        priors = self._high_quality_shape_priors()
        rect_solutions = self._autofill_rectangles_for_cells(
            cells,
            priors,
            max_solutions=48,
        )
        selected = self._rank_secondary_autofill_solutions(
            rect_solutions,
            current_rects,
            priors,
        )
        if not selected:
            self._refresh_autofill_view_with_estimate()
            return

        priors = self._high_quality_shape_priors()
        self._clear_autofill_phantoms()
        self._create_autofill_phantoms(selected[0], priors)
        self._refresh_autofill_view_with_estimate()

    def _refresh_autofill_view_without_estimate(self) -> None:
        self._info_text.set(self._info_summary_text())
        self._draw(update_total=False)

    def _refresh_autofill_view_with_estimate(self) -> None:
        self._refresh_summary_bars()
        self._draw(update_total=False)

    def _apply_autofill_solution(self, solution_id: int) -> None:
        solution = next(
            (s for s in self._autofill_solutions if s["id"] == solution_id),
            None,
        )
        if solution is None:
            return
        self._clear_autofill_phantoms()
        priors = self._high_quality_shape_priors()
        self._create_autofill_phantoms(solution["rects"], priors)
        self._refresh_autofill_view_with_estimate()

    def _try_autofill_hidden_high_quality(self) -> None:
        self._clear_autofill_phantoms()
        self._secondary_fill_next_id = 1
        cells = self._empty_zone_cells()
        if not cells:
            self._autofill_solutions = []
            self._autofill_next_id = 1
            self._refresh_autofill_view_without_estimate()
            return
        priors = self._high_quality_shape_priors()
        rect_solutions = self._autofill_rectangles_for_cells(cells, priors)
        self._autofill_solutions = [
            {"id": idx, "rects": rects}
            for idx, rects in enumerate(rect_solutions, start=1)
        ]
        if not self._autofill_solutions:
            self._refresh_autofill_view_without_estimate()
            return
        if self._autofill_next_id > len(self._autofill_solutions):
            self._autofill_next_id = 1
        apply_id = self._autofill_next_id
        self._autofill_next_id += 1
        if self._autofill_next_id > len(self._autofill_solutions):
            self._autofill_next_id = 1
        self._apply_autofill_solution(apply_id)

    def _posterior_distribution_for_item(
        self,
        uid: str,
        k: ItemKnowledge,
    ) -> List[WeightedValue]:
        if k.price is not None and k.item_cid:
            item = self.csv_index.get(k.item_cid)
            quality = item.quality if item is not None else k.quality
            cells = self._weighted_value_cells(uid, k, item)
            return [WeightedValue(float(k.price), 1.0, quality, cells)]

        manual_item = self._valid_manual_confirm_item(uid, k)
        if manual_item is not None:
            return [
                WeightedValue(
                    float(manual_item.base_value),
                    1.0,
                    manual_item.quality,
                    self._csv_item_cells(manual_item),
                )
            ]

        candidates = self._candidate_items_for_grid(uid, k)
        if not candidates:
            return []

        if len(candidates) == 1:
            return [
                WeightedValue(
                    float(candidates[0].base_value),
                    1.0,
                    candidates[0].quality,
                    self._csv_item_cells(candidates[0]),
                )
            ]

        probs = candidate_probabilities(
            candidates,
            self._map_category_weights,
            self.state.map_id,
        )
        observed_price = float(k.price) if k.price is not None else None
        return [
            WeightedValue(
                float(item.base_value),
                probs.get(item.item_id, 0.0)
                * price_likelihood(float(item.base_value), observed_price),
                item.quality,
                self._csv_item_cells(item),
            )
            for item in candidates
        ]

    def _csv_item_cells(self, item: CsvItem) -> int:
        w, h = self._shape_wh(item.shape)
        return w * h

    def _weighted_value_cells(
        self,
        uid: str,
        k: ItemKnowledge,
        item: Optional[CsvItem],
    ) -> int:
        if item is not None:
            return self._csv_item_cells(item)
        w, h = self._effective_shape_wh(uid, k)
        return w * h

    def _zero_estimate_reason(
        self,
        item_rows: List[Tuple[str, ItemKnowledge]],
        distributions: List[List[WeightedValue]],
        constraints: Optional[List[Tuple[Optional[int], Optional[int]]]],
    ) -> str:
        if not item_rows:
            return "没有可估物品，请先解析或标记"
        if constraints == []:
            return "金约束不匹配，请检查输入"
        if not any(distributions):
            return "候选为空，请放宽筛选"
        estimate_last = getattr(self, "_estimate_last", None)
        if isinstance(estimate_last, dict):
            estimate_item_count = estimate_last.get("item_count")
        else:
            estimate_item_count = getattr(estimate_last, "item_count", None)
        if (
            any(value is not None for value in self._resolved_gold_input_values().values())
            and estimate_last is not None
            and estimate_item_count == 0
        ):
            return "金约束不匹配，请检查输入"
        map_id = normalize_map_id(self.state.map_id)
        if map_id not in MAP_TO_TIER_NEST:
            return "地图未适配，请补充数据"
        if self._compute_empty_zone_count():
            return "仍有空格，请尝试填充"
        return "候选为空，请放宽筛选"

    def _estimate_display_text(self, estimate: float) -> str:
        if estimate > 0:
            return f"¥{estimate:,.0f}"
        reason = getattr(self, "_estimate_zero_reason", "") or "候选为空，请放宽筛选"
        return reason

    def _selected_estimate_method(self) -> str:
        var = getattr(self, "_estimate_method_var", None)
        value = var.get() if var is not None else ""
        if value in self.ESTIMATE_METHODS:
            return value
        return self.ESTIMATE_METHOD_EXPECTED

    def _calc_selected_estimate_price(self) -> float:
        method = self._selected_estimate_method()
        if method == self.ESTIMATE_METHOD_VARIANCE_ADJUSTED:
            return self._calc_grid_variance_adjusted_estimate_price()
        if method == self.ESTIMATE_METHOD_TRIMMED_MEAN:
            return self._calc_grid_trimmed_mean_estimate_price()
        if method == self.ESTIMATE_METHOD_85_PERCENT:
            return self._calc_grid_85_percent_estimate_price()
        if method == self.ESTIMATE_METHOD_EXPECTED:
            return self._calc_grid_total_estimate_price()
        return self._calc_grid_total_estimate_price()

    @staticmethod
    def _distribution_expected_value(dist: List[WeightedValue]) -> Optional[float]:
        total_weight = sum(max(0.0, value.weight) for value in dist)
        if total_weight <= 0:
            return None
        return (
            sum(value.value * max(0.0, value.weight) for value in dist)
            / total_weight
        )

    @staticmethod
    def _distribution_mean_variance(
        dist: List[WeightedValue],
    ) -> Optional[Tuple[float, float]]:
        total_weight = sum(max(0.0, value.weight) for value in dist)
        if total_weight <= 0:
            return None
        mean = (
            sum(value.value * max(0.0, value.weight) for value in dist)
            / total_weight
        )
        variance = (
            sum(
                ((value.value - mean) ** 2) * max(0.0, value.weight)
                for value in dist
            )
            / total_weight
        )
        return mean, variance

    @staticmethod
    def _distribution_upper_trimmed_expected_value(
        dist: List[WeightedValue],
        trim_ratio: float = 0.05,
    ) -> Optional[float]:
        weighted = [
            (value.value, max(0.0, value.weight))
            for value in dist
            if value.weight > 0
        ]
        total_weight = sum(weight for _value, weight in weighted)
        if total_weight <= 0:
            return None
        keep_weight = total_weight * max(0.0, min(1.0, 1.0 - trim_ratio))
        if keep_weight <= 0:
            return None

        total = 0.0
        used = 0.0
        for value, weight in sorted(weighted, key=lambda pair: pair[0]):
            if used >= keep_weight:
                break
            take = min(weight, keep_weight - used)
            total += value * take
            used += take
        if used <= 0:
            return None
        return total / used

    def _grid_value_distributions(
        self,
    ) -> Tuple[
        List[Tuple[str, ItemKnowledge]],
        List[List[WeightedValue]],
        Optional[List[Tuple[Optional[int], Optional[int]]]],
    ]:
        item_rows: List[Tuple[str, ItemKnowledge]] = list(self.state.items.items())
        item_rows.extend(self._phantom_items.items())
        distributions = [
            self._posterior_distribution_for_item(uid, k)
            for uid, k in item_rows
        ]
        constraints = self._gold_target_constraints(
            known_count=0,
            known_cells=0,
            optional_cells=[
                max(
                    (value.cells or 0)
                    for value in dist
                    if value.quality == 5
                )
                for dist in distributions
                if any(value.quality == 5 for value in dist)
            ],
        )
        return item_rows, distributions, constraints

    def _calc_grid_total_estimate_price(self) -> float:
        item_rows, distributions, constraints = self._grid_value_distributions()

        total = 0.0
        estimated_count = 0
        for dist in distributions:
            expected = self._distribution_expected_value(dist)
            if expected is None:
                continue
            total += expected
            estimated_count += 1

        self._estimate_last = {
            "estimate": total,
            "item_count": estimated_count,
            "unresolved_count": len(distributions) - estimated_count,
            "method": "expected_value",
        }
        self._estimate_zero_reason = (
            self._zero_estimate_reason(item_rows, distributions, constraints)
            if total <= 0 or constraints == []
            else ""
        )
        if constraints == []:
            return 0.0
        return total

    def _calc_grid_variance_adjusted_estimate_price(self) -> float:
        item_rows, distributions, constraints = self._grid_value_distributions()

        total_mean = 0.0
        total_variance = 0.0
        estimated_count = 0
        for dist in distributions:
            stats = self._distribution_mean_variance(dist)
            if stats is None:
                continue
            mean, variance = stats
            total_mean += mean
            total_variance += variance
            estimated_count += 1

        std = math.sqrt(max(0.0, total_variance))
        penalty = min(total_mean * 0.30, std * 0.25)
        estimate = max(0.0, total_mean - penalty)
        self._estimate_last = {
            "estimate": estimate,
            "raw_estimate": total_mean,
            "std": std,
            "penalty": penalty,
            "item_count": estimated_count,
            "unresolved_count": len(distributions) - estimated_count,
            "method": "variance_adjusted_expected_value",
        }
        self._estimate_zero_reason = (
            self._zero_estimate_reason(item_rows, distributions, constraints)
            if estimate <= 0 or constraints == []
            else ""
        )
        if constraints == []:
            return 0.0
        return estimate

    def _calc_grid_trimmed_mean_estimate_price(self) -> float:
        item_rows, distributions, constraints = self._grid_value_distributions()

        total = 0.0
        estimated_count = 0
        for dist in distributions:
            expected = self._distribution_upper_trimmed_expected_value(
                dist,
                trim_ratio=0.05,
            )
            if expected is None:
                continue
            total += expected
            estimated_count += 1

        self._estimate_last = {
            "estimate": total,
            "item_count": estimated_count,
            "unresolved_count": len(distributions) - estimated_count,
            "method": "upper_trimmed_expected_value",
            "trim_ratio": 0.05,
        }
        self._estimate_zero_reason = (
            self._zero_estimate_reason(item_rows, distributions, constraints)
            if total <= 0 or constraints == []
            else ""
        )
        if constraints == []:
            return 0.0
        return total

    def _calc_grid_85_percent_estimate_price(self) -> float:
        item_rows, distributions, constraints = self._grid_value_distributions()

        raw_total = 0.0
        estimated_count = 0
        for dist in distributions:
            expected = self._distribution_expected_value(dist)
            if expected is None:
                continue
            raw_total += expected
            estimated_count += 1

        total = raw_total * 0.85
        self._estimate_last = {
            "estimate": total,
            "raw_estimate": raw_total,
            "item_count": estimated_count,
            "unresolved_count": len(distributions) - estimated_count,
            "method": "expected_value_85_percent",
        }
        self._estimate_zero_reason = (
            self._zero_estimate_reason(item_rows, distributions, constraints)
            if total <= 0 or constraints == []
            else ""
        )
        if constraints == []:
            return 0.0
        return total

    def _calc_grid_total_price(self) -> float:
        return self._calc_grid_total_estimate_price()

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
        if self.state.current_round < MIN_ROUND_SHOW_EMPTY:
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
        gold_red_cell_set: Set[Tuple[int, int]] = set()

        for uid, k in item_rows:
            if k.box_id is None:
                continue
            col, row = self._effective_display_origin(uid, k)
            w, h = self._effective_shape_wh(uid, k)
            cells = w * h
            total_cells += cells
            item_count += 1
            quality = self._display_quality(uid, k)
            if quality in quality_stats:
                quality_stats[quality]["count"] += 1
                quality_stats[quality]["cells"] += cells
                if quality in (5, 6):
                    gold_red_cell_set.update(self._rect_cells(row, col, w, h))
            elif self._is_high_quality_range(k) or self._is_gold_red_candidate_range(uid, k):
                gold_red_cell_set.update(self._rect_cells(row, col, w, h))

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
        gold_red_cells = len(gold_red_cell_set)

        def _quality_text(label: str, quality: int) -> str:
            count = quality_stats[quality]["count"]
            cells = quality_stats[quality]["cells"]
            avg = cells / count if count else 0.0
            return f"已知{label}: {count}件 {cells}格 {avg:.2f}均格"

        def _gold_quality_text() -> str:
            values = self._resolved_gold_input_values()
            has_input = any(values.get(key) is not None for key in values)
            if not has_input:
                return _quality_text("金", 5)
            count = values.get("gold_count")
            cells = values.get("gold_total_cells")
            avg = values.get("gold_avg_cells")
            count_text = str(count) if isinstance(count, int) else "-"
            cells_text = str(cells) if isinstance(cells, int) else "-"
            avg_text = f"{float(avg):.2f}" if isinstance(avg, (int, float)) else "-"
            return f"已知金: {count_text}件 {cells_text}格 {avg_text}均格"

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
            "   ".join(
                (
                    _quality_text("红", 6),
                    _gold_quality_text(),
                    f"金红总共格数: {gold_red_cells}格",
                )
            ),
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
