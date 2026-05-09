# -*- coding: utf-8 -*-
"""GridWindow 的绘制与交互逻辑。"""

import statistics
import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Optional, Tuple

from .constants import CATEGORY_NAMES, fmt_shape
from .item_db import candidate_probabilities, probability_source_label
from .models import CsvItem, ItemKnowledge
from .grid_view_shared import (
    CELL_H,
    CELL_W,
    EMPTY_BG,
    EMPTY_ZONE_COLOR,
    EMPTY_ZONE_STIPPLE,
    GRID_COLS,
    GRID_LINE,
    GRID_ROWS,
    HIGH_VALUE_THRESHOLD,
    MANUAL_QUALITY_BG,
    MANUAL_QUALITY_FG,
    MIN_ROUND_SHOW_EMPTY,
    PHANTOM_BG,
    PHANTOM_BORDER,
    QUALITY_BG,
    QUALITY_FG,
    RESIZE_HANDLE_COLOR,
    RESIZE_HANDLE_W,
    UNKNOWN_BG,
    UNKNOWN_FG,
    _CAT_SHORT,
)


class GridWindowInteractionMixin:
    """负责画布绘制、鼠标交互和候选弹窗。"""

    def _draw(self, update_total: bool = True) -> None:
        """重绘整个网格画布。"""
        canvas = self.canvas
        canvas.delete("all")
        # 当前帧共享占位缓存，避免同一轮重绘里反复全表扫描。
        self._occupied_for_draw = self._build_occupied()
        for row in range(self.vis_rows):
            for col in range(GRID_COLS):
                x1, y1 = (col * CELL_W, row * CELL_H)
                x2, y2 = (x1 + CELL_W, y1 + CELL_H)
                canvas.create_rectangle(
                    x1, y1, x2, y2, fill=EMPTY_BG, outline=GRID_LINE, width=1
                )
                bid = row * GRID_COLS + col
                canvas.create_text(
                    x1 + 4,
                    y1 + 3,
                    text=str(bid),
                    anchor="nw",
                    fill="#404050",
                    font=("Consolas", 7),
                )
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
                            x1,
                            y1,
                            x1 + CELL_W,
                            y1 + CELL_H,
                            fill=EMPTY_ZONE_COLOR,
                            stipple=EMPTY_ZONE_STIPPLE,
                            outline="",
                        )
        for uid, k in self.state.items.items():
            if k.box_id is None:
                continue
            self._draw_item(uid, k)
        for phid, pk in self._phantom_items.items():
            if phid in self._manual_shapes:
                self._draw_item(phid, pk)
        if self._phantom_draw_state:
            pds = self._phantom_draw_state
            sr, sc = (pds["start_row"], pds["start_col"])
            cr, cc = (pds["cur_row"], pds["cur_col"])
            min_r, max_r = (min(sr, cr), max(sr, cr))
            min_c, max_c = (min(sc, cc), max(sc, cc))
            preview_w = max_c - min_c + 1
            preview_h = max_r - min_r + 1
            preview_invalid = self._rect_overlaps_occupied(
                min_r, min_c, preview_w, preview_h
            )
            preview_color = "#cc4444" if preview_invalid else PHANTOM_BORDER
            px1 = min_c * CELL_W + 1
            py1 = min_r * CELL_H + 1
            px2 = (max_c + 1) * CELL_W - 1
            py2 = (max_r + 1) * CELL_H - 1
            canvas.create_rectangle(
                px1, py1, px2, py2, fill="", outline=preview_color, width=2, dash=(6, 3)
            )
            canvas.create_text(
                (px1 + px2) / 2,
                (py1 + py2) / 2,
                text=f"{preview_w}x{preview_h}" + (" 重叠" if preview_invalid else ""),
                fill=preview_color,
                font=("微软雅黑", 10, "bold"),
            )
        if update_total and hasattr(self, "_total_label"):
            self._update_total_label()
        self._occupied_for_draw = None

    def _draw_item(self, uid: str, k: ItemKnowledge) -> None:
        """绘制单个物品或手动画出的幽灵框。"""
        canvas = self.canvas
        col, row = self._effective_display_origin(uid, k)
        w, h = self._effective_shape_wh(uid, k)
        if row >= self.vis_rows or col + w > GRID_COLS:
            return
        x1 = col * CELL_W + 2
        y1 = row * CELL_H + 2
        x2 = (col + w) * CELL_W - 2
        y2 = (row + h) * CELL_H - 2
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        is_phantom = uid in self._phantom_items
        q = self._display_quality(uid, k) or 0
        if k.manual_quality in MANUAL_QUALITY_BG:
            bg = MANUAL_QUALITY_BG[k.manual_quality]
            fg = MANUAL_QUALITY_FG.get(k.manual_quality, UNKNOWN_FG)
        else:
            bg = PHANTOM_BG if is_phantom else QUALITY_BG.get(q, UNKNOWN_BG)
            fg = QUALITY_FG.get(q, UNKNOWN_FG)
        tag = f"item_{uid}"
        price_value = self._display_price_value(uid, k)
        is_high_value = price_value is not None and price_value >= HIGH_VALUE_THRESHOLD
        if k.manual_quality in MANUAL_QUALITY_BG:
            border_color = MANUAL_QUALITY_BG[k.manual_quality]
            border_width = 3
        elif is_phantom:
            border_color = PHANTOM_BORDER
            border_width = 2
        elif is_high_value:
            border_color = "#ffd34d"
            border_width = 3
        elif uid in self._manual_shapes:
            border_color = "#ffdd00"
            border_width = 2
        else:
            border_color = "#ffffff"
            border_width = 1
        canvas.create_rectangle(
            x1 - border_width,
            y1 - border_width,
            x2 + border_width,
            y2 + border_width,
            fill=border_color,
            outline="",
            tags=(tag,),
        )
        canvas.create_rectangle(x1, y1, x2, y2, fill=bg, outline="", tags=(tag,))
        lines = self._item_text_lines(uid, k)
        text = "\n".join(lines)
        canvas.create_text(
            cx,
            cy,
            text=text,
            fill=fg,
            font=("微软雅黑", 8),
            justify="center",
            anchor="center",
            tags=(tag,),
        )
        if is_high_value:
            canvas.create_rectangle(
                x2 - 31, y1, x2, y1 + 14, fill="#ffd34d", outline="", tags=(tag,)
            )
            canvas.create_text(
                x2 - 15,
                y1 + 7,
                text="10万+",
                fill="#3a2600",
                font=("微软雅黑", 7, "bold"),
                tags=(tag,),
            )
        if k.shape is None:
            hw = RESIZE_HANDLE_W
            hc = RESIZE_HANDLE_COLOR
            pad = 4
            stipple = "gray50"
            canvas.create_rectangle(
                x2 - hw,
                y1 + pad,
                x2,
                y2 - pad,
                fill=hc,
                outline="",
                stipple=stipple,
                tags=(tag,),
            )
            canvas.create_rectangle(
                x1,
                y1 + pad,
                x1 + hw,
                y2 - pad,
                fill=hc,
                outline="",
                stipple=stipple,
                tags=(tag,),
            )
            canvas.create_rectangle(
                x1 + pad,
                y2 - hw,
                x2 - pad,
                y2,
                fill=hc,
                outline="",
                stipple=stipple,
                tags=(tag,),
            )
            canvas.create_rectangle(
                x1 + pad,
                y1,
                x2 - pad,
                y1 + hw,
                fill=hc,
                outline="",
                stipple=stipple,
                tags=(tag,),
            )
            for cx2, cy2 in [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]:
                canvas.create_rectangle(
                    cx2 - hw // 2,
                    cy2 - hw // 2,
                    cx2 + hw // 2,
                    cy2 + hw // 2,
                    fill=hc,
                    outline="",
                    tags=(tag,),
                )

    def _item_text_lines(self, uid: str, k: ItemKnowledge) -> List[str]:
        """生成格子内显示的 2~3 行文本。"""
        lines: List[str] = []
        type_text = ""
        if uid in self._phantom_items:
            type_text = "手动"
        elif k.categories:
            type_text = "/".join(
                (_CAT_SHORT.get(c, str(c)) for c in sorted(k.categories))
            )
        if k.shape:
            shape_text = fmt_shape(k.shape)
        elif uid in self._manual_shapes:
            mw, mh, _mdc, _mdr = self._manual_shapes[uid]
            shape_text = f"{mw}x{mh}*"
        else:
            shape_text = "?x?"
        header_text = f"{type_text} {shape_text}".strip()
        if header_text:
            lines.append(header_text)
        best, count, unique, est, _label = self._query_item_for_grid(uid, k)

        def _short(name: str, max_len: int = 5) -> str:
            return name[:max_len] + "…" if len(name) > max_len else name

        if k.price is not None and k.item_cid:
            name = (
                self.csv_index[k.item_cid].name
                if k.item_cid in self.csv_index
                else f"CID={k.item_cid}"
            )
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

    def _on_click(self, event: tk.Event) -> None:
        cx = int(self.canvas.canvasx(event.x))
        cy = int(self.canvas.canvasy(event.y))
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
        if uid is not None:
            k = self._phantom_items.get(uid) or self.state.items.get(uid)
            if k:
                self._show_popup(uid, k, event.x_root, event.y_root)
            return
        self._phantom_draw_state = {
            "start_row": row,
            "start_col": col,
            "cur_row": row,
            "cur_col": col,
        }
        self._draw(update_total=False)

    def _on_right_click(self, event: tk.Event) -> None:
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
            self._refresh_summary_bars()
            self._draw(update_total=False)

    def _find_resize_handle_at(self, cx: int, cy: int) -> Optional[Tuple[str, str]]:
        hz = RESIZE_HANDLE_W + 2
        for uid, k in self.state.items.items():
            if k.shape is not None or k.box_id is None:
                continue
            dc, dr = self._effective_display_origin(uid, k)
            w, h = self._effective_shape_wh(uid, k)
            x1 = dc * CELL_W + 2
            y1 = dr * CELL_H + 2
            x2 = (dc + w) * CELL_W - 2
            y2 = (dr + h) * CELL_H - 2
            in_x = x1 <= cx <= x2
            in_y = y1 <= cy <= y2
            if y2 - hz <= cy <= y2 and in_x:
                return (uid, "s")
            if y1 <= cy <= y1 + hz and in_x:
                return (uid, "n")
            if x2 - hz <= cx <= x2 and in_y:
                return (uid, "e")
            if x1 <= cx <= x1 + hz and in_y:
                return (uid, "w")
        return None

    def _start_drag(self, uid: str, direction: str, cx: int, cy: int) -> None:
        k = self.state.items.get(uid)
        if not k:
            return
        w, h = self._effective_shape_wh(uid, k)
        dc, dr = self._effective_display_origin(uid, k)
        self._drag_state = {
            "uid": uid,
            "direction": direction,
            "start_cx": cx,
            "start_cy": cy,
            "orig_w": w,
            "orig_h": h,
            "orig_dc": dc,
            "orig_dr": dr,
        }

    def _on_drag(self, event: tk.Event) -> None:
        """处理两种拖拽：画幽灵框、调整未知尺寸物品。"""
        if self._phantom_draw_state is not None:
            cx = int(self.canvas.canvasx(event.x))
            cy = int(self.canvas.canvasy(event.y))
            col = max(0, min(cx // CELL_W, GRID_COLS - 1))
            row = max(0, min(cy // CELL_H, self.vis_rows - 1))
            pds = self._phantom_draw_state
            if row != pds["cur_row"] or col != pds["cur_col"]:
                pds["cur_row"] = row
                pds["cur_col"] = col
                self._draw(update_total=False)
            return
        if not self._drag_state:
            return
        ds = self._drag_state
        uid = ds["uid"]
        k = self.state.items.get(uid)
        if not k:
            return
        cx = int(self.canvas.canvasx(event.x))
        cy = int(self.canvas.canvasy(event.y))
        dx_cells = (cx - ds["start_cx"]) / CELL_W
        dy_cells = (cy - ds["start_cy"]) / CELL_H
        w0, h0 = (ds["orig_w"], ds["orig_h"])
        dc0, dr0 = (ds["orig_dc"], ds["orig_dr"])
        direction = ds["direction"]
        brow = k.box_id // GRID_COLS if k.box_id is not None else dr0
        bcol = k.box_id % GRID_COLS if k.box_id is not None else dc0
        occ = self._build_occupied(exclude_uid=uid)
        if direction == "e":
            delta = round(dx_cells)
            if delta > 0:
                max_ext = 0
                for c in range(dc0 + w0, GRID_COLS):
                    if any(((dr0 + r, c) in occ for r in range(h0))):
                        break
                    max_ext += 1
                delta = min(delta, max_ext)
            new_w = max(bcol - dc0 + 1, max(1, w0 + delta))
            new_h = h0
            new_dc, new_dr = (dc0, dr0)
        elif direction == "w":
            delta = round(dx_cells)
            if delta < 0:
                max_ext = 0
                for c in range(dc0 - 1, -1, -1):
                    if any(((dr0 + r, c) in occ for r in range(h0))):
                        break
                    max_ext += 1
                delta = max(delta, -max_ext)
            raw_dc = dc0 + delta
            new_dc = max(0, min(raw_dc, bcol))
            new_w = max(1, dc0 + w0 - new_dc)
            new_h = h0
            new_dr = dr0
        elif direction == "s":
            delta = round(dy_cells)
            if delta > 0:
                max_ext = 0
                for r in range(dr0 + h0, GRID_ROWS):
                    if any(((r, dc0 + c) in occ for c in range(w0))):
                        break
                    max_ext += 1
                delta = min(delta, max_ext)
            new_h = max(brow - dr0 + 1, max(1, h0 + delta))
            new_w = w0
            new_dc, new_dr = (dc0, dr0)
        elif direction == "n":
            delta = round(dy_cells)
            if delta < 0:
                max_ext = 0
                for r in range(dr0 - 1, -1, -1):
                    if any(((r, dc0 + c) in occ for c in range(w0))):
                        break
                    max_ext += 1
                delta = max(delta, -max_ext)
            raw_dr = dr0 + delta
            new_dr = max(0, min(raw_dr, brow))
            new_h = max(1, dr0 + h0 - new_dr)
            new_w = w0
            new_dc = dc0
        else:
            return
        new_dc = max(0, min(new_dc, GRID_COLS - 1))
        new_dr = max(0, min(new_dr, GRID_ROWS - 1))
        new_w = max(1, min(new_w, GRID_COLS - new_dc))
        new_h = max(1, min(new_h, GRID_ROWS - new_dr))
        new_shape = (new_w, new_h, new_dc, new_dr)
        if self._manual_shapes.get(uid) != new_shape:
            self._manual_shapes[uid] = new_shape
            self._draw(update_total=False)

    def _on_drag_end(self, event: tk.Event) -> None:
        if self._phantom_draw_state is not None:
            pds = self._phantom_draw_state
            sr, sc = (pds["start_row"], pds["start_col"])
            cr, cc = (pds["cur_row"], pds["cur_col"])
            min_r, max_r = (min(sr, cr), max(sr, cr))
            min_c, max_c = (min(sc, cc), max(sc, cc))
            self._create_phantom(min_r, min_c, max_c - min_c + 1, max_r - min_r + 1)
            self._phantom_draw_state = None
            self._refresh_summary_bars()
            self._draw(update_total=False)
        elif self._drag_state:
            self._drag_state = None
            self._refresh_summary_bars()
            self._draw(update_total=False)

    def _find_item_at(self, row: int, col: int) -> Optional[str]:
        for uid, k in self.state.items.items():
            if k.box_id is None:
                continue
            sc, sr = self._effective_display_origin(uid, k)
            w, h = self._effective_shape_wh(uid, k)
            if sr <= row < sr + h and sc <= col < sc + w:
                return uid
        for phid in self._phantom_items:
            if phid not in self._manual_shapes:
                continue
            w, h, dc, dr = self._manual_shapes[phid]
            if dr <= row < dr + h and dc <= col < dc + w:
                return phid
        return None

    def _show_popup(
        self, uid: str, k: ItemKnowledge, mouse_x: int = 200, mouse_y: int = 200
    ) -> None:
        """显示候选详情弹窗，并支持手动品质/候选确认。"""
        popup = tk.Toplevel(self.root)
        popup.title(f"物品候选  BoxId={k.box_id}")
        popup.transient(self.root)
        popup.configure(bg="#f5f5f8")
        pw, ph = (620, 500)
        popup.update_idletasks()
        sw = popup.winfo_screenwidth()
        sh = popup.winfo_screenheight()
        ox = mouse_x - pw // 2
        oy = mouse_y - ph // 2
        if ox + pw > sw:
            ox = max(0, sw - pw)
        if oy + ph > sh:
            oy = max(0, sh - ph)
        ox = max(0, ox)
        oy = max(0, oy)
        popup.geometry(f"{pw}x{ph}+{ox}+{oy}")
        popup.minsize(560, 440)
        popup.resizable(True, True)
        popup.grab_set()
        hdr_parts = []
        if k.shape:
            hdr_parts.append(f"形状: {fmt_shape(k.shape)}")
        elif uid in self._manual_shapes:
            mw, mh, _mdc, _mdr = self._manual_shapes[uid]
            tag = "手动画框" if uid in self._phantom_items else "手动设置"
            hdr_parts.append(f"形状: {mw}x{mh}（{tag}，精确匹配）")
        elif k.box_id is not None:
            max_w, max_h = self._compute_max_size(uid, k)
            if max_w < GRID_COLS or max_h < GRID_ROWS:
                hdr_parts.append(f"形状: ≤{max_w}x{max_h}（推断上界，非精确）")
        display_quality = self._display_quality(uid, k)
        if display_quality:
            if k.manual_quality is not None:
                hdr_parts.append(f"品质: Q{k.manual_quality}（手动设定）")
            elif self._is_high_quality_range(k):
                high_q_set = {
                    item.quality for item in self._candidate_items_for_grid(uid, k)
                }
                if len(high_q_set) == 1:
                    hdr_parts.append(f"品质: Q{display_quality}（高品质范围唯一）")
                else:
                    hdr_parts.append("品质: 金/红（高品质范围）")
            elif k.quality is not None:
                hdr_parts.append(f"品质: Q{k.quality}")
            else:
                hdr_parts.append(f"品质: Q{display_quality}（候选唯一补齐）")
        if k.categories:
            cats = " / ".join(
                (CATEGORY_NAMES.get(c, str(c)) for c in sorted(k.categories))
            )
            hdr_parts.append(f"类别: {cats}")
        if k.item_cid:
            hdr_parts.append(f"CID: {k.item_cid}")
        hdr_text = "    |    ".join(hdr_parts) if hdr_parts else "（属性未知）"
        q = display_quality or 0
        hdr_bg = QUALITY_BG.get(q, "#888888")
        tk.Label(
            popup,
            text=hdr_text,
            bg=hdr_bg,
            fg="#ffffff",
            font=("Microsoft YaHei", 10, "bold"),
            pady=6,
            padx=10,
            anchor="w",
        ).pack(fill="x")
        candidates = self._candidate_items_for_grid(uid, k)
        candidates.sort(key=lambda i: -i.base_value)
        candidate_probs = candidate_probabilities(
            candidates,
            map_category_weights=self._map_category_weights,
            map_id=self.state.map_id,
        )
        prob_source = probability_source_label(candidates, self.state.map_id)
        n = len(candidates)
        if n > 1:
            prices = [i.base_value for i in candidates]
            min_p, max_p = (min(prices), max(prices))
            _best, _count, _unique, weighted_est, weighted_label = (
                self._query_item_for_grid(uid, k)
            )
            weighted_text = (
                f"{weighted_label}: ¥{weighted_est:,.0f}    "
                if weighted_est is not None
                else ""
            )
            stat_text = f"共 {n} 个候选    {weighted_text}范围: ¥{min_p:,} ~ ¥{max_p:,}    概率: {prob_source}"
        elif n == 1:
            stat_text = f"唯一确定: {candidates[0].name}    ¥{candidates[0].base_value:,}    概率: {prob_source}"
        else:
            stat_text = "无匹配候选"
        tk.Label(
            popup,
            text=stat_text,
            bg="#ebebf0",
            fg="#444455",
            font=("Microsoft YaHei", 9),
            pady=4,
            padx=8,
            anchor="w",
        ).pack(fill="x")
        if k.excluded_categories:
            excl_names = " / ".join(
                (CATEGORY_NAMES.get(c, str(c)) for c in sorted(k.excluded_categories))
            )
            tk.Label(
                popup,
                text=f"  已排除类别（{len(k.excluded_categories)}个）: {excl_names}",
                bg="#f5e8e8",
                fg="#883333",
                font=("Microsoft YaHei", 8),
                pady=3,
                padx=10,
                anchor="w",
            ).pack(fill="x")
        frame = tk.Frame(popup, bg="#f5f5f8")
        frame.pack(fill="both", expand=True, padx=8, pady=(5, 3))
        cols_def = [
            ("名称", 125, "w"),
            ("品质", 45, "center"),
            ("形状", 50, "center"),
            ("类别", 120, "w"),
            ("概率", 65, "e"),
            ("价格", 75, "e"),
        ]
        tree = ttk.Treeview(
            frame, columns=[c[0] for c in cols_def], show="headings", height=7
        )
        style = ttk.Style()
        style.configure("Treeview", font=("Microsoft YaHei", 9), rowheight=20)
        style.configure("Treeview.Heading", font=("Microsoft YaHei", 9, "bold"))
        for col_name, width, anchor in cols_def:
            tree.heading(col_name, text=col_name)
            tree.column(col_name, width=width, anchor=anchor, minwidth=40)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        median_val = (
            statistics.median([i.base_value for i in candidates]) if n > 1 else 0
        )
        top_val = candidates[0].base_value if candidates else 0
        tree.tag_configure("top", background="#ffe4b0")
        tree.tag_configure("valuable", background="#ffd6d6")
        tree.tag_configure("high", background="#fffff0")
        tree.tag_configure("normal", background="#ffffff")
        tree.tag_configure("confirmed", background="#d9f7d9")
        iid_to_item: Dict[str, CsvItem] = {}
        selected_iid: Optional[str] = None
        confirmed_cid = k.manual_confirm_item_id
        for item in candidates:
            cat_str = " / ".join(
                (CATEGORY_NAMES.get(c, str(c)) for c in item.category_tags)
            )
            if confirmed_cid and item.item_id == confirmed_cid:
                tag = "confirmed"
            elif item.base_value >= HIGH_VALUE_THRESHOLD:
                tag = "valuable"
            elif item.base_value == top_val and n > 1:
                tag = "top"
            elif item.base_value >= median_val:
                tag = "high"
            else:
                tag = "normal"
            iid = tree.insert(
                "",
                "end",
                values=(
                    item.name,
                    f"Q{item.quality}",
                    fmt_shape(item.shape),
                    cat_str,
                    f"{candidate_probs.get(item.item_id, 0.0) * 100:.2f}%",
                    f"¥{item.base_value:,}",
                ),
                tags=(tag,),
            )
            iid_to_item[iid] = item
            if confirmed_cid and item.item_id == confirmed_cid:
                selected_iid = iid
        status_var = tk.StringVar(value="请选择候选物品，然后可设为金/红或确认")
        warn_var = tk.StringVar(value="")

        def _candidates_without_manual_quality() -> List[CsvItem]:
            old_mq = k.manual_quality
            try:
                k.manual_quality = None
                return self._candidate_items_for_grid(uid, k)
            finally:
                k.manual_quality = old_mq

        def _set_inline_warning(msg: str) -> None:
            warn_var.set(msg)

        def _set_manual_quality(qv: int) -> None:
            if qv not in (5, 6):
                return
            if (
                k.quality is not None
                and (not self._is_high_quality_range(k))
                and (k.quality != qv)
            ):
                _set_inline_warning(f"该格已判定为 Q{k.quality}，不能设为 Q{qv}。")
                return
            pool = _candidates_without_manual_quality()
            q5_count = sum((1 for it in pool if it.quality == 5))
            q6_count = sum((1 for it in pool if it.quality == 6))
            q_candidates = [it for it in pool if it.quality == qv]
            if not q_candidates:
                q_name = "金" if qv == 5 else "红"
                _set_inline_warning(
                    f"当前候选中没有“{q_name}”品质（金={q5_count}，红={q6_count}）。"
                )
                return
            k.manual_quality = qv
            warn_var.set("")
            self._refresh()
            popup.destroy()

        def _clear_manual_quality() -> None:
            k.manual_quality = None
            self._refresh()
            popup.destroy()

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

        tree.bind("<<TreeviewSelect>>", _on_select)
        tree.bind("<Double-1>", _confirm_selected)
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
        tk.Label(
            popup,
            textvariable=status_var,
            bg="#eef3ff",
            fg="#334466",
            font=("Microsoft YaHei", 9),
            pady=4,
            padx=10,
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 3))
        tk.Label(
            popup,
            textvariable=warn_var,
            bg="#fff0f0",
            fg="#b03a3a",
            font=("Microsoft YaHei", 9),
            pady=3,
            padx=10,
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 4))
        quality_frame = tk.Frame(popup, bg="#f5f5f8")
        quality_frame.pack(pady=(0, 4))
        tk.Button(
            quality_frame,
            text="设为金",
            command=lambda: _set_manual_quality(5),
            font=("Microsoft YaHei", 9),
            relief="flat",
            bg="#49557a",
            fg="white",
            padx=10,
            pady=3,
            cursor="hand2",
        ).pack(side="left", padx=4)
        tk.Button(
            quality_frame,
            text="设为红",
            command=lambda: _set_manual_quality(6),
            font=("Microsoft YaHei", 9),
            relief="flat",
            bg="#49557a",
            fg="white",
            padx=10,
            pady=3,
            cursor="hand2",
        ).pack(side="left", padx=4)
        tk.Button(
            quality_frame,
            text="清除设定",
            command=_clear_manual_quality,
            font=("Microsoft YaHei", 9),
            relief="flat",
            bg="#666666",
            fg="white",
            padx=10,
            pady=3,
            cursor="hand2",
        ).pack(side="left", padx=4)
        btn_frame = tk.Frame(popup, bg="#f5f5f8")
        btn_frame.pack(pady=4)
        tk.Button(
            btn_frame,
            text="确定候选",
            command=_confirm_selected,
            font=("Microsoft YaHei", 9),
            relief="flat",
            bg="#2f8f46",
            fg="white",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4)
        tk.Button(
            btn_frame,
            text="取消已确认",
            command=_clear_confirmation,
            font=("Microsoft YaHei", 9),
            relief="flat",
            bg="#8f5f2f",
            fg="white",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4)
        tk.Button(
            btn_frame,
            text="关闭",
            command=popup.destroy,
            font=("Microsoft YaHei", 9),
            relief="flat",
            bg="#5566aa",
            fg="white",
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4)

    def run(self) -> None:
        if self._owns_mainloop:
            self.root.mainloop()
