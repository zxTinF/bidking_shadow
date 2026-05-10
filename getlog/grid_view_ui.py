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
)


class GridWindowUiMixin:
    """负责构建窗口、导航条、输入区和画布容器。"""

    def _build_window(self) -> None:
        """按当前模式组装主窗口。"""
        if self._master is None:
            self.root = tk.Tk()
        else:
            self.root = tk.Toplevel(self._master)
        self.root.title(
            f"BidKing 鉴影可视化 第 {self.state.current_round} 回合"
        )
        self.root.configure(bg="#1a1a2e")
        self._build_info_bar()
        self._build_legend()
        self._build_canvas()
        if self._snapshots:
            self._build_nav_bar()
        self._build_input_table()
        self._build_footer_note()
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
        controls = tk.Frame(self.root, bg="#1a1a2e", pady=0)
        controls.pack(fill="x", padx=8, pady=(0, 2))
        self._always_on_top_var = tk.BooleanVar(value=False)
        self._always_on_top_button = tk.Checkbutton(
            controls,
            text="置顶",
            variable=self._always_on_top_var,
            command=self._toggle_always_on_top,
            indicatoron=False,
            bg="#2d3448",
            fg="#dfe7ff",
            activebackground="#42577a",
            activeforeground="#ffffff",
            selectcolor="#4f7aa3",
            relief="flat",
            padx=10,
            pady=2,
            font=("Microsoft YaHei UI", 9),
        )
        self._always_on_top_button.pack(side="left")
        self._update_always_on_top_button()

    def _toggle_always_on_top(self) -> None:
        enabled = bool(self._always_on_top_var.get())
        try:
            self.root.attributes("-topmost", enabled)
        except tk.TclError as exc:
            self._always_on_top_var.set(False)
            self._update_always_on_top_button()
            messagebox.showerror("置顶失败", str(exc), parent=self.root)
            return
        self._update_always_on_top_button()

    def _update_always_on_top_button(self) -> None:
        enabled = bool(self._always_on_top_var.get())
        if enabled:
            self._always_on_top_button.config(
                text="已置顶",
                bg="#4f7aa3",
                activebackground="#5d8ebd",
                fg="#ffffff",
            )
        else:
            self._always_on_top_button.config(
                text="置顶",
                bg="#2d3448",
                activebackground="#42577a",
                fg="#dfe7ff",
            )

    def _build_legend(self) -> None:
        """构建图例和总价展示区。"""
        bar = tk.Frame(self.root, bg="#222233", pady=5)
        bar.pack(fill="x", padx=8)
        controls_row = tk.Frame(bar, bg="#222233")
        controls_row.pack(fill="x")
        total_row = tk.Frame(bar, bg="#222233")
        total_row.pack(fill="x", pady=(4, 0))
        self._estimate_method_var = tk.StringVar(
            value=self.ESTIMATE_METHOD_EXPECTED
        )
        estimate = self._calc_selected_estimate_price()
        tk.Button(
            controls_row,
            text="估价",
            command=self._update_total_label,
            bg="#3f6f99",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=2,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="right", padx=(0, 8))
        method_box = tk.OptionMenu(
            controls_row,
            self._estimate_method_var,
            *self.ESTIMATE_METHODS,
            command=lambda _value: self._update_total_label(),
        )
        method_box.config(
            bg="#1f2233",
            fg="#dfe7ff",
            activebackground="#42577a",
            activeforeground="#ffffff",
            highlightbackground="#4b4b65",
            highlightcolor="#4b4b65",
            relief="flat",
            borderwidth=0,
            width=16,
            padx=6,
            pady=2,
            font=("Microsoft YaHei UI", 9),
        )
        method_box["menu"].config(
            bg="#1f2233",
            fg="#dfe7ff",
            activebackground="#3f6f99",
            activeforeground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        method_box.pack(side="right", padx=(0, 8))
        self._total_label = tk.Label(
            total_row,
            text=f"估算总价格: {self._estimate_display_text(estimate)}",
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

    @staticmethod
    def _sanitize_decimal_var(var: tk.StringVar) -> None:
        raw = var.get()
        normalized = raw.replace("。", ".")
        chars = []
        seen_dot = False
        decimals = 0
        for ch in normalized:
            if ch.isdigit():
                if seen_dot:
                    if decimals >= 2:
                        continue
                    decimals += 1
                chars.append(ch)
            elif ch == "." and not seen_dot:
                seen_dot = True
                chars.append(ch)
        clean = "".join(chars)
        if raw != clean:
            var.set(clean)

    def _sanitize_registered_input_vars(self) -> None:
        for key, var in self._input_vars.items():
            if key == "gold_avg_cells":
                self._sanitize_decimal_var(var)
            else:
                self._sanitize_numeric_var(var)

    def _validate_gold_total_cells(self, proposed: str) -> bool:
        return proposed == "" or proposed.isdigit()

    def _validate_gold_avg_cells(self, proposed: str) -> bool:
        proposed = proposed.replace("。", ".")
        if proposed == "":
            return True
        if proposed.count(".") > 1:
            return False
        head, dot, tail = proposed.partition(".")
        if head and not head.isdigit():
            return False
        if dot and (not head or len(tail) > 2 or (tail and not tail.isdigit())):
            return False
        return bool(head or dot)

    def _collect_input_values(self) -> Dict[str, object]:
        values: Dict[str, object] = {}
        for key, var in self._input_vars.items():
            raw = var.get().strip().replace("。", ".")
            if key == "gold_avg_cells":
                values[key] = float(raw) if raw and raw != "." else None
            else:
                values[key] = int(raw) if raw.isdigit() else None
        return values

    def _confirm_input_values(self) -> None:
        """记录输入区当前值，供后续人工估算参考。"""
        self._sanitize_registered_input_vars()
        self._captured_input_values = self._resolved_gold_input_values(
            self._collect_input_values()
        )
        count = self._captured_input_values.get("gold_count")
        cells = self._captured_input_values.get("gold_total_cells")
        avg = self._captured_input_values.get("gold_avg_cells")
        if isinstance(count, int):
            self._gold_count_var.set(str(count))
        if isinstance(cells, int):
            self._gold_total_cells_var.set(str(cells))
        if isinstance(avg, (int, float)):
            self._gold_avg_cells_var.set(f"{float(avg):.2f}")
        parts: List[str] = []
        for key, val in self._captured_input_values.items():
            label = self._input_labels.get(key, key)
            shown = str(val) if val is not None else "-"
            parts.append(f"{label}={shown}")
        self._refresh_summary_bars()

    def _reset_input_values(self) -> None:
        """清空输入区约束并刷新估算。"""
        for var in self._input_vars.values():
            var.set("")
        self._captured_input_values = {}
        self._refresh_summary_bars()

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
            text="金个数",
            bg="#2a2a3a",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self._gold_count_var = tk.StringVar(value="")
        self._input_vars["gold_count"] = self._gold_count_var
        self._input_labels["gold_count"] = "金个数"
        ent_count = tk.Entry(
            fields,
            textvariable=self._gold_count_var,
            bg="#1f2233",
            fg="#ffffff",
            insertbackground="#ffffff",
            relief="flat",
            font=("Consolas", 10),
            width=6,
            validate="key",
            validatecommand=(self.root.register(self._validate_gold_total_cells), "%P"),
        )
        ent_count.grid(row=0, column=1, sticky="w")
        ent_count.bind(
            "<KeyRelease>",
            lambda _e: self._sanitize_numeric_var(self._gold_count_var),
        )
        tk.Label(
            fields,
            text="金总格",
            bg="#2a2a3a",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=2, sticky="w", padx=(16, 8))
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
            width=6,
            validate="key",
            validatecommand=(self.root.register(self._validate_gold_total_cells), "%P"),
        )
        ent.grid(row=0, column=3, sticky="w")
        ent.bind("<KeyRelease>", self._sanitize_gold_total_cells)
        tk.Label(
            fields,
            text="金均格",
            bg="#2a2a3a",
            fg="#ffffff",
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=4, sticky="w", padx=(16, 8))
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
            width=7,
            validate="key",
            validatecommand=(self.root.register(self._validate_gold_avg_cells), "%P"),
        )
        ent_avg.grid(row=0, column=5, sticky="w")
        ent_avg.bind(
            "<KeyRelease>",
            lambda _e: self._sanitize_decimal_var(self._gold_avg_cells_var),
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
        ).grid(row=0, column=6, sticky="w", padx=(16, 0))
        tk.Button(
            fields,
            text="重置",
            command=self._reset_input_values,
            bg="#4b4f63",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=2,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=0, column=7, sticky="w", padx=(8, 0))
        autofill = tk.Frame(table, bg="#2a2a3a")
        autofill.pack(anchor="w", fill="x", pady=(8, 0))
        left = tk.Frame(autofill, bg="#2a2a3a")
        left.pack(side="left", anchor="n")
        tk.Button(
            left,
            text="尝试填充",
            command=self._try_autofill_hidden_high_quality,
            bg="#546b3f",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=3,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", anchor="w")
        tk.Button(
            left,
            text="二次填充",
            command=self._try_secondary_autofill_high_quality,
            bg="#5b5840",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=3,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", anchor="w", padx=(8, 0))
        tk.Button(
            left,
            text="清空填充",
            command=self._clear_autofill_view,
            bg="#65424a",
            fg="#ffffff",
            relief="flat",
            padx=10,
            pady=3,
            font=("Microsoft YaHei UI", 9),
        ).pack(side="left", anchor="w", padx=(8, 0))
        tk.Label(
            left,
            text="先尝试填充快速补空格，手动增删后再使用二次填充；\n填充和二次填充可能性非常多，结果不一定准确，更建议手动调整。",
            bg="#2a2a3a",
            fg="#c8cedf",
            font=("Microsoft YaHei UI", 9),
            wraplength=290,
            justify="left",
        ).pack(side="left", anchor="w", padx=(10, 0))

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
        self.canvas.pack(side="left", fill="y", expand=False)
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

    def _build_footer_note(self) -> None:
        """构建右下角项目说明。"""
        bar = tk.Frame(self.root, bg="#1a1a2e", pady=2)
        bar.pack(fill="x", padx=8, pady=(0, 4))
        tk.Label(
            bar,
            text="此为开源免费项目",
            bg="#1a1a2e",
            fg="#8f98b3",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="right")
