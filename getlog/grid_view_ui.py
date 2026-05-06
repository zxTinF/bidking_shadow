# -*- coding: utf-8 -*-
"""GridWindow 的 Tk 窗口与控件搭建逻辑。"""

import tkinter as tk
from tkinter import messagebox
from typing import Dict, List, Optional

from .grid_view_shared import (
    CANVAS_MAX_H,
    CANVAS_MAX_W,
    CELL_H,
    CELL_W,
    EMPTY_BG,
    GRID_COLS,
    GRID_ROWS,
    UNKNOWN_BG,
)


class GridWindowUiMixin:
    """负责构建窗口、导航条、输入区和画布容器。"""

    def _build_window(self) -> None:
        """按当前模式组装主窗口。"""
        live_tag = "  ● LIVE" if self._log_path else ""
        if self._master is None:
            self.root = tk.Tk()
        else:
            self.root = tk.Toplevel(self._master)
        self.root.title(
            f"BidKing 鉴影可视化 第 {self.state.current_round} 回合{live_tag}"
        )
        self.root.configure(bg="#1a1a2e")
        self._build_info_bar()
        self._build_legend()
        self._build_canvas()
        if self._snapshots:
            self._build_nav_bar()
        self._build_input_table()
        self._draw()

    def _build_info_bar(self) -> None:
        bar = tk.Frame(self.root, bg="#1a1a2e", pady=4)
        bar.pack(fill="x", padx=8)
        self._info_text = tk.StringVar(value=self._info_summary_text())
        tk.Label(
            bar,
            textvariable=self._info_text,
            bg="#1a1a2e",
            fg="#ccccdd",
            font=("Microsoft YaHei UI", 10),
            wraplength=CANVAS_MAX_W - 20,
            justify="left",
        ).pack(side="left")
        if self._log_path:
            tk.Label(
                bar,
                text=" ● LIVE ",
                bg="#c03030",
                fg="#ffffff",
                font=("Microsoft YaHei UI", 9, "bold"),
                relief="flat",
                padx=4,
            ).pack(side="right", padx=8)

    def _build_legend(self) -> None:
        """构建图例和总价展示区。"""
        bar = tk.Frame(self.root, bg="#222233", pady=5)
        bar.pack(fill="x", padx=8)
        tk.Label(
            bar,
            text=" 未知 ",
            bg=UNKNOWN_BG,
            fg="#ffffff",
            font=("Microsoft YaHei UI", 8),
            relief="flat",
            padx=2,
        ).pack(side="left", padx=(6, 2))
        total = self._calc_grid_total_price()
        self._total_label = tk.Label(
            bar,
            text=f"估算总价  ¥{total:,.0f}",
            bg="#222233",
            fg="#e8d080",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self._total_label.pack(side="right", padx=12)

    def _sanitize_gold_total_cells(self, _event: Optional[tk.Event] = None) -> None:
        self._sanitize_numeric_var(self._gold_total_cells_var)

    @staticmethod
    def _sanitize_numeric_var(var: tk.StringVar) -> None:
        raw = var.get()
        clean = "".join((ch for ch in raw if ch.isdigit()))
        if raw != clean:
            var.set(clean)

    def _sanitize_registered_input_vars(self) -> None:
        for var in self._input_vars.values():
            self._sanitize_numeric_var(var)

    def _validate_gold_total_cells(self, proposed: str) -> bool:
        return proposed == "" or proposed.isdigit()

    def _collect_input_values(self) -> Dict[str, Optional[int]]:
        values: Dict[str, Optional[int]] = {}
        for key, var in self._input_vars.items():
            raw = var.get().strip()
            values[key] = int(raw) if raw.isdigit() else None
        return values

    def _confirm_input_values(self) -> None:
        """记录输入区当前值，供后续人工估算参考。"""
        self._sanitize_registered_input_vars()
        self._captured_input_values = self._collect_input_values()
        parts: List[str] = []
        for key, val in self._captured_input_values.items():
            label = self._input_labels.get(key, key)
            shown = str(val) if val is not None else "-"
            parts.append(f"{label}={shown}")
        msg = "，".join(parts) if parts else "暂无输入值"
        messagebox.showinfo("输入已记录", msg, parent=self.root)

    def _build_input_table(self) -> None:
        """构建人工录入区。"""
        bar = tk.Frame(self.root, bg="#222233", pady=4)
        bar.pack(fill="x", padx=8, pady=(0, 2))
        table = tk.Frame(
            bar,
            bg="#2a2a3a",
            highlightbackground="#4b4b65",
            highlightthickness=1,
            padx=8,
            pady=6,
        )
        table.pack(fill="x")
        fields = tk.Frame(table, bg="#2a2a3a")
        fields.pack(anchor="w")
        tk.Label(
            fields,
            text="金总格",
            bg="#2a2a3a",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gold_total_cells_var = tk.StringVar(value="")
        self._input_vars["gold_total_cells"] = self._gold_total_cells_var
        self._input_labels["gold_total_cells"] = "金总格"
        ent = tk.Entry(
            fields,
            textvariable=self._gold_total_cells_var,
            bg="#1f2233",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief="flat",
            font=("Consolas", 10),
            width=14,
            validate="key",
            validatecommand=(self.root.register(self._validate_gold_total_cells), "%P"),
        )
        ent.grid(row=0, column=1, sticky="w")
        ent.bind("<KeyRelease>", self._sanitize_gold_total_cells)
        tk.Label(
            fields,
            text="金均格",
            bg="#2a2a3a",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=2, sticky="w", padx=(16, 8))
        self._gold_avg_cells_var = tk.StringVar(value="")
        self._input_vars["gold_avg_cells"] = self._gold_avg_cells_var
        self._input_labels["gold_avg_cells"] = "金均格"
        ent_avg = tk.Entry(
            fields,
            textvariable=self._gold_avg_cells_var,
            bg="#1f2233",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief="flat",
            font=("Consolas", 10),
            width=14,
            validate="key",
            validatecommand=(self.root.register(self._validate_gold_total_cells), "%P"),
        )
        ent_avg.grid(row=0, column=3, sticky="w")
        ent_avg.bind(
            "<KeyRelease>",
            lambda _e: self._sanitize_numeric_var(self._gold_avg_cells_var),
        )
        tk.Button(
            fields,
            text="确认",
            command=self._confirm_input_values,
            bg="#3f6f99",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=2,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=4, sticky="w", padx=(16, 0))

    def _build_nav_bar(self) -> None:
        """快照回放模式下的回合导航栏。"""
        bar = tk.Frame(self.root, bg="#1a1a2e", pady=4)
        bar.pack(fill="x", padx=8, pady=(0, 6))
        self._btn_prev = tk.Button(
            bar,
            text="上一回合",
            command=lambda: self._snap_goto(self._snap_idx - 1),
            bg="#334466",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=3,
            font=("Microsoft YaHei UI", 9),
        )
        self._btn_prev.pack(side="left")
        self._nav_label = tk.StringVar()
        tk.Label(
            bar,
            textvariable=self._nav_label,
            bg="#1a1a2e",
            fg="#d8deff",
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", padx=12)
        self._btn_next = tk.Button(
            bar,
            text="下一回合",
            command=lambda: self._snap_goto(self._snap_idx + 1),
            bg="#334466",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=3,
            font=("Microsoft YaHei UI", 9),
        )
        self._btn_next.pack(side="left")
        self._update_nav_label()

    def _update_nav_label(self) -> None:
        if not self._snapshots:
            return
        label, _ = self._snapshots[self._snap_idx]
        total = len(self._snapshots)
        self._nav_label.set(f"{label}   ({self._snap_idx + 1} / {total})")
        self._btn_prev.config(
            state="normal" if self._snap_idx > 0 else "disabled",
            bg="#334466" if self._snap_idx > 0 else "#222233",
        )
        self._btn_next.config(
            state="normal" if self._snap_idx < len(self._snapshots) - 1 else "disabled",
            bg="#334466" if self._snap_idx < len(self._snapshots) - 1 else "#222233",
        )

    def _snap_goto(self, idx: int) -> None:
        if not self._snapshots or not 0 <= idx < len(self._snapshots):
            return
        self._snap_idx = idx
        self.state = self._snapshots[idx][1]
        self._recalc_vis_rows()
        self._remove_overlapping_phantoms()
        label, _ = self._snapshots[idx]
        self.root.title(f"BidKing 鉴影可视化  对局 {self.state.uid}  {label}")
        self._refresh()
        self._update_nav_label()
        cw = GRID_COLS * CELL_W + 1
        ch = GRID_ROWS * CELL_H + 1
        self.canvas.config(scrollregion=(0, 0, cw, ch))

    def _snap_prev(self) -> None:
        self._snap_goto(self._snap_idx - 1)

    def _snap_next(self) -> None:
        self._snap_goto(self._snap_idx + 1)

    def _build_canvas(self) -> None:
        outer = tk.Frame(self.root, bg="#1a1a2e")
        outer.pack(fill="both", expand=True, anchor="w", padx=8, pady=(4, 8))
        cw = GRID_COLS * CELL_W + 1
        ch = GRID_ROWS * CELL_H + 1
        v_sb = tk.Scrollbar(outer, orient="vertical")
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
        self.canvas.pack(side="left", fill="y", expand=True)
        v_sb.pack(side="left", fill="y")
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.focus_set()
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> str:
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        return "break"
