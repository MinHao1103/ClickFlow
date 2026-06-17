import tkinter as tk
from tkinter import ttk, messagebox
import logging
from typing import List, Optional

from models.click_step import ClickStep
from models.profile import Profile
from services.database_manager import DatabaseManager
from services.click_executor import ClickExecutor, ExecutionState
from services.keyboard_monitor import KeyboardMonitor
from services.mouse_tracker import MouseTracker

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────
_C = {
    "bg":            "#f0f2f5",
    "bg_dark":       "#e2e8f0",
    "card":          "#ffffff",
    "border":        "#cbd5e1",
    "text":          "#1e293b",
    "text_muted":    "#64748b",
    "accent":        "#2563eb",
    "accent_dark":   "#1d4ed8",
    "success":       "#16a34a",
    "success_dark":  "#15803d",
    "success_bg":    "#dcfce7",
    "danger":        "#dc2626",
    "danger_dark":   "#b91c1c",
    "danger_bg":     "#fee2e2",
    "warning":       "#b45309",
    "warning_bg":    "#fef3c7",
    "purple":        "#7c3aed",
    "teal":          "#0891b2",
    # status-bar state backgrounds
    "sb_idle":       "#e2e8f0",
    "sb_run":        "#dcfce7",
    "sb_error":      "#fee2e2",
}

# Per-action foreground colour in the listbox
_ACTION_FG = {
    "click":          _C["accent"],
    "double_click":   _C["accent"],
    "right_click":    _C["purple"],
    "move":           _C["teal"],
    "delay":          _C["warning"],
    "keyboard_input": _C["success"],
    "hotkey":         _C["success"],
}

_ACTION_TYPES = [
    "click", "double_click", "right_click",
    "move", "delay", "keyboard_input", "hotkey",
]
_COORD_ACTIONS = {"click", "double_click", "right_click", "move"}
_KB_ACTIONS    = {"keyboard_input", "hotkey"}


# ── Tooltip ───────────────────────────────────────────────────────────────────
class _Tip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._w = widget
        self._text = text
        self._win: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _e=None) -> None:
        x = self._w.winfo_rootx() + 16
        y = self._w.winfo_rooty() + self._w.winfo_height() + 4
        self._win = tk.Toplevel(self._w)
        self._win.wm_overrideredirect(True)
        self._win.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._win, text=self._text,
            bg="#fffde7", fg=_C["text"],
            relief="solid", bd=1,
            font=("Segoe UI", 9), padx=7, pady=3,
        ).pack()

    def _hide(self, _e=None) -> None:
        if self._win:
            self._win.destroy()
            self._win = None


# ── MainWindow ────────────────────────────────────────────────────────────────
class MainWindow:
    def __init__(self, root: tk.Tk, db: DatabaseManager) -> None:
        self._root = root
        self._db = db
        self._steps: List[ClickStep] = []
        self._edit_index: Optional[int] = None
        self._active_profile: str = ""

        self._executor = ClickExecutor(
            on_status_update=self._on_status_update,
            on_finished=self._on_execution_finished,
            on_error=self._on_execution_error,
        )
        self._mouse_tracker = MouseTracker(callback=self._on_mouse_move)
        self._keyboard_monitor = KeyboardMonitor(
            on_stop=self._stop_execution,
            on_capture=self._capture_position,
        )

        self._apply_styles()
        self._build_window()
        self._build_ui()
        self._apply_action_state()
        self._refresh_list()          # show empty-state immediately
        self._reload_profile_list()
        self._mouse_tracker.start()
        self._keyboard_monitor.start()

    # ── styles ────────────────────────────────────────────────────────────────

    def _apply_styles(self) -> None:
        s = ttk.Style(self._root)
        s.theme_use("clam")

        s.configure(".",
            background=_C["bg"],
            foreground=_C["text"],
            font=("Segoe UI", 10),
            bordercolor=_C["border"],
            troughcolor=_C["bg_dark"],
            focuscolor=_C["accent"],
        )
        for cls in ("TFrame", "TLabel", "TCheckbutton"):
            s.configure(cls, background=_C["bg"], foreground=_C["text"])

        s.configure("TLabelframe",
            background=_C["bg"],
            bordercolor=_C["border"],
            relief="groove",
        )
        s.configure("TLabelframe.Label",
            background=_C["bg"],
            foreground=_C["accent"],
            font=("Segoe UI", 10, "bold"),
        )
        s.configure("TEntry",
            fieldbackground=_C["card"],
            foreground=_C["text"],
            bordercolor=_C["border"],
            lightcolor=_C["border"],
            darkcolor=_C["border"],
            insertcolor=_C["accent"],
        )
        s.configure("TCombobox",
            fieldbackground=_C["card"],
            foreground=_C["text"],
            arrowcolor=_C["accent"],
        )
        s.configure("TSeparator", background=_C["border"])
        s.configure("TScrollbar",
            background=_C["bg_dark"],
            troughcolor=_C["bg"],
            bordercolor=_C["bg"],
            arrowcolor=_C["text_muted"],
            arrowsize=12,
        )

        # ── button variants ───────────────────────────────────────────────────
        s.configure("TButton",
            background=_C["bg_dark"],
            foreground=_C["text"],
            bordercolor=_C["border"],
            padding=(8, 4),
            relief="flat",
        )
        s.map("TButton",
            background=[("active", _C["border"]), ("disabled", _C["bg"])],
            foreground=[("disabled", _C["text_muted"])],
            relief=[("active", "flat")],
        )

        s.configure("Accent.TButton",
            background=_C["accent"],
            foreground="white",
            bordercolor=_C["accent"],
            padding=(8, 4),
            font=("Segoe UI", 10),
            relief="flat",
        )
        s.map("Accent.TButton",
            background=[("active", _C["accent_dark"]), ("disabled", "#93c5fd")],
            relief=[("active", "flat")],
        )

        s.configure("Start.TButton",
            background=_C["success"],
            foreground="white",
            bordercolor=_C["success"],
            font=("Segoe UI", 11, "bold"),
            padding=(6, 9),
            relief="flat",
        )
        s.map("Start.TButton",
            background=[("active", _C["success_dark"]), ("disabled", "#86efac")],
            relief=[("active", "flat")],
        )

        s.configure("Stop.TButton",
            background=_C["danger"],
            foreground="white",
            bordercolor=_C["danger"],
            font=("Segoe UI", 11, "bold"),
            padding=(6, 9),
            relief="flat",
        )
        s.map("Stop.TButton",
            background=[("active", _C["danger_dark"]), ("disabled", _C["bg_dark"])],
            foreground=[("disabled", _C["text_muted"])],
            relief=[("active", "flat")],
        )

        s.configure("Ghost.TButton",
            background=_C["bg"],
            foreground=_C["text_muted"],
            bordercolor=_C["border"],
            padding=(6, 3),
            font=("Segoe UI", 9),
            relief="flat",
        )
        s.map("Ghost.TButton",
            background=[("active", _C["bg_dark"])],
            foreground=[("active", _C["text"])],
            relief=[("active", "flat")],
        )

        s.configure("GhostDanger.TButton",
            background=_C["bg"],
            foreground=_C["danger"],
            bordercolor=_C["border"],
            padding=(6, 3),
            font=("Segoe UI", 9),
            relief="flat",
        )
        s.map("GhostDanger.TButton",
            background=[("active", _C["danger_bg"])],
            relief=[("active", "flat")],
        )

    # ── window ────────────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self._root.title("Automation Script Engine")
        self._root.geometry("1000x780")
        self._root.resizable(False, False)
        self._root.configure(bg=_C["bg"])

    # ── top-level layout ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_tracker_bar()

        # Pack BOTTOM items first so mid's expand=True doesn't consume their space
        self._build_status_bar()
        self._build_profile_bar()

        mid = ttk.Frame(self._root)
        mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 0))

        # LEFT — fixed width
        left = ttk.LabelFrame(mid, text="  步驟編輯器", width=280)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        left.pack_propagate(False)
        self._build_step_editor(left)

        # RIGHT — fixed width, pack before center to prevent expand starvation
        right = ttk.LabelFrame(mid, text="  執行控制", width=230)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(4, 0))
        right.pack_propagate(False)
        self._build_execution_panel(right)

        # CENTER — takes all remaining space
        center = ttk.LabelFrame(mid, text="  步驟序列")
        center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
        self._build_step_sequence(center)

    # ── tracker bar ───────────────────────────────────────────────────────────

    def _build_tracker_bar(self) -> None:
        card = tk.Frame(self._root, bg=_C["card"],
                        highlightthickness=1, highlightbackground=_C["border"])
        card.pack(fill=tk.X, padx=8, pady=(8, 4))

        inner = tk.Frame(card, bg=_C["card"], padx=14, pady=8)
        inner.pack(fill=tk.X)

        # section label
        tk.Label(inner, text="即時滑鼠座標", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 16))

        # X
        tk.Label(inner, text="X", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self._var_mx = tk.IntVar(value=0)
        tk.Label(inner, textvariable=self._var_mx, bg=_C["card"],
                 fg=_C["accent"], font=("Segoe UI", 22, "bold"),
                 width=5, anchor=tk.E).pack(side=tk.LEFT)

        # Y
        tk.Label(inner, text="Y", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(18, 5))
        self._var_my = tk.IntVar(value=0)
        tk.Label(inner, textvariable=self._var_my, bg=_C["card"],
                 fg=_C["accent"], font=("Segoe UI", 22, "bold"),
                 width=5, anchor=tk.E).pack(side=tk.LEFT)

        # divider
        tk.Frame(inner, bg=_C["border"], width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=20, pady=2)

        # capture button
        btn = ttk.Button(inner, text="⊕  擷取座標",
                         style="Accent.TButton", command=self._capture_position)
        btn.pack(side=tk.LEFT)
        _Tip(btn, "點擊按鈕或按 S 鍵，將目前游標位置填入步驟編輯器")

        tk.Label(inner, text="  ← S 鍵快速觸發", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "italic")).pack(side=tk.LEFT, padx=8)

    # ── step editor ───────────────────────────────────────────────────────────

    def _build_step_editor(self, parent: ttk.LabelFrame) -> None:
        # ── Action type ──
        self._mk_field_label(parent, "動作類型")
        self._var_action = tk.StringVar(value="click")
        self._combo_action = ttk.Combobox(
            parent, textvariable=self._var_action,
            values=_ACTION_TYPES, state="readonly", width=18,
        )
        self._combo_action.pack(fill=tk.X, padx=10, pady=(2, 6))
        self._combo_action.bind(
            "<<ComboboxSelected>>", lambda _: self._apply_action_state()
        )

        self._mk_divider(parent)

        # ── Coordinates ──
        self._mk_field_label(parent, "座標")
        row = tk.Frame(parent, bg=_C["bg"])
        row.pack(fill=tk.X, padx=10, pady=(2, 6))

        tk.Label(row, text="X", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9), width=2).pack(side=tk.LEFT)
        self._var_x = tk.StringVar()
        self._ent_x = ttk.Entry(row, textvariable=self._var_x, width=7)
        self._ent_x.pack(side=tk.LEFT, padx=(3, 14))

        tk.Label(row, text="Y", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9), width=2).pack(side=tk.LEFT)
        self._var_y = tk.StringVar()
        self._ent_y = ttk.Entry(row, textvariable=self._var_y, width=7)
        self._ent_y.pack(side=tk.LEFT, padx=(3, 0))

        self._mk_divider(parent)

        # ── Count / Delay ──
        self._mk_field_label(parent, "次數 / 延遲")
        row2 = tk.Frame(parent, bg=_C["bg"])
        row2.pack(fill=tk.X, padx=10, pady=(2, 6))

        tk.Label(row2, text="×", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 10), width=2).pack(side=tk.LEFT)
        self._var_count = tk.StringVar(value="1")
        self._ent_count = ttk.Entry(row2, textvariable=self._var_count, width=5)
        self._ent_count.pack(side=tk.LEFT, padx=(3, 2))
        tk.Label(row2, text="次", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 14))

        tk.Label(row2, text="⏱", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self._var_delay = tk.StringVar(value="0")
        ttk.Entry(row2, textvariable=self._var_delay, width=5).pack(
            side=tk.LEFT, padx=(3, 2))
        tk.Label(row2, text="秒", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)

        self._mk_divider(parent)

        # ── Keyboard / Hotkey ──
        self._mk_field_label(parent, "文字 / 按鍵組合")
        self._var_kb = tk.StringVar()
        self._ent_kb = ttk.Entry(parent, textvariable=self._var_kb, width=22)
        self._ent_kb.pack(fill=tk.X, padx=10, pady=(2, 2))
        self._lbl_kb_hint = tk.Label(
            parent, text="", bg=_C["bg"],
            fg=_C["text_muted"], font=("Segoe UI", 8, "italic"),
        )
        self._lbl_kb_hint.pack(anchor=tk.W, padx=11, pady=(0, 6))

        self._mk_divider(parent)

        # ── Buttons ──
        btn_area = tk.Frame(parent, bg=_C["bg"])
        btn_area.pack(fill=tk.X, padx=10, pady=(8, 10))

        self._btn_commit = ttk.Button(
            btn_area, text="＋  新增步驟",
            style="Accent.TButton", command=self._commit_step,
        )
        self._btn_commit.pack(fill=tk.X, pady=(0, 5))

        self._btn_cancel = ttk.Button(
            btn_area, text="✕  取消編輯",
            style="Ghost.TButton", command=self._cancel_edit, state=tk.DISABLED,
        )
        self._btn_cancel.pack(fill=tk.X)

    def _mk_field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text.upper(), bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "bold"),
                 ).pack(anchor=tk.W, padx=10, pady=(8, 0))

    def _mk_divider(self, parent: tk.Widget) -> None:
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=10, pady=4)

    def _apply_action_state(self) -> None:
        action = self._var_action.get()
        coord = tk.NORMAL if action in _COORD_ACTIONS else tk.DISABLED
        kb    = tk.NORMAL if action in _KB_ACTIONS    else tk.DISABLED
        self._ent_x.config(state=coord)
        self._ent_y.config(state=coord)
        self._ent_kb.config(state=kb)
        if action == "hotkey":
            hint = "格式: ctrl+c  /  alt+F4  /  ctrl+shift+s"
        elif action == "keyboard_input":
            hint = "輸入要打出的文字內容"
        else:
            hint = ""
        self._lbl_kb_hint.config(text=hint)

    # ── commit / parse / clear ────────────────────────────────────────────────

    def _commit_step(self) -> None:
        try:
            step = self._parse_step()
        except ValueError as exc:
            messagebox.showerror("輸入錯誤", str(exc))
            return
        if self._edit_index is None:
            self._steps.append(step)
        else:
            self._steps[self._edit_index] = step
            self._exit_edit_mode()
        self._refresh_list()

    def _cancel_edit(self) -> None:
        self._exit_edit_mode()
        self._clear_fields()

    def _exit_edit_mode(self) -> None:
        self._edit_index = None
        self._btn_commit.config(text="＋  新增步驟")
        self._btn_cancel.config(state=tk.DISABLED)

    def _parse_step(self) -> ClickStep:
        action = self._var_action.get()
        x, y = 0, 0
        if action in _COORD_ACTIONS:
            try:
                x = int(self._var_x.get())
                y = int(self._var_y.get())
            except ValueError:
                raise ValueError("X / Y 必須為整數")
        try:
            count = int(self._var_count.get())
            if count < 1:
                raise ValueError()
        except ValueError:
            raise ValueError("Count 必須為 ≥ 1 的整數")
        try:
            delay = float(self._var_delay.get())
            if delay < 0:
                raise ValueError()
        except ValueError:
            raise ValueError("Delay 必須為 ≥ 0 的數值")
        kb = self._var_kb.get().strip() or None
        return ClickStep(x=x, y=y, count=count, delay=delay,
                         action_type=action, keyboard_text=kb)

    def _clear_fields(self) -> None:
        self._var_action.set("click")
        self._var_x.set("")
        self._var_y.set("")
        self._var_count.set("1")
        self._var_delay.set("0")
        self._var_kb.set("")
        self._apply_action_state()

    # ── step sequence ─────────────────────────────────────────────────────────

    def _build_step_sequence(self, parent: ttk.LabelFrame) -> None:
        # top bar: active profile + step count
        top = tk.Frame(parent, bg=_C["bg"])
        top.pack(fill=tk.X, padx=8, pady=(4, 0))
        self._lbl_count = tk.Label(
            top, text="0 步驟", bg=_C["bg"],
            fg=_C["text_muted"], font=("Segoe UI", 9),
        )
        self._lbl_count.pack(side=tk.RIGHT)
        self._lbl_active = tk.Label(
            top, text="未命名", bg=_C["bg"],
            fg=_C["accent"], font=("Segoe UI", 9, "bold"),
        )
        self._lbl_active.pack(side=tk.LEFT)
        tk.Label(top, text="  雙擊可編輯", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "italic")).pack(side=tk.LEFT)

        # wrapper holds either listbox or empty-state
        self._seq_wrapper = tk.Frame(parent, bg=_C["bg"])
        self._seq_wrapper.pack(fill=tk.BOTH, expand=True, padx=8, pady=(3, 0))

        # empty state
        self._frame_empty = tk.Frame(
            self._seq_wrapper, bg=_C["card"],
            highlightthickness=1, highlightbackground=_C["border"],
        )
        tk.Label(
            self._frame_empty,
            text="尚無步驟\n\n點擊「＋ 新增步驟」開始\n或雙擊列表項目進行編輯",
            bg=_C["card"], fg=_C["text_muted"],
            font=("Segoe UI", 12), justify=tk.CENTER,
        ).pack(expand=True)

        # list state
        self._frame_list = tk.Frame(self._seq_wrapper, bg=_C["bg"])
        self._listbox = tk.Listbox(
            self._frame_list,
            selectmode=tk.SINGLE,
            font=("Consolas", 10),
            bg=_C["card"],
            fg=_C["text"],
            selectbackground=_C["accent"],
            selectforeground="white",
            activestyle="none",
            borderwidth=0,
            highlightthickness=1,
            highlightcolor=_C["accent"],
            highlightbackground=_C["border"],
            relief="flat",
        )
        self._listbox.pack(fill=tk.BOTH, expand=True)
        self._listbox.bind("<Double-Button-1>", self._on_list_dblclick)

        # action buttons — safe actions left, destructive right with visual gap
        btn = tk.Frame(parent, bg=_C["bg"])
        btn.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(btn, text="↑ 上移", style="Ghost.TButton",
                   command=self._move_up).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn, text="↓ 下移", style="Ghost.TButton",
                   command=self._move_down).pack(side=tk.LEFT)
        tk.Frame(btn, bg=_C["border"], width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        ttk.Button(btn, text="✕ 刪除", style="GhostDanger.TButton",
                   command=self._delete_step).pack(side=tk.LEFT, padx=(0, 3))
        ttk.Button(btn, text="⊘ 清空", style="GhostDanger.TButton",
                   command=self._clear_all).pack(side=tk.LEFT)

    def _refresh_list(self) -> None:
        self._listbox.delete(0, tk.END)
        n = len(self._steps)

        for i, step in enumerate(self._steps):
            label = f"  #{i + 1:02d}  {step.display_label()}"
            self._listbox.insert(tk.END, label)
            row_bg = _C["card"] if i % 2 == 0 else "#f8fafc"
            fg = _ACTION_FG.get(step.action_type, _C["text"])
            self._listbox.itemconfig(i, background=row_bg, foreground=fg)

        self._lbl_count.config(text=f"{n} 步驟")

        # toggle empty / list frame
        if n == 0:
            self._frame_list.pack_forget()
            self._frame_empty.pack(fill=tk.BOTH, expand=True)
        else:
            self._frame_empty.pack_forget()
            self._frame_list.pack(fill=tk.BOTH, expand=True)

    def _on_list_dblclick(self, _e: tk.Event) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        idx  = sel[0]
        step = self._steps[idx]
        self._edit_index = idx
        # enable all before setting values
        for w in (self._ent_x, self._ent_y, self._ent_kb):
            w.config(state=tk.NORMAL)
        self._var_action.set(step.action_type)
        self._var_x.set(str(step.x))
        self._var_y.set(str(step.y))
        self._var_count.set(str(step.count))
        self._var_delay.set(str(step.delay))
        self._var_kb.set(step.keyboard_text or "")
        self._apply_action_state()
        self._btn_commit.config(text="✓  更新步驟")
        self._btn_cancel.config(state=tk.NORMAL)

    def _move_up(self) -> None:
        sel = self._listbox.curselection()
        if not sel or sel[0] == 0:
            return
        i = sel[0]
        self._steps[i - 1], self._steps[i] = self._steps[i], self._steps[i - 1]
        self._refresh_list()
        self._listbox.selection_set(i - 1)

    def _move_down(self) -> None:
        sel = self._listbox.curselection()
        if not sel or sel[0] >= len(self._steps) - 1:
            return
        i = sel[0]
        self._steps[i], self._steps[i + 1] = self._steps[i + 1], self._steps[i]
        self._refresh_list()
        self._listbox.selection_set(i + 1)

    def _delete_step(self) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        del self._steps[sel[0]]
        self._refresh_list()

    def _clear_all(self) -> None:
        if not self._steps:
            return
        if messagebox.askyesno("確認清空", "確定要清空所有步驟嗎？"):
            self._steps.clear()
            self._refresh_list()

    # ── execution panel ───────────────────────────────────────────────────────

    def _build_execution_panel(self, parent: ttk.LabelFrame) -> None:
        PX = 10   # consistent horizontal padding throughout this panel

        # rounds config
        tk.Label(parent, text="執行輪數", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "bold")).pack(
            anchor=tk.W, padx=PX, pady=(12, 0))

        row = tk.Frame(parent, bg=_C["bg"])
        row.pack(fill=tk.X, padx=PX, pady=(4, 0))
        self._var_rounds = tk.StringVar(value="1")
        ttk.Entry(row, textvariable=self._var_rounds, width=6).pack(side=tk.LEFT)
        tk.Label(row, text=" 輪", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 10)).pack(side=tk.LEFT)

        self._var_infinite = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="無限循環", variable=self._var_infinite).pack(
            anchor=tk.W, padx=PX, pady=(4, 0))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=PX, pady=8)

        # start / stop
        self._btn_start = ttk.Button(
            parent, text="▶  開始",
            style="Start.TButton", command=self._start_execution,
        )
        self._btn_start.pack(fill=tk.X, padx=PX, pady=(0, 5))

        self._btn_stop = ttk.Button(
            parent, text="■  停止",
            style="Stop.TButton", command=self._stop_execution, state=tk.DISABLED,
        )
        self._btn_stop.pack(fill=tk.X, padx=PX)
        _Tip(self._btn_stop, "按 Space 鍵也可立即停止")

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=PX, pady=8)

        # live status card
        tk.Label(parent, text="即時狀態", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "bold")).pack(
            anchor=tk.W, padx=PX)

        card = tk.Frame(parent, bg=_C["card"],
                        highlightthickness=1, highlightbackground=_C["border"])
        card.pack(fill=tk.X, padx=PX, pady=(5, 0))

        self._var_st_round = tk.StringVar(value="—")
        self._var_st_step  = tk.StringVar(value="—")
        self._var_st_click = tk.StringVar(value="—")

        for icon, label, var in (
            ("↺", "輪次", self._var_st_round),
            ("▶", "步驟", self._var_st_step),
            ("◎", "點擊", self._var_st_click),
        ):
            row = tk.Frame(card, bg=_C["card"])
            row.pack(fill=tk.X, padx=8, pady=5)
            tk.Label(row, text=f"{icon} {label}", bg=_C["card"],
                     fg=_C["text_muted"], font=("Segoe UI", 9),
                     anchor=tk.W).pack(side=tk.LEFT)
            tk.Label(row, textvariable=var, bg=_C["card"],
                     fg=_C["accent"], font=("Segoe UI", 11, "bold"),
                     anchor=tk.E).pack(side=tk.RIGHT, expand=True, fill=tk.X)

    # ── profile bar ───────────────────────────────────────────────────────────

    def _build_profile_bar(self) -> None:
        bar = tk.Frame(self._root, bg=_C["bg_dark"],
                       highlightthickness=1, highlightbackground=_C["border"])
        bar.pack(fill=tk.X, padx=8, pady=(0, 2), side=tk.BOTTOM)

        inner = tk.Frame(bar, bg=_C["bg_dark"], padx=10, pady=6)
        inner.pack(fill=tk.X)

        # ── TOP ROW: save fields + action buttons ─────────────────────────────
        top = tk.Frame(inner, bg=_C["bg_dark"])
        top.pack(fill=tk.X)

        tk.Label(top, text="設定檔", bg=_C["bg_dark"],
                 fg=_C["accent"], font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT, padx=(0, 10))
        tk.Label(top, text="名稱", bg=_C["bg_dark"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._var_prof_name = tk.StringVar()
        ttk.Entry(top, textvariable=self._var_prof_name, width=13).pack(
            side=tk.LEFT, padx=(3, 8))
        tk.Label(top, text="描述", bg=_C["bg_dark"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._var_prof_desc = tk.StringVar()
        ttk.Entry(top, textvariable=self._var_prof_desc, width=18).pack(
            side=tk.LEFT, padx=(3, 0))

        ttk.Button(top, text="儲存", style="Accent.TButton",
                   command=self._save_profile).pack(side=tk.RIGHT)
        tk.Frame(top, bg=_C["border"], width=1).pack(
            side=tk.RIGHT, fill=tk.Y, padx=8, pady=1)
        ttk.Button(top, text="＋ 新增操作", style="Ghost.TButton",
                   command=self._new_operation).pack(side=tk.RIGHT)

        # ── BOTTOM ROW: inline profile list ───────────────────────────────────
        bot = tk.Frame(inner, bg=_C["bg_dark"])
        bot.pack(fill=tk.X, pady=(5, 0))

        tk.Label(bot, text="已儲存操作", bg=_C["bg_dark"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "bold")).pack(
            side=tk.LEFT, anchor=tk.N, padx=(0, 8), pady=(2, 0))

        lb_wrap = tk.Frame(bot, bg=_C["bg_dark"])
        lb_wrap.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sb = ttk.Scrollbar(lb_wrap, orient=tk.VERTICAL)
        self._lb_profiles = tk.Listbox(
            lb_wrap,
            height=3,
            selectmode=tk.SINGLE,
            font=("Segoe UI", 9),
            bg=_C["card"],
            fg=_C["text"],
            selectbackground=_C["accent"],
            selectforeground="white",
            activestyle="none",
            borderwidth=0,
            highlightthickness=1,
            highlightcolor=_C["accent"],
            highlightbackground=_C["border"],
            relief="flat",
            yscrollcommand=sb.set,
        )
        sb.config(command=self._lb_profiles.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._lb_profiles.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._lb_profiles.bind("<ButtonRelease-1>", self._on_lb_profile_select)
        self._lb_profiles.bind("<Double-Button-1>", lambda _e: self._load_selected_profile())

        act = tk.Frame(bot, bg=_C["bg_dark"])
        act.pack(side=tk.LEFT, padx=(8, 0), anchor=tk.N)
        ttk.Button(act, text="載入", style="Accent.TButton",
                   command=self._load_selected_profile).pack(fill=tk.X, pady=(0, 3))
        ttk.Button(act, text="刪除", style="GhostDanger.TButton",
                   command=self._delete_selected_profile).pack(fill=tk.X)

    # ── status bar ────────────────────────────────────────────────────────────

    def _build_status_bar(self) -> None:
        self._var_statusbar = tk.StringVar(value="就緒")
        self._statusbar = tk.Label(
            self._root,
            textvariable=self._var_statusbar,
            bg=_C["sb_idle"],
            fg=_C["text_muted"],
            anchor=tk.W,
            font=("Segoe UI", 9),
            padx=14, pady=5,
        )
        self._statusbar.pack(fill=tk.X, side=tk.BOTTOM)

    # ── callbacks: mouse ──────────────────────────────────────────────────────

    def _on_mouse_move(self, x: int, y: int) -> None:
        self._root.after(0, self._var_mx.set, x)
        self._root.after(0, self._var_my.set, y)

    def _capture_position(self) -> None:
        self._root.after(0, self._do_capture)

    def _do_capture(self) -> None:
        self._var_x.set(str(self._var_mx.get()))
        self._var_y.set(str(self._var_my.get()))

    # ── callbacks: execution ──────────────────────────────────────────────────

    def _start_execution(self) -> None:
        if not self._steps:
            messagebox.showwarning("警告", "請先新增至少一個步驟")
            return
        if self._executor.is_running:
            return
        rounds = 0 if self._var_infinite.get() else self._parse_rounds()
        if rounds is None:
            return
        self._btn_start.config(state=tk.DISABLED)
        self._btn_stop.config(state=tk.NORMAL)
        self._set_status("執行中…", "run")
        self._executor.start(list(self._steps), rounds)

    def _parse_rounds(self) -> Optional[int]:
        try:
            r = int(self._var_rounds.get())
            if r < 1:
                raise ValueError()
            return r
        except ValueError:
            messagebox.showerror("輸入錯誤", "輪數必須為 ≥ 1 的整數")
            return None

    def _stop_execution(self) -> None:
        if self._executor.is_running:
            self._executor.stop()

    def _on_status_update(self, state: ExecutionState) -> None:
        def _up() -> None:
            self._var_st_round.set(str(state.current_round))
            self._var_st_step.set(f"{state.current_step} / {state.total_steps}")
            self._var_st_click.set(f"{state.current_click} / {state.total_clicks}")
            self._set_status(
                f"Round {state.current_round}   │   "
                f"Step {state.current_step}/{state.total_steps}   │   "
                f"Click {state.current_click}/{state.total_clicks}",
                "run",
            )
        self._root.after(0, _up)

    def _on_execution_finished(self) -> None:
        def _up() -> None:
            self._btn_start.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.DISABLED)
            self._var_st_round.set("—")
            self._var_st_step.set("—")
            self._var_st_click.set("—")
            self._set_status("✓  執行完畢", "done")
        self._root.after(0, _up)

    def _on_execution_error(self, msg: str) -> None:
        def _up() -> None:
            self._btn_start.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.DISABLED)
            self._set_status(f"執行錯誤：{msg}", "error")
            messagebox.showerror("執行錯誤", msg)
        self._root.after(0, _up)

    # ── callbacks: profile ────────────────────────────────────────────────────

    def _reload_profile_list(self) -> None:
        try:
            names = self._db.list_profile_names()
        except Exception as exc:
            logger.exception("Failed to list profiles")
            messagebox.showerror("資料庫錯誤", str(exc))
            return
        self._lb_profiles.delete(0, tk.END)
        for n in names:
            self._lb_profiles.insert(tk.END, f"  {n}")
        # re-highlight the active profile
        for i in range(self._lb_profiles.size()):
            if self._lb_profiles.get(i).strip() == self._active_profile:
                self._lb_profiles.selection_set(i)
                break

    def _on_lb_profile_select(self, _e: tk.Event) -> None:
        sel = self._lb_profiles.curselection()
        if sel:
            self._var_prof_name.set(self._lb_profiles.get(sel[0]).strip())

    def _load_selected_profile(self) -> None:
        sel = self._lb_profiles.curselection()
        if not sel:
            messagebox.showwarning("提示", "請先在列表中選取一個操作")
            return
        self._var_prof_name.set(self._lb_profiles.get(sel[0]).strip())
        self._load_profile()

    def _delete_selected_profile(self) -> None:
        sel = self._lb_profiles.curselection()
        if not sel:
            messagebox.showwarning("提示", "請先在列表中選取一個操作")
            return
        name = self._lb_profiles.get(sel[0]).strip()
        if not messagebox.askyesno("確認刪除", f"確定要刪除「{name}」嗎？\n此操作無法復原。"):
            return
        try:
            self._db.delete_profile(name)
            if self._active_profile == name:
                self._active_profile = ""
                self._update_active_label()
            self._reload_profile_list()
            self._set_status(f"已刪除設定檔：{name}", "idle")
        except Exception as exc:
            logger.exception("Failed to delete profile")
            messagebox.showerror("資料庫錯誤", str(exc))

    def _save_profile(self) -> None:
        name = self._var_prof_name.get().strip()
        if not name:
            messagebox.showwarning("警告", "請輸入設定檔名稱")
            return
        if not self._steps:
            messagebox.showwarning("警告", "請先新增至少一個步驟")
            return
        try:
            self._db.save_profile(
                Profile(name=name, description=self._var_prof_desc.get().strip()),
                self._steps,
            )
            self._active_profile = name
            self._update_active_label()
            self._reload_profile_list()
            self._set_status(f"已儲存設定檔：{name}", "idle")
            messagebox.showinfo("儲存成功", f"設定檔「{name}」已儲存")
        except Exception as exc:
            logger.exception("Failed to save profile")
            messagebox.showerror("資料庫錯誤", str(exc))

    def _load_profile(self) -> None:
        name = self._var_prof_name.get().strip() or self._combo_prof.get()
        if not name:
            messagebox.showwarning("警告", "請選擇或輸入設定檔名稱")
            return
        try:
            result = self._db.load_profile(name)
            if result is None:
                messagebox.showwarning("警告", f"找不到設定檔：{name}")
                return
            profile, steps = result
            self._steps = steps
            self._active_profile = profile.name
            self._var_prof_name.set(profile.name)
            self._var_prof_desc.set(profile.description)
            self._refresh_list()
            self._update_active_label()
            self._set_status(f"已載入設定檔：{name}（{len(steps)} 步驟）", "idle")
        except Exception as exc:
            logger.exception("Failed to load profile")
            messagebox.showerror("資料庫錯誤", str(exc))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _update_active_label(self) -> None:
        self._lbl_active.config(
            text=self._active_profile if self._active_profile else "未命名"
        )

    def _new_operation(self) -> None:
        if self._steps:
            if not messagebox.askyesno("新增操作", "目前步驟將被清除。\n確定要開始新的操作嗎？"):
                return
        self._steps.clear()
        self._active_profile = ""
        self._var_prof_name.set("")
        self._var_prof_desc.set("")
        self._lb_profiles.selection_clear(0, tk.END)
        self._exit_edit_mode()
        self._clear_fields()
        self._refresh_list()
        self._update_active_label()
        self._set_status("已開始新操作", "idle")

    def _set_status(self, msg: str, state: str = "idle") -> None:
        _colors = {
            "idle":  (_C["sb_idle"],    _C["text_muted"]),
            "run":   (_C["sb_run"],     _C["success"]),
            "done":  (_C["success_bg"], _C["success_dark"]),
            "error": (_C["sb_error"],   _C["danger"]),
        }
        bg, fg = _colors.get(state, _colors["idle"])
        self._statusbar.config(bg=bg, fg=fg)
        self._var_statusbar.set(msg)

    def on_close(self) -> None:
        self._mouse_tracker.stop()
        self._keyboard_monitor.stop()
        self._executor.stop()
        logger.info("Program Exit")
        self._root.destroy()
