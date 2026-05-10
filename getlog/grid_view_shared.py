# -*- coding: utf-8 -*-
"""grid_view 的共享常量与轻量 helper。

这里放不依赖 Tk 实例的内容：
- 网格/颜色常量
- 若干通用显示配置
"""

from typing import Dict

GRID_COLS = 10
GRID_ROWS = 30
VISIBLE_ROWS = 10
CELL_SIZE = 56
CELL_W = CELL_SIZE
CELL_H = CELL_SIZE
CANVAS_MAX_W = GRID_COLS * CELL_W + 1
CANVAS_MAX_H = VISIBLE_ROWS * CELL_H + 1
QUALITY_BG: Dict[int, str] = {
    1: "#7a7a8a",
    2: "#3a8a4a",
    3: "#2060c0",
    4: "#8030b0",
    5: "#c07010",
    6: "#c02020",
}
QUALITY_FG: Dict[int, str] = {k: "#ffffff" for k in range(1, 7)}
MANUAL_QUALITY_BG: Dict[int, str] = {
    5: "#f0b429",
    6: "#e34b5f",
}
MANUAL_QUALITY_FG: Dict[int, str] = {
    5: "#241600",
    6: "#ffffff",
}
UNKNOWN_BG = "#7a5c3a"
UNKNOWN_FG = "#ffffff"
EMPTY_BG = "#2a2a3a"
GRID_LINE = "#505060"
EMPTY_ZONE_COLOR = "#cc4400"
EMPTY_ZONE_STIPPLE = "gray25"
MIN_ROUND_SHOW_EMPTY = 3
RESIZE_HANDLE_W = 8
RESIZE_HANDLE_COLOR = "#ffffff"
PHANTOM_BG = "#0d3a4a"
PHANTOM_BORDER = "#00cccc"
EMPTY_CELL_VALUE = 15000
HIGH_VALUE_THRESHOLD = 100000
_CAT_SHORT: Dict[int, str] = {
    101: "家具",
    102: "医药",
    103: "时尚",
    104: "兵装",
    105: "珠宝",
    106: "文物",
    107: "数码",
    108: "能源",
    109: "食饮",
    110: "书画",
}
