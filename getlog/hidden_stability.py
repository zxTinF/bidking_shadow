# -*- coding: utf-8 -*-
"""隐藏块稳定性校验。"""

from typing import Iterable, Set

from .hidden_models import Cell, HiddenStabilityResult
from .hidden_regions import rect_cells


def rect_is_free(
    row: int,
    col: int,
    width: int,
    height: int,
    occupied: Set[Cell],
    *,
    grid_cols: int,
    grid_rows: int,
) -> bool:
    """检查矩形是否在网格内且不与已占用格冲突。"""
    if row < 0 or col < 0:
        return False
    if col + width > grid_cols or row + height > grid_rows:
        return False
    return rect_cells(row, col, width, height).isdisjoint(occupied)


def evaluate_block_stability(
    *,
    row: int,
    col: int,
    width: int,
    height: int,
    occupied_without_self: Iterable[Cell],
    grid_cols: int,
    grid_rows: int,
) -> HiddenStabilityResult:
    """按“能左滑时才允许上插，然后继续左滑，直到被挡住”为规则模拟最终停位。"""
    occupied = set(occupied_without_self)
    cur_row = row
    cur_col = col
    trace: list[str] = []

    while True:
        can_left = rect_is_free(
            cur_row,
            cur_col - 1,
            width,
            height,
            occupied,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
        )
        if not can_left:
            break
        while rect_is_free(
            cur_row - 1,
            cur_col,
            width,
            height,
            occupied,
            grid_cols=grid_cols,
            grid_rows=grid_rows,
        ):
            cur_row -= 1
            trace.append("up")
        cur_col -= 1
        trace.append("left")
        continue

    if cur_row == row and cur_col == col:
        reason = "当前位置已稳定"
        stable = True
    else:
        path = " -> ".join(trace) if trace else "移动"
        reason = f"当前摆法不稳定，可继续 {path} 到 ({cur_row},{cur_col})"
        stable = False
    return HiddenStabilityResult(
        stable=stable,
        final_row=cur_row,
        final_col=cur_col,
        trace=tuple(trace),
        reason=reason,
    )
