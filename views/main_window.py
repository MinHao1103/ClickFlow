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
from services.recorder import Recorder

logger = logging.getLogger(__name__)

# ── Palette ───────────────────────────────────────────────────────────────────
_C = {
    "bg":            "#0f172a",   # main background — deepest navy
    "bg_dark":       "#334155",   # elevated surfaces (buttons, scrollbar thumb)
    "card":          "#1e293b",   # panel / card background
    "border":        "#475569",   # visible dividers on dark bg
    "text":          "#e2e8f0",   # primary text
    "text_muted":    "#94a3b8",   # secondary / hint text
    "accent":        "#818cf8",   # light indigo — readable on dark
    "accent_dark":   "#6366f1",   # indigo hover
    "success":       "#4ade80",   # bright green
    "success_dark":  "#86efac",   # lighter green (used as fg on dark green bg)
    "success_bg":    "#052e16",   # very dark green (done status bar)
    "danger":        "#f87171",   # soft red
    "danger_dark":   "#dc2626",   # darker red (button hover)
    "danger_bg":     "#450a0a",   # very dark red (ghost hover / record status bar)
    "warning":       "#fbbf24",   # amber
    "purple":        "#c084fc",   # light purple
    "teal":          "#22d3ee",   # cyan
    # status-bar state backgrounds
    "sb_idle":       "#1e293b",
    "sb_run":        "#052e16",
    "sb_error":      "#450a0a",
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
    "image_click":    _C["teal"],
}

_ACTION_TYPES = [
    "click", "double_click", "right_click",
    "move", "delay", "keyboard_input", "hotkey",
    "image_click",
]
_COORD_ACTIONS = {"click", "double_click", "right_click", "move"}
_KB_ACTIONS    = {"keyboard_input", "hotkey"}
_IMG_ACTIONS   = {"image_click"}


# ── Mini execution monitor ────────────────────────────────────────────────────
class _Mini:
    """Always-on-top execution monitor shown while automation runs."""
    W, H = 230, 112

    def __init__(
        self,
        root: tk.Tk,
        var_round: tk.StringVar,
        var_step: tk.StringVar,
        var_click: tk.StringVar,
        on_stop: callable,
    ) -> None:
        self._win = tk.Toplevel(root)
        self._win.wm_attributes("-topmost", True)
        self._win.wm_attributes("-alpha", 0.93)
        self._win.overrideredirect(True)
        self._win.configure(bg=_C["border"])   # thin border via bg colour

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = sw - self.W - 24
        y = sh - self.H - 64
        self._win.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self._dx = self._dy = 0
        self._build(var_round, var_step, var_click, on_stop)

    def _build(self, var_round, var_step, var_click, on_stop) -> None:
        # header — drag handle
        hdr = tk.Frame(self._win, bg=_C["accent"], height=26)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        hdr_lbl = tk.Label(
            hdr, text="⚙  執行中",
            bg=_C["accent"], fg="white", font=("Segoe UI", 8, "bold"),
        )
        hdr_lbl.pack(side=tk.LEFT, padx=10)
        for w in (hdr, hdr_lbl):
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<B1-Motion>", self._drag)

        # stats row
        body = tk.Frame(self._win, bg=_C["card"], padx=10, pady=6)
        body.pack(fill=tk.BOTH, expand=True)

        row = tk.Frame(body, bg=_C["card"])
        row.pack(fill=tk.X, pady=(0, 6))
        for icon, lbl, var in (
            ("↺", "輪", var_round),
            ("▶", "步驟", var_step),
            ("◎", "點擊", var_click),
        ):
            col = tk.Frame(row, bg=_C["card"])
            col.pack(side=tk.LEFT, expand=True)
            tk.Label(col, text=f"{icon} {lbl}", bg=_C["card"],
                     fg=_C["text_muted"], font=("Segoe UI", 7)).pack()
            tk.Label(col, textvariable=var, bg=_C["card"],
                     fg=_C["accent"], font=("Segoe UI", 9, "bold")).pack()

        ttk.Button(body, text="■  停止", style="Stop.TButton",
                   command=on_stop).pack(fill=tk.X)

    def _press(self, e: tk.Event) -> None:
        self._dx = e.x_root - self._win.winfo_x()
        self._dy = e.y_root - self._win.winfo_y()

    def _drag(self, e: tk.Event) -> None:
        self._win.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def destroy(self) -> None:
        self._win.destroy()


# ── Mini recording monitor ────────────────────────────────────────────────────
class _MiniRecorder:
    """Always-on-top recording monitor shown while the Recorder is active."""
    W, H = 300, 340

    def __init__(self, root: tk.Tk, on_stop: callable,
                 on_rect_changed: callable) -> None:
        self._win = tk.Toplevel(root)
        self._win.wm_attributes("-topmost", True)
        self._win.wm_attributes("-alpha", 0.93)
        self._win.overrideredirect(True)
        self._win.configure(bg=_C["border"])

        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        x = sw - self.W - 24
        y = sh - self.H - 64
        self._win.geometry(f"{self.W}x{self.H}+{x}+{y}")

        self._dx = self._dy = 0
        self._on_rect_changed = on_rect_changed
        self._var_count = tk.StringVar(value="0 步驟")
        self._listbox: Optional[tk.Listbox] = None
        self._build(on_stop)

    def _build(self, on_stop) -> None:
        hdr = tk.Frame(self._win, bg=_C["danger"], height=26)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        hdr_lbl = tk.Label(
            hdr, text="●  錄製中",
            bg=_C["danger"], fg="white", font=("Segoe UI", 8, "bold"),
        )
        hdr_lbl.pack(side=tk.LEFT, padx=10)
        for w in (hdr, hdr_lbl):
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<B1-Motion>", self._drag)

        body = tk.Frame(self._win, bg=_C["card"], padx=8, pady=6)
        body.pack(fill=tk.BOTH, expand=True)

        count_row = tk.Frame(body, bg=_C["card"])
        count_row.pack(fill=tk.X, pady=(0, 4))
        tk.Label(count_row, text="已錄製", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(count_row, textvariable=self._var_count, bg=_C["card"],
                 fg=_C["danger"], font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(5, 0))

        lb_wrap = tk.Frame(body, bg=_C["card"])
        lb_wrap.pack(fill=tk.BOTH, expand=True, pady=(0, 6))
        sb = ttk.Scrollbar(lb_wrap, orient=tk.VERTICAL)
        self._listbox = tk.Listbox(
            lb_wrap,
            font=("Consolas", 8),
            bg=_C["card"], fg=_C["text"],
            selectbackground=_C["accent"], selectforeground="white",
            activestyle="none", borderwidth=0,
            highlightthickness=1,
            highlightcolor=_C["border"], highlightbackground=_C["border"],
            relief="flat", yscrollcommand=sb.set,
        )
        sb.config(command=self._listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ttk.Button(body, text="■  停止錄製  [F9]", style="Stop.TButton",
                   command=on_stop).pack(fill=tk.X)

    def update(self, step: ClickStep, total: int) -> None:
        if self._listbox is None:
            return
        self._var_count.set(f"{total} 步驟")
        row_bg = _C["card"] if total % 2 == 1 else "#263548"
        fg = _ACTION_FG.get(step.action_type, _C["text"])
        label = f"  #{total:02d}  {step.display_label().strip()}"
        self._listbox.insert(tk.END, label)
        self._listbox.itemconfig(tk.END, background=_C["success_bg"], foreground=_C["success"])
        self._listbox.see(tk.END)
        idx = self._listbox.size() - 1
        self._win.after(400, lambda: self._reset_row(idx, row_bg, fg))

    def _reset_row(self, idx: int, bg: str, fg: str) -> None:
        if self._listbox and idx < self._listbox.size():
            self._listbox.itemconfig(idx, background=bg, foreground=fg)

    def get_rect(self) -> tuple:
        w = self._win
        return (w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())

    def _press(self, e: tk.Event) -> None:
        self._dx = e.x_root - self._win.winfo_x()
        self._dy = e.y_root - self._win.winfo_y()

    def _drag(self, e: tk.Event) -> None:
        nx = e.x_root - self._dx
        ny = e.y_root - self._dy
        self._win.geometry(f"+{nx}+{ny}")
        self._on_rect_changed(nx, ny, self.W, self.H)

    def destroy(self) -> None:
        self._listbox = None
        self._win.destroy()


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
            bg=_C["bg_dark"], fg=_C["text"],
            relief="solid", bd=1,
            font=("Segoe UI", 9), padx=7, pady=3,
        ).pack()

    def _hide(self, _e=None) -> None:
        if self._win:
            self._win.destroy()
            self._win = None


# ── Region selector overlay ───────────────────────────────────────────────────
class _RegionSelector:
    """Full-screen screenshot overlay: user drags to select a region to save as reference image."""

    def __init__(self, root: tk.Tk, save_dir: str, on_done: callable) -> None:
        from PIL import Image, ImageTk
        import pyautogui as _pag
        import os as _os, time as _time

        self._on_done  = on_done
        self._save_dir = save_dir

        # Capture full screen BEFORE the overlay appears
        self._shot = _pag.screenshot()

        # Calculate DPI scale (physical px vs logical px)
        lw = root.winfo_screenwidth()
        lh = root.winfo_screenheight()
        self._scale_x = self._shot.width  / lw
        self._scale_y = self._shot.height / lh

        # Resize screenshot to logical resolution for display
        display = self._shot.resize((lw, lh), Image.LANCZOS)
        self._img_tk = ImageTk.PhotoImage(display)

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.wm_attributes("-topmost", True)
        self._win.geometry(f"{lw}x{lh}+0+0")

        cv = tk.Canvas(self._win, width=lw, height=lh,
                       cursor="crosshair", highlightthickness=0, bg="black")
        cv.pack()
        cv.create_image(0, 0, anchor=tk.NW, image=self._img_tk)
        cv.create_rectangle(0, 0, lw, lh, fill="black", stipple="gray50", outline="")
        tk.Label(self._win,
                 text="拖曳選取目標區域   |   Esc 取消",
                 bg="#0f172a", fg="#94a3b8",
                 font=("Segoe UI", 12, "bold")).place(x=lw // 2, y=16, anchor="n")

        self._cv   = cv
        self._rect = None
        self._sx = self._sy = 0

        cv.bind("<ButtonPress-1>",   self._press)
        cv.bind("<B1-Motion>",       self._drag)
        cv.bind("<ButtonRelease-1>", self._release)
        self._win.bind("<Escape>",   lambda _: self._cancel())

    def _press(self, e: tk.Event) -> None:
        self._sx, self._sy = e.x, e.y
        if self._rect:
            self._cv.delete(self._rect)
        self._rect = self._cv.create_rectangle(
            e.x, e.y, e.x, e.y, outline=_C["accent"], width=2)

    def _drag(self, e: tk.Event) -> None:
        if self._rect:
            self._cv.coords(self._rect, self._sx, self._sy, e.x, e.y)

    def _release(self, e: tk.Event) -> None:
        import os as _os, time as _time
        x1 = int(min(self._sx, e.x) * self._scale_x)
        y1 = int(min(self._sy, e.y) * self._scale_y)
        x2 = int(max(self._sx, e.x) * self._scale_x)
        y2 = int(max(self._sy, e.y) * self._scale_y)
        self._win.destroy()

        if x2 - x1 < 8 or y2 - y1 < 8:
            self._on_done(None)
            return

        cropped  = self._shot.crop((x1, y1, x2, y2))
        _os.makedirs(self._save_dir, exist_ok=True)
        filename = f"img_{int(_time.time() * 1000)}.png"
        path     = _os.path.join(self._save_dir, filename)
        cropped.save(path)
        self._on_done(path)

    def _cancel(self) -> None:
        self._win.destroy()
        self._on_done(None)


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
        self._recorder: Optional[Recorder] = None
        self._mini: Optional[_Mini] = None
        self._mini_rec: Optional[_MiniRecorder] = None
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

        # Combobox dropdown listbox inherits OS defaults; override for dark theme
        self._root.option_add("*TCombobox*Listbox*foreground",       _C["text"])
        self._root.option_add("*TCombobox*Listbox*background",       _C["card"])
        self._root.option_add("*TCombobox*Listbox*selectBackground", _C["accent"])
        self._root.option_add("*TCombobox*Listbox*selectForeground", "white")

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
            font=("Segoe UI", 11, "bold"),
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
            background=[("active", _C["accent_dark"]), ("disabled", "#3730a3")],
            foreground=[("disabled", "#a5b4fc")],
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
            background=[("active", _C["success_dark"]), ("disabled", "#166534")],
            foreground=[("disabled", "#4ade80")],
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

        s.configure("Record.TButton",
            background=_C["danger"],
            foreground="white",
            bordercolor=_C["danger"],
            font=("Segoe UI", 10, "bold"),
            padding=(8, 5),
            relief="flat",
        )
        s.map("Record.TButton",
            background=[("active", _C["danger_dark"]), ("disabled", _C["bg_dark"])],
            foreground=[("disabled", _C["text_muted"])],
            relief=[("active", "flat")],
        )

    # ── window ────────────────────────────────────────────────────────────────

    def _build_window(self) -> None:
        self._root.title("Automation Script Engine")
        self._root.geometry("1280x960")
        self._root.minsize(960, 720)
        self._root.resizable(True, True)
        self._root.configure(bg=_C["bg"])

    # ── top-level layout ──────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self._build_tracker_bar()

        # Status bar at very bottom (pack before mid so it claims its space first)
        self._build_status_bar()

        mid = ttk.Frame(self._root)
        mid.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 4))

        # LEFT — step editor, fixed width
        left = ttk.LabelFrame(mid, text="  步驟編輯器", width=285)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 4))
        left.pack_propagate(False)
        self._build_step_editor(left)

        # RIGHT — control panel (recording + execution + profile), fixed width
        right = ttk.LabelFrame(mid, text="  控制台", width=275)
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
        tk.Label(inner, text="🖱  即時座標", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 14))

        # X
        tk.Label(inner, text="X", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._var_mx = tk.IntVar(value=0)
        tk.Label(inner, textvariable=self._var_mx, bg=_C["card"],
                 fg=_C["accent"], font=("Segoe UI", 19, "bold"),
                 width=5, anchor=tk.E).pack(side=tk.LEFT)

        # Y
        tk.Label(inner, text="Y", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(14, 4))
        self._var_my = tk.IntVar(value=0)
        tk.Label(inner, textvariable=self._var_my, bg=_C["card"],
                 fg=_C["accent"], font=("Segoe UI", 19, "bold"),
                 width=5, anchor=tk.E).pack(side=tk.LEFT)

        # divider
        tk.Frame(inner, bg=_C["border"], width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=18, pady=2)

        # capture button
        btn = ttk.Button(inner, text="⊕  擷取座標",
                         style="Accent.TButton", command=self._capture_position)
        btn.pack(side=tk.LEFT)
        _Tip(btn, "點擊按鈕或按 S 鍵，將目前游標位置填入步驟編輯器")

        tk.Label(inner, text="  ← S 鍵快速觸發", bg=_C["card"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "italic")).pack(side=tk.LEFT, padx=6)

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
        self._var_x.trace_add("write", lambda *_: self._auto_norm(self._var_x))
        self._ent_x = self._numeric_entry(row, self._var_x, width=7)
        self._ent_x.pack(side=tk.LEFT, padx=(3, 14))

        tk.Label(row, text="Y", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9), width=2).pack(side=tk.LEFT)
        self._var_y = tk.StringVar()
        self._var_y.trace_add("write", lambda *_: self._auto_norm(self._var_y))
        self._ent_y = self._numeric_entry(row, self._var_y, width=7)
        self._ent_y.pack(side=tk.LEFT, padx=(3, 0))

        self._mk_divider(parent)

        # ── Count ──
        self._mk_field_label(parent, "執行次數")
        row_cnt = tk.Frame(parent, bg=_C["bg"])
        row_cnt.pack(fill=tk.X, padx=10, pady=(2, 6))
        tk.Label(row_cnt, text="×", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 10), width=2).pack(side=tk.LEFT)
        self._var_count = tk.StringVar(value="1")
        self._var_count.trace_add("write", lambda *_: self._auto_norm(self._var_count))
        self._numeric_entry(row_cnt, self._var_count, width=8).pack(side=tk.LEFT, padx=(3, 4))
        tk.Label(row_cnt, text="次", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)

        self._mk_divider(parent)

        # ── Delay ──
        self._mk_field_label(parent, "步驟延遲")
        row_dly = tk.Frame(parent, bg=_C["bg"])
        row_dly.pack(fill=tk.X, padx=10, pady=(2, 6))
        tk.Label(row_dly, text="⏱", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 10), width=2).pack(side=tk.LEFT)
        self._var_delay = tk.StringVar(value="0")
        self._var_delay.trace_add("write", lambda *_: self._auto_norm(self._var_delay))
        self._numeric_entry(row_dly, self._var_delay, width=8).pack(
            side=tk.LEFT, padx=(3, 4))
        tk.Label(row_dly, text="秒", bg=_C["bg"], fg=_C["text_muted"],
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

        # ── Image click params (hidden until image_click is selected) ──
        self._frm_img = tk.Frame(parent, bg=_C["bg"])
        # Not packed yet; _apply_action_state controls visibility

        self._var_img_path = tk.StringVar(value="")
        path_row = tk.Frame(self._frm_img, bg=_C["bg"])
        path_row.pack(fill=tk.X, padx=10, pady=(6, 2))
        tk.Label(path_row, text="圖片", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9), width=4, anchor=tk.W).pack(side=tk.LEFT)
        self._lbl_img_name = tk.Label(
            path_row, textvariable=self._var_img_path,
            bg=_C["bg"], fg=_C["accent"], font=("Consolas", 8),
            anchor=tk.W, wraplength=160)
        self._lbl_img_name.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(self._frm_img, text="📷  截取區域",
                   style="Accent.TButton",
                   command=self._capture_region).pack(
            fill=tk.X, padx=10, pady=(0, 4))

        self._lbl_img_preview = tk.Label(self._frm_img, bg=_C["card"],
                                          relief="flat", borderwidth=0)
        self._lbl_img_preview.pack(padx=10, pady=(0, 4))
        self._img_preview_tk = None

        conf_row = tk.Frame(self._frm_img, bg=_C["bg"])
        conf_row.pack(fill=tk.X, padx=10, pady=(0, 2))
        tk.Label(conf_row, text="相似度", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._var_conf = tk.StringVar(value="0.85")
        self._numeric_entry(conf_row, self._var_conf, width=5).pack(
            side=tk.LEFT, padx=(6, 0))
        tk.Label(conf_row, text="（0.1–1.0）", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(4, 0))

        tout_row = tk.Frame(self._frm_img, bg=_C["bg"])
        tout_row.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(tout_row, text="超時", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._var_timeout = tk.StringVar(value="10")
        self._numeric_entry(tout_row, self._var_timeout, width=5).pack(
            side=tk.LEFT, padx=(6, 0))
        tk.Label(tout_row, text="秒（找不到圖片則報錯）", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(4, 0))

        # ── Buttons ──
        self._btn_area = tk.Frame(parent, bg=_C["bg"])
        btn_area = self._btn_area
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
                 fg=_C["text_muted"], font=("Segoe UI", 9, "bold"),
                 ).pack(anchor=tk.W, padx=10, pady=(10, 0))

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
        if action in _IMG_ACTIONS:
            self._frm_img.pack(fill=tk.X, before=self._btn_area)
        else:
            self._frm_img.pack_forget()

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

    @staticmethod
    def _norm(s: str) -> str:
        return s.strip().replace("。", ".").replace("．", ".").replace("，", ",")

    def _auto_norm(self, var: tk.StringVar) -> None:
        val = var.get()
        new_val = val.replace("。", ".").replace("．", ".")
        if new_val != val:
            var.set(new_val)

    @staticmethod
    def _disable_ime(widget: tk.Widget) -> None:
        """Disassociate Windows IME from a widget so numeric fields receive raw ASCII."""
        try:
            import ctypes
            hwnd = int(widget.winfo_id())
            if hwnd:
                ctypes.windll.imm32.ImmAssociateContext(hwnd, 0)
        except Exception:
            pass

    def _numeric_entry(self, parent: tk.Widget, textvariable: tk.StringVar,
                       **kwargs) -> ttk.Entry:
        """ttk.Entry with IME disabled — for X/Y/count/delay/rounds fields."""
        e = ttk.Entry(parent, textvariable=textvariable, **kwargs)
        e.bind("<Map>", lambda _evt, w=e: self._disable_ime(w))
        return e

    def _parse_step(self) -> ClickStep:
        action = self._var_action.get()
        x, y = 0, 0
        if action in _COORD_ACTIONS:
            try:
                x = int(self._norm(self._var_x.get()))
                y = int(self._norm(self._var_y.get()))
            except ValueError:
                raise ValueError("X / Y 必須為整數")
        try:
            count = int(self._norm(self._var_count.get()))
            if count < 1:
                raise ValueError()
        except ValueError:
            raise ValueError("Count 必須為 ≥ 1 的整數")
        try:
            delay = float(self._norm(self._var_delay.get()))
            if delay < 0:
                raise ValueError()
        except ValueError:
            raise ValueError("Delay 必須為 ≥ 0 的數值")
        kb = self._var_kb.get().strip() or None

        if action in _IMG_ACTIONS:
            import json as _json
            path = self._var_img_path.get().strip()
            if not path:
                raise ValueError("請先按「截取區域」選取參考圖片")
            import os as _os
            if not _os.path.isfile(path):
                raise ValueError(f"圖片檔案不存在：{path}")
            try:
                conf = float(self._norm(self._var_conf.get()) or "0.85")
                conf = max(0.1, min(1.0, conf))
            except ValueError:
                raise ValueError("相似度請輸入 0.1–1.0 之間的數字")
            try:
                timeout = float(self._norm(self._var_timeout.get()) or "10")
                if timeout <= 0:
                    raise ValueError()
            except ValueError:
                raise ValueError("超時秒數請輸入大於 0 的數字")
            return ClickStep(
                action_type="image_click",
                delay=delay,
                extra_json=_json.dumps(
                    {"path": path, "confidence": conf, "timeout": timeout},
                    ensure_ascii=False,
                ),
            )

        return ClickStep(x=x, y=y, count=count, delay=delay,
                         action_type=action, keyboard_text=kb)

    def _clear_fields(self) -> None:
        self._var_action.set("click")
        self._var_x.set("")
        self._var_y.set("")
        self._var_count.set("1")
        self._var_delay.set("0")
        self._var_kb.set("")
        self._var_img_path.set("")
        self._var_conf.set("0.85")
        self._var_timeout.set("10")
        self._lbl_img_preview.config(image="")
        self._img_preview_tk = None
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
            font=("Consolas", 11),
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
            label = f"   #{i + 1:02d}   {step.display_label()}"
            self._listbox.insert(tk.END, label)
            row_bg = _C["card"] if i % 2 == 0 else "#263548"
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
        if step.action_type in _IMG_ACTIONS:
            import json as _json
            p = _json.loads(step.extra_json or "{}")
            self._var_img_path.set(p.get("path", ""))
            self._var_conf.set(str(p.get("confidence", 0.85)))
            self._var_timeout.set(str(p.get("timeout", 10)))
            if p.get("path"):
                self._update_img_preview(p["path"])
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
        PX = 10

        # ── section helper ────────────────────────────────────────────────────
        def _sec_header(dot_color: str, title: str, right_widget_fn=None):
            hdr = tk.Frame(parent, bg=_C["bg"])
            hdr.pack(fill=tk.X, padx=PX, pady=(10, 4))
            tk.Label(hdr, text="●", bg=_C["bg"],
                     fg=dot_color, font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(hdr, text=title, bg=_C["bg"],
                     fg=_C["text"], font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            if right_widget_fn:
                right_widget_fn(hdr)

        # ── 1. 錄製操作 ───────────────────────────────────────────────────────
        _sec_header(_C["danger"], "錄製操作")

        self._btn_record_start = ttk.Button(
            parent, text="●  開始錄製",
            style="Record.TButton", command=self._start_recording,
        )
        self._btn_record_start.pack(fill=tk.X, padx=PX, pady=(0, 2))

        self._btn_record_stop = ttk.Button(
            parent, text="■  停止錄製  [F9]",
            style="Ghost.TButton", command=self._stop_recording, state=tk.DISABLED,
        )
        self._btn_record_stop.pack(fill=tk.X, padx=PX, pady=(0, 4))

        # Compact options row: checkbox left, delay right
        opt = tk.Frame(parent, bg=_C["bg"])
        opt.pack(fill=tk.X, padx=PX, pady=(0, 3))
        self._var_record_move = tk.BooleanVar(value=False)
        ttk.Checkbutton(opt, text="錄製移動",
                        variable=self._var_record_move).pack(side=tk.LEFT)
        tk.Label(opt, text="秒", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.RIGHT)
        self._var_max_delay = tk.StringVar(value="5.0")
        self._var_max_delay.trace_add("write", lambda *_: self._auto_norm(self._var_max_delay))
        self._numeric_entry(opt, self._var_max_delay, width=4).pack(
            side=tk.RIGHT, padx=(2, 3))
        tk.Label(opt, text="上限", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.RIGHT)

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(2, 0))

        # ── 2. 執行控制 ───────────────────────────────────────────────────────
        _sec_header(_C["success"], "執行控制")

        # Rounds row
        rnd = tk.Frame(parent, bg=_C["bg"])
        rnd.pack(fill=tk.X, padx=PX, pady=(0, 5))
        self._var_rounds = tk.StringVar(value="1")
        self._var_rounds.trace_add("write", lambda *_: self._auto_norm(self._var_rounds))
        self._numeric_entry(rnd, self._var_rounds, width=5).pack(side=tk.LEFT)
        tk.Label(rnd, text=" 輪", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._var_infinite = tk.BooleanVar(value=False)
        ttk.Checkbutton(rnd, text="無限循環",
                        variable=self._var_infinite).pack(side=tk.RIGHT)

        self._btn_start = ttk.Button(
            parent, text="▶  開始",
            style="Start.TButton", command=self._start_execution,
        )
        self._btn_start.pack(fill=tk.X, padx=PX, pady=(0, 3))

        self._btn_stop = ttk.Button(
            parent, text="■  停止",
            style="Stop.TButton", command=self._stop_execution, state=tk.DISABLED,
        )
        self._btn_stop.pack(fill=tk.X, padx=PX)
        _Tip(self._btn_stop, "按 Space 鍵也可立即停止")

        # Compact live-status strip (3 columns in one row)
        self._var_st_round = tk.StringVar(value="—")
        self._var_st_step  = tk.StringVar(value="—")
        self._var_st_click = tk.StringVar(value="—")

        stat = tk.Frame(parent, bg=_C["card"],
                        highlightthickness=1, highlightbackground=_C["border"])
        stat.pack(fill=tk.X, padx=PX, pady=(5, 3))
        for icon, lbl, var in (
            ("↺", "輪", self._var_st_round),
            ("▶", "步", self._var_st_step),
            ("◎", "點", self._var_st_click),
        ):
            col = tk.Frame(stat, bg=_C["card"])
            col.pack(side=tk.LEFT, expand=True, fill=tk.X, pady=4, padx=2)
            tk.Label(col, text=f"{icon} {lbl}", bg=_C["card"],
                     fg=_C["text_muted"], font=("Segoe UI", 8)).pack()
            tk.Label(col, textvariable=var, bg=_C["card"],
                     fg=_C["accent"], font=("Segoe UI", 9, "bold")).pack()

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(2, 0))

        # ── 3. 設定檔 ─────────────────────────────────────────────────────────
        def _add_new_btn(hdr):
            ttk.Button(hdr, text="＋ 新增", style="Ghost.TButton",
                       command=self._new_operation).pack(side=tk.RIGHT)

        _sec_header(_C["accent"], "設定檔", _add_new_btn)

        # Name / desc stacked
        for lbl_text, var_attr in (("名稱", "_var_prof_name"), ("描述", "_var_prof_desc")):
            setattr(self, var_attr, tk.StringVar())
            row = tk.Frame(parent, bg=_C["bg"])
            row.pack(fill=tk.X, padx=PX, pady=(0, 3))
            tk.Label(row, text=lbl_text, bg=_C["bg"], fg=_C["text_muted"],
                     font=("Segoe UI", 8), width=3, anchor=tk.W).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=getattr(self, var_attr)).pack(
                side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(parent, text="💾  儲存設定檔", style="Accent.TButton",
                   command=self._save_profile).pack(fill=tk.X, padx=PX, pady=(2, 4))

        # Pack action buttons at the bottom FIRST so they always have space
        act = tk.Frame(parent, bg=_C["bg"])
        act.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(3, 8))
        ttk.Button(act, text="載入", style="Accent.TButton",
                   command=self._load_selected_profile).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        ttk.Button(act, text="刪除", style="GhostDanger.TButton",
                   command=self._delete_selected_profile).pack(
            side=tk.LEFT, expand=True, fill=tk.X)

        tk.Label(parent, text="已儲存操作", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "bold")).pack(
            anchor=tk.W, padx=PX, pady=(0, 2))

        lb_wrap = tk.Frame(parent, bg=_C["bg"])
        lb_wrap.pack(fill=tk.BOTH, expand=True, padx=PX, pady=(0, 3))
        sb = ttk.Scrollbar(lb_wrap, orient=tk.VERTICAL)
        self._lb_profiles = tk.Listbox(
            lb_wrap, height=4,
            selectmode=tk.SINGLE,
            font=("Segoe UI", 9),
            bg=_C["card"], fg=_C["text"],
            selectbackground=_C["accent"], selectforeground="white",
            activestyle="none", borderwidth=0,
            highlightthickness=1, highlightcolor=_C["accent"],
            highlightbackground=_C["border"], relief="flat",
            yscrollcommand=sb.set,
        )
        sb.config(command=self._lb_profiles.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._lb_profiles.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._lb_profiles.bind("<ButtonRelease-1>", self._on_lb_profile_select)
        self._lb_profiles.bind("<Double-Button-1>", lambda _e: self._load_selected_profile())

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

    def _capture_region(self) -> None:
        def on_done(path: Optional[str]) -> None:
            if not path:
                return
            self._var_img_path.set(path)
            self._update_img_preview(path)
        _RegionSelector(self._root, save_dir="images", on_done=on_done)

    def _update_img_preview(self, path: str) -> None:
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            img.thumbnail((220, 80))
            self._img_preview_tk = ImageTk.PhotoImage(img)
            self._lbl_img_preview.config(image=self._img_preview_tk)
        except Exception:
            self._lbl_img_preview.config(image="")
            self._img_preview_tk = None

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
        self._btn_record_start.config(state=tk.DISABLED)
        self._btn_record_stop.config(state=tk.DISABLED)
        self._set_status("執行中…", "run")
        self._executor.start(list(self._steps), rounds)
        self._show_mini()

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

    def _show_mini(self) -> None:
        self._root.withdraw()
        self._mini = _Mini(
            self._root,
            self._var_st_round,
            self._var_st_step,
            self._var_st_click,
            self._stop_execution,
        )

    def _close_mini(self) -> None:
        if self._mini:
            self._mini.destroy()
            self._mini = None
        self._root.deiconify()

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
            self._close_mini()
            self._btn_start.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.DISABLED)
            self._btn_record_start.config(state=tk.NORMAL)
            self._btn_record_stop.config(state=tk.DISABLED)
            self._var_st_round.set("—")
            self._var_st_step.set("—")
            self._var_st_click.set("—")
            self._set_status("✓  執行完畢", "done")
        self._root.after(0, _up)

    def _on_execution_error(self, msg: str) -> None:
        def _up() -> None:
            self._close_mini()
            self._btn_start.config(state=tk.NORMAL)
            self._btn_stop.config(state=tk.DISABLED)
            self._btn_record_start.config(state=tk.NORMAL)
            self._btn_record_stop.config(state=tk.DISABLED)
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
        name = self._var_prof_name.get().strip()
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

    # ── callbacks: recording ──────────────────────────────────────────────────

    def _start_recording(self) -> None:
        if self._steps:
            if not messagebox.askyesno("開始錄製", "目前步驟將被清除。\n確定要開始錄製嗎？"):
                return
            self._steps.clear()
            self._refresh_list()

        try:
            max_delay = float(self._norm(self._var_max_delay.get()))
        except ValueError:
            messagebox.showerror("輸入錯誤", "延遲上限必須為有效數字")
            return

        x = self._root.winfo_rootx()
        y = self._root.winfo_rooty()
        w = self._root.winfo_width()
        h = self._root.winfo_height()

        self._keyboard_monitor.stop()

        self._recorder = Recorder(
            on_step=self._on_step_recorded,
            on_stopped=lambda: self._root.after(0, self._on_recording_stopped),
            app_rect=(x, y, w, h),
            max_delay=max_delay,
            record_move=self._var_record_move.get(),
        )
        self._recorder.start()

        self._btn_record_start.config(state=tk.DISABLED)
        self._btn_record_stop.config(state=tk.NORMAL)
        self._btn_start.config(state=tk.DISABLED)
        self._btn_stop.config(state=tk.DISABLED)
        self._set_status("●  錄製中…", "record")
        self._show_mini_recorder()

    def _stop_recording(self) -> None:
        if self._recorder and self._recorder.is_recording:
            self._recorder.stop()

    def _show_mini_recorder(self) -> None:
        self._root.withdraw()
        self._mini_rec = _MiniRecorder(
            self._root,
            on_stop=self._stop_recording,
            on_rect_changed=self._update_recorder_rect,
        )
        # Sync rect once after the window has rendered
        self._root.after(150, lambda: self._update_recorder_rect(
            *self._mini_rec.get_rect()) if self._mini_rec else None)

    def _update_recorder_rect(self, x: int, y: int, w: int, h: int) -> None:
        if self._recorder:
            self._recorder._app_rect = (x, y, w, h)

    def _close_mini_recorder(self) -> None:
        if self._mini_rec:
            self._mini_rec.destroy()
            self._mini_rec = None
        self._root.deiconify()

    def _on_step_recorded(self, step: ClickStep) -> None:
        def _up(s=step):
            self._steps.append(s)
            self._refresh_list()
            if self._mini_rec:
                self._mini_rec.update(s, len(self._steps))
        self._root.after(0, _up)

    def _on_recording_stopped(self) -> None:
        self._close_mini_recorder()
        self._keyboard_monitor.start()
        self._btn_record_start.config(state=tk.NORMAL)
        self._btn_record_stop.config(state=tk.DISABLED)
        self._btn_start.config(state=tk.NORMAL)
        self._btn_stop.config(state=tk.DISABLED)
        self._set_status(f"✓  錄製完成，共 {len(self._steps)} 步驟", "done")

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
            "idle":   (_C["sb_idle"],    _C["text_muted"]),
            "run":    (_C["sb_run"],     _C["success"]),
            "done":   (_C["success_bg"], _C["success_dark"]),
            "error":  (_C["sb_error"],   _C["danger"]),
            "record": (_C["danger_bg"],  _C["danger"]),
        }
        bg, fg = _colors.get(state, _colors["idle"])
        self._statusbar.config(bg=bg, fg=fg)
        self._var_statusbar.set(msg)

    def on_close(self) -> None:
        self._mouse_tracker.stop()
        self._keyboard_monitor.stop()
        self._executor.stop()
        if self._mini:
            self._mini.destroy()
            self._mini = None
        if self._mini_rec:
            self._mini_rec.destroy()
            self._mini_rec = None
        if self._recorder and self._recorder.is_recording:
            self._recorder.stop()
        logger.info("Program Exit")
        self._root.destroy()
