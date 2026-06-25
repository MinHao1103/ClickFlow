import tkinter as tk
from tkinter import ttk, messagebox
import logging
import os
import sys
import threading
from typing import List, Optional

from models.click_step import ClickStep
from models.profile import Profile
from models.scene_rule import SceneRule
from services.database_manager import DatabaseManager
from services.scene_runner import SceneRunner
from services.click_executor import ClickExecutor, ExecutionState
from services.keyboard_monitor import KeyboardMonitor
from services.mouse_tracker import MouseTracker
from services.recorder import Recorder

logger = logging.getLogger(__name__)


def _app_dir() -> str:
    """Return the directory that contains app data (images/, etc.).
    Both the exe and dev mode use the same dist/ folder so there is
    only one copy of images on disk.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # dev: dist/ lives next to the project root
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "dist")


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
    "drag":           _C["warning"],
}

_ACTION_TYPES = [
    "click", "double_click", "right_click",
    "move", "delay", "keyboard_input", "hotkey",
    "image_click", "drag",
]
_COORD_ACTIONS = {"click", "double_click", "right_click", "move"}
_KB_ACTIONS    = {"keyboard_input", "hotkey"}
_IMG_ACTIONS   = {"image_click"}
_DRAG_ACTIONS  = {"drag"}


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
    """Screenshot overlay for region selection.

    confine_rect: (x, y, w, h) in logical screen coords — overlay is limited to
    that window area.  None → full-screen overlay (original behaviour).
    """

    def __init__(self, root: tk.Tk, save_dir: str, on_done: callable,
                 confine_rect: tuple | None = None) -> None:
        from PIL import Image, ImageTk
        import pyautogui as _pag
        import os as _os, time as _time

        self._on_done  = on_done
        self._save_dir = save_dir

        if confine_rect:
            cx, cy, cw, ch = confine_rect
            # Capture only the window area
            self._shot = _pag.screenshot(region=(cx, cy, cw, ch))
            lw, lh = cw, ch
            win_geom = f"{cw}x{ch}+{cx}+{cy}"
        else:
            cx = cy = 0
            # Capture full screen BEFORE the overlay appears
            self._shot = _pag.screenshot()
            lw = root.winfo_screenwidth()
            lh = root.winfo_screenheight()
            win_geom = f"{lw}x{lh}+0+0"

        # DPI scale: physical px vs logical px
        self._scale_x = self._shot.width  / lw
        self._scale_y = self._shot.height / lh

        # Resize screenshot to logical resolution for display
        display = self._shot.resize((lw, lh), Image.LANCZOS)
        self._img_tk = ImageTk.PhotoImage(display)

        self._win = tk.Toplevel(root)
        self._win.overrideredirect(True)
        self._win.wm_attributes("-topmost", True)
        self._win.geometry(win_geom)

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
        # logical (display) coords — used by pyautogui for mouse control
        lx1 = int(min(self._sx, e.x))
        ly1 = int(min(self._sy, e.y))
        # physical pixel coords — used for image cropping
        x1 = int(lx1 * self._scale_x)
        y1 = int(ly1 * self._scale_y)
        x2 = int(max(self._sx, e.x) * self._scale_x)
        y2 = int(max(self._sy, e.y) * self._scale_y)
        self._win.destroy()

        if x2 - x1 < 8 or y2 - y1 < 8:
            self._on_done(None, 0, 0)
            return

        cropped  = self._shot.crop((x1, y1, x2, y2))
        _os.makedirs(self._save_dir, exist_ok=True)
        filename = f"img_{int(_time.time() * 1000)}.png"
        path     = _os.path.join(self._save_dir, filename)
        cropped.save(path)
        self._on_done(path, lx1, ly1)

    def _cancel(self) -> None:
        self._win.destroy()
        self._on_done(None, 0, 0)


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
            on_orb_solve=self._orb_execute,
        )

        # ── Tab 3 scene-runner state ─────────────────────────────────────────
        self._scene_rules: List[SceneRule] = []
        self._scene_runner: Optional[SceneRunner] = None
        self._scene_sel: Optional[int] = None      # selected rule index
        self._scene_preview_photo = None           # keep PhotoImage alive
        self._tab3_win: dict = {"hwnd": None, "title": None, "ref": (0, 0), "map": {}}
        self._scene_profile: str = "摩靈傳說"      # currently loaded profile name

        # ── Orb config (shared by Tab 2 and Tab 3, loaded once from DB) ──────
        self._orb_config      = self._db.load_orb_config("default")
        self._orb_board_img   = None
        self._orb_executor    = None
        self._orb_loop_active = False
        self._orb_loop_after  = None
        self._scene_lbl_orb   = None   # Tab 3 calibration label (set during build)

        self._apply_styles()
        self._build_window()
        self._build_ui()
        self._apply_action_state()
        self._refresh_list()          # show empty-state immediately
        self._reload_profile_list()
        try:
            self._scene_rules = self._db.load_scene_rules(self._scene_profile)
            if not self._scene_rules:
                # First run — populate default profile with 摩靈 preset
                self._scene_load_tos_preset("摩靈傳說")
            # Ensure the click-only profile exists and has the example rule at top
            profiles = self._db.list_scene_profile_names()
            click_rules = self._db.load_scene_rules("按鈕點擊") if "按鈕點擊" in profiles else []
            needs_reload = (not click_rules or not click_rules[0].name.startswith("📌"))
            if needs_reload:
                saved = self._scene_profile
                saved_rules = list(self._scene_rules)
                self._scene_load_click_preset("按鈕點擊")
                # Restore active profile state after seeding
                self._scene_profile = saved
                self._scene_rules = saved_rules
                self._scene_refresh_profiles()
                self._scene_refresh_list()
        except Exception:
            pass
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
        s.configure("TNotebook",
            background=_C["bg"],
            bordercolor=_C["border"],
            tabmargins=[2, 4, 0, 0],
        )
        s.configure("TNotebook.Tab",
            background=_C["card"],
            foreground=_C["text_muted"],
            padding=[14, 7],
            font=("Segoe UI", 10),
        )
        s.map("TNotebook.Tab",
            background=[("selected", _C["bg"]), ("active", _C["bg_dark"])],
            foreground=[("selected", _C["accent"]), ("active", _C["text"])],
        )
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
        self._build_status_bar()

        # Window-binding state for each tab (mutable dicts passed by reference)
        # hwnd is a fast-path cache; title is used to re-locate if hwnd becomes stale
        self._tab1_win: dict = {"hwnd": None, "title": None, "ref": (0, 0), "map": {}}
        self._tab2_win: dict = {"hwnd": None, "title": None, "ref": (0, 0), "map": {}}

        self._nb = ttk.Notebook(self._root)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 4))

        # ── Tab 1: 自動化 ──────────────────────────────────────────────────────
        tab1 = ttk.Frame(self._nb)
        self._nb.add(tab1, text="  🤖  自動化  ")
        self._build_automation_tab(tab1)

        # ── Tab 2: 轉珠 ────────────────────────────────────────────────────────
        tab2 = ttk.Frame(self._nb)
        self._nb.add(tab2, text="  🔮  轉珠  ")
        self._build_orb_tab(tab2)

        # ── Tab 3: 場景腳本 ─────────────────────────────────────────────────
        tab3 = ttk.Frame(self._nb)
        self._nb.add(tab3, text="  🎮  場景腳本  ")
        self._build_scene_tab(tab3)

    def _build_automation_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        mid = ttk.Frame(parent)
        mid.grid(row=0, column=0, sticky="nsew", padx=8, pady=(4, 4))
        mid.rowconfigure(0, weight=1)
        # Three columns: left(1) + center(2) + right(1) — proportional, all scalable
        mid.columnconfigure(0, weight=1, minsize=240)
        mid.columnconfigure(1, weight=2, minsize=160)
        mid.columnconfigure(2, weight=1, minsize=230)

        left = ttk.LabelFrame(mid, text="  步驟編輯器")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._build_step_editor(left)

        center = ttk.LabelFrame(mid, text="  步驟序列")
        center.grid(row=0, column=1, sticky="nsew", padx=4)
        center.rowconfigure(0, weight=1)
        center.columnconfigure(0, weight=1)
        self._build_step_sequence(center)

        right = ttk.LabelFrame(mid, text="  控制台")
        right.grid(row=0, column=2, sticky="nsew", padx=(4, 0))
        self._build_execution_panel(right)

    def _build_orb_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        mid = ttk.Frame(parent)
        mid.grid(row=0, column=0, sticky="nsew", padx=8, pady=4)
        mid.rowconfigure(0, weight=1)
        # Two columns: settings(1) + preview(3) — preview gets most space
        mid.columnconfigure(0, weight=1, minsize=220)
        mid.columnconfigure(1, weight=3, minsize=300)

        left = ttk.LabelFrame(mid, text="  盤面設定")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._build_orb_settings(left)

        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew")
        self._build_orb_preview(right)

    def _build_orb_settings(self, parent: ttk.LabelFrame) -> None:
        PX = 10

        # ── Window binding (optional) — select BEFORE calibrating ────────────
        self._build_window_picker(parent, self._tab2_win, PX)
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(4, 6))

        # Calibrate button
        ttk.Button(parent, text="📷  框選盤面", style="Accent.TButton",
                   command=self._orb_calibrate).pack(
            fill=tk.X, padx=PX, pady=(0, 6))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(0, 4))

        # Two-column parameter grid
        grid = tk.Frame(parent, bg=_C["bg"])
        grid.pack(fill=tk.X, padx=PX, pady=2)

        def _cell(row, col, label, default):
            tk.Label(grid, text=label, bg=_C["bg"], fg=_C["text_muted"],
                     font=("Segoe UI", 9), anchor=tk.W).grid(
                row=row, column=col * 2, padx=(0, 3), pady=3, sticky=tk.W)
            v = tk.StringVar(value=str(default))
            e = self._numeric_entry(grid, v, width=5)
            e.grid(row=row, column=col * 2 + 1, padx=(0, 12), pady=3, sticky=tk.W)
            return v

        self._orb_var_rows  = _cell(0, 0, "列數",     5)
        self._orb_var_cols  = _cell(0, 1, "欄數",     6)
        self._orb_var_speed = _cell(1, 0, "拖曳速度", 25)
        tk.Label(grid, text="ms/格", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 8)).grid(row=1, column=2, padx=(0, 0), pady=3, sticky=tk.W)
        self._orb_var_beam  = _cell(2, 0, "求解精度", 50)
        self._orb_var_steps = _cell(2, 1, "最大步數", 50)

        # Preset buttons: 標準 / 高精度 — store refs so _orb_set_preset can toggle styles
        preset_row = tk.Frame(parent, bg=_C["bg"])
        preset_row.pack(fill=tk.X, padx=PX, pady=(4, 0))
        self._btn_preset_std = ttk.Button(
            preset_row, text="標準", style="Ghost.TButton",
            command=lambda: self._orb_set_preset(30, 40))
        self._btn_preset_std.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        self._btn_preset_hi = ttk.Button(
            preset_row, text="高精度", style="Accent.TButton",
            command=lambda: self._orb_set_preset(50, 50))
        self._btn_preset_hi.pack(side=tk.LEFT, expand=True, fill=tk.X)

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=8)

        ttk.Button(parent, text="🔍  辨識測試", style="Ghost.TButton",
                   command=self._orb_recognize_test).pack(
            fill=tk.X, padx=PX, pady=(0, 4))

        # ── Bottom-anchored execution controls (packed before the scrollable area) ──
        # Pack in reverse visual order so bottom items stay pinned regardless of height.

        self._btn_orb_stop = ttk.Button(
            parent, text="■  停止連續", style="Stop.TButton",
            command=self._orb_stop_loop)
        self._btn_orb_stop.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(0, 8))
        self._btn_orb_stop.pack_forget()   # hidden until loop starts

        self._btn_orb_run = ttk.Button(
            parent, text="▶  執行轉珠", style="Start.TButton",
            command=self._orb_execute)
        self._btn_orb_run.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(0, 4))

        tk.Label(parent, text="快捷鍵：F8", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 8, "italic")).pack(
            side=tk.BOTTOM, anchor=tk.W, padx=PX)

        # Continuous mode row — sits just above the execute button
        self._orb_var_loop = tk.BooleanVar(value=False)
        loop_row = tk.Frame(parent, bg=_C["bg"])
        loop_row.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(4, 2))
        ttk.Checkbutton(loop_row, text="連續模式", variable=self._orb_var_loop,
                        style="TCheckbutton").pack(side=tk.LEFT)

        self._orb_var_interval = tk.StringVar(value="6")
        self._orb_var_interval.trace_add("write",
            lambda *_: self._auto_norm(self._orb_var_interval))
        tk.Label(loop_row, text="間隔", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(12, 2))
        self._numeric_entry(loop_row, self._orb_var_interval, width=4).pack(side=tk.LEFT)
        tk.Label(loop_row, text="秒", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(2, 0))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            side=tk.BOTTOM, fill=tk.X, padx=PX, pady=6)

    def _build_orb_preview(self, parent: ttk.Frame) -> None:
        # ── Title ─────────────────────────────────────────────────────────────
        hdr = tk.Frame(parent, bg=_C["bg"])
        hdr.pack(fill=tk.X, pady=(4, 0))
        tk.Label(hdr, text="盤面預覽", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        self._lbl_orb_combo = tk.Label(
            hdr, text="", bg=_C["bg"],
            fg=_C["success"], font=("Segoe UI", 9, "bold"))
        self._lbl_orb_combo.pack(side=tk.RIGHT)

        # ── Board canvas ──────────────────────────────────────────────────────
        canvas_wrap = tk.Frame(parent,
                               bg=_C["card"],
                               highlightthickness=1,
                               highlightbackground=_C["border"])
        canvas_wrap.pack(fill=tk.BOTH, expand=True, pady=(6, 8))

        self._orb_canvas = tk.Canvas(canvas_wrap,
                                     bg=_C["card"],
                                     highlightthickness=0)
        self._orb_canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # placeholder text
        self._orb_canvas_hint = self._orb_canvas.create_text(
            0, 0, text="尚未校準\n請先按「📷 框選盤面」",
            fill=_C["text_muted"], font=("Segoe UI", 11),
            justify=tk.CENTER, anchor=tk.CENTER,
        )
        self._orb_canvas.bind("<Configure>", self._orb_canvas_resize)

        # ── Status label ──────────────────────────────────────────────────────
        self._lbl_orb_status = tk.Label(
            parent, text="就緒", bg=_C["bg"],
            fg=_C["text_muted"], font=("Segoe UI", 9))
        self._lbl_orb_status.pack(anchor=tk.W)

        if self._orb_config:
            cfg = self._orb_config
            self._root.after(200, lambda: self._lbl_orb_status.config(
                text=(f"已校準：{cfg.rows}×{cfg.cols}，"
                      f"格子 {cfg.cell_w}×{cfg.cell_h}px  "
                      f"原點({cfg.board_x},{cfg.board_y})"),
                fg=_C["success"],
            ))

    def _orb_canvas_resize(self, _e: tk.Event) -> None:
        w = self._orb_canvas.winfo_width()
        h = self._orb_canvas.winfo_height()
        self._orb_canvas.coords(self._orb_canvas_hint, w // 2, h // 2)

    def _orb_set_preset(self, beam: int, steps: int) -> None:
        self._orb_var_beam.set(str(beam))
        self._orb_var_steps.set(str(steps))
        if self._orb_config:
            self._orb_config.beam_width = beam
            self._orb_config.max_steps  = steps
        # Highlight the active preset button, ghost the other
        is_std = (beam == 30 and steps == 40)
        self._btn_preset_std.config(style="Accent.TButton" if is_std  else "Ghost.TButton")
        self._btn_preset_hi.config( style="Accent.TButton" if not is_std else "Ghost.TButton")
        self._lbl_orb_status.config(
            text=f"{'標準' if is_std else '高精度'} 模式：精度 {beam}，步數 {steps}",
            fg=_C["text_muted"])

    # ═══════════════════════════════════════════════════════════════════════════
    # Tab 3 — 場景腳本
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_scene_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)

        mid = ttk.Frame(parent)
        mid.grid(row=0, column=0, sticky="nsew", padx=8, pady=(4, 4))
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=3, minsize=340)
        mid.columnconfigure(1, weight=1, minsize=260)

        left = ttk.LabelFrame(mid, text="  規則列表")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self._build_scene_rules_panel(left)

        right = ttk.LabelFrame(mid, text="  執行")
        right.grid(row=0, column=1, sticky="nsew")
        self._build_scene_control_panel(right)

    # ── left: rule list + edit form ───────────────────────────────────────────

    def _build_scene_rules_panel(self, parent: ttk.LabelFrame) -> None:
        PX = 10

        # ── Profile selector ─────────────────────────────────────────────────
        prof_row = tk.Frame(parent, bg=_C["bg"])
        prof_row.pack(fill=tk.X, padx=PX, pady=(6, 4))
        tk.Label(prof_row, text="腳本", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        self._scene_prof_var = tk.StringVar(value=self._scene_profile)
        self._scene_prof_cb = ttk.Combobox(
            prof_row, textvariable=self._scene_prof_var,
            state="readonly", font=("Segoe UI", 9), width=16)
        self._scene_prof_cb.pack(side=tk.LEFT, padx=(0, 6))
        self._scene_prof_cb.bind("<<ComboboxSelected>>", self._scene_profile_select)
        ttk.Button(prof_row, text="＋", style="Ghost.TButton", width=3,
                   command=self._scene_profile_new).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(prof_row, text="✕", style="GhostDanger.TButton", width=3,
                   command=self._scene_profile_delete).pack(side=tk.LEFT)
        self._scene_refresh_profiles()

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(4, 4))

        # ── Window binding ───────────────────────────────────────────────────
        self._build_window_picker(parent, self._tab3_win, PX)
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(4, 6))

        # ── Edit form (TOP) ──────────────────────────────────────────────────
        form = tk.Frame(parent, bg=_C["bg"])
        form.pack(fill=tk.X, padx=PX)

        # Name + enabled
        row0 = tk.Frame(form, bg=_C["bg"])
        row0.pack(fill=tk.X, pady=(0, 4))
        tk.Label(row0, text="名稱", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._scene_var_name = tk.StringVar()
        ttk.Entry(row0, textvariable=self._scene_var_name,
                  font=("Segoe UI", 9), width=16).pack(side=tk.LEFT, padx=(0, 8))
        self._scene_var_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(row0, text="啟用",
                        variable=self._scene_var_enabled).pack(side=tk.LEFT)

        # Image path + capture buttons
        row1 = tk.Frame(form, bg=_C["bg"])
        row1.pack(fill=tk.X, pady=(0, 4))
        tk.Label(row1, text="圖片", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._scene_var_imgpath = tk.StringVar()
        self._scene_var_imgpath.trace_add("write",
            lambda *_: self._root.after(50, self._scene_update_preview))
        ttk.Entry(row1, textvariable=self._scene_var_imgpath,
                  font=("Segoe UI", 8), width=20).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(row1, text="瀏覽", style="Ghost.TButton",
                   command=self._scene_browse).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(row1, text="框選", style="Ghost.TButton",
                   command=self._scene_capture).pack(side=tk.LEFT)

        # Image preview — fixed-height frame prevents Label height=N (chars) issue
        prev_frame = tk.Frame(form, bg=_C["card"], height=72)
        prev_frame.pack(fill=tk.X, pady=(0, 6))
        prev_frame.pack_propagate(False)
        self._scene_preview_lbl = tk.Label(
            prev_frame, bg=_C["card"],
            text="（無圖片）", fg=_C["text_muted"], font=("Segoe UI", 8))
        self._scene_preview_lbl.pack(fill=tk.BOTH, expand=True)

        # Action — two big radio buttons with descriptions
        tk.Label(form, text="偵測到圖片時，自動執行：",
                 bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(anchor=tk.W, pady=(0, 2))

        self._scene_var_action = tk.StringVar(value="click")

        def _make_action_btn(val, title, desc):
            f = tk.Frame(form, bg=_C["card"],
                         highlightthickness=1, highlightbackground=_C["border"])
            f.pack(fill=tk.X, pady=(0, 3))
            rb = ttk.Radiobutton(f, text=title,
                                 variable=self._scene_var_action, value=val)
            rb.pack(anchor=tk.W, padx=8, pady=(4, 0))
            tk.Label(f, text=desc, bg=_C["card"], fg=_C["text_muted"],
                     font=("Segoe UI", 8)).pack(anchor=tk.W, padx=24, pady=(0, 4))

        _make_action_btn("click",     "點擊它",
                         "自動點擊圖片出現的位置（按鈕、確認框）")
        _make_action_btn("orb_solve", "啟動轉珠 AI",
                         "執行自動轉珠（用於戰鬥珠盤場景）")

        # Fixed click coordinates (optional — overrides template centre)
        row_xy = tk.Frame(form, bg=_C["bg"])
        row_xy.pack(fill=tk.X, pady=(4, 0))
        tk.Label(row_xy, text="固定座標", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(row_xy, text="X", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 2))
        self._scene_var_click_x = tk.StringVar()
        self._numeric_entry(row_xy, self._scene_var_click_x, width=6).pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(row_xy, text="Y", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 2))
        self._scene_var_click_y = tk.StringVar()
        self._numeric_entry(row_xy, self._scene_var_click_y, width=6).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row_xy, text="📍", style="Ghost.TButton", width=3,
                   command=self._capture_position).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(row_xy, text="← S 鍵", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 8)).pack(side=tk.LEFT)

        # Dynamic mode hint
        self._scene_mode_lbl = tk.Label(
            form, text="", bg=_C["bg"], fg=_C["accent"], font=("Segoe UI", 8))
        self._scene_mode_lbl.pack(anchor=tk.W, pady=(2, 2))
        for var in (self._scene_var_imgpath, self._scene_var_click_x,
                    self._scene_var_click_y, self._scene_var_action):
            var.trace_add("write", lambda *_: self._root.after(0, self._scene_update_mode_hint))

        # Confidence + cooldown
        row3 = tk.Frame(form, bg=_C["bg"])
        row3.pack(fill=tk.X, pady=(4, 6))
        tk.Label(row3, text="信心度", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._scene_var_conf = tk.StringVar(value="0.8")
        self._numeric_entry(row3, self._scene_var_conf, width=5).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(row3, text="冷卻", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._scene_var_cool = tk.StringVar(value="3.0")
        self._numeric_entry(row3, self._scene_var_cool, width=5).pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(row3, text="秒", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)

        btn_row_apply = tk.Frame(form, bg=_C["bg"])
        btn_row_apply.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(btn_row_apply, text="套用（更新選中）", style="Accent.TButton",
                   command=self._scene_apply_edit).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(btn_row_apply, text="＋ 新增規則", style="Start.TButton",
                   command=self._scene_add_from_form).pack(side=tk.LEFT, expand=True, fill=tk.X)

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(0, 4))

        # ── Rules list (BOTTOM, expands) ─────────────────────────────────────
        list_hdr = tk.Frame(parent, bg=_C["bg"])
        list_hdr.pack(fill=tk.X, padx=PX, pady=(0, 4))
        tk.Label(list_hdr, text="規則列表（由上往下依序偵測）",
                 bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT)
        btn_row = tk.Frame(list_hdr, bg=_C["bg"])
        btn_row.pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="↑", style="Ghost.TButton", width=3,
                   command=self._scene_move_up).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(btn_row, text="↓", style="Ghost.TButton", width=3,
                   command=self._scene_move_down).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="複製", style="Ghost.TButton",
                   command=self._scene_duplicate).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(btn_row, text="✕ 刪除", style="GhostDanger.TButton",
                   command=self._scene_delete).pack(side=tk.LEFT)

        lb_wrap = tk.Frame(parent, bg=_C["card"],
                           highlightthickness=1, highlightbackground=_C["border"])
        lb_wrap.pack(fill=tk.BOTH, expand=True, padx=PX, pady=(0, 8))

        sb = tk.Scrollbar(lb_wrap, orient=tk.VERTICAL, bg=_C["bg_dark"],
                          troughcolor=_C["bg"], activebackground=_C["accent"])
        self._scene_lb = tk.Listbox(
            lb_wrap,
            bg=_C["card"], fg=_C["text"],
            selectbackground=_C["accent"], selectforeground="white",
            font=("Segoe UI", 9),
            relief="flat", bd=0,
            activestyle="none",
            height=16,
            yscrollcommand=sb.set,
        )
        sb.config(command=self._scene_lb.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._scene_lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._scene_lb.bind("<<ListboxSelect>>", self._scene_on_select)

    # ── right: log + start/stop ───────────────────────────────────────────────

    def _build_scene_control_panel(self, parent: ttk.LabelFrame) -> None:
        PX = 10

        # ── Start / Stop — single toggle button, never repacked ─────────────
        self._btn_scene_run = ttk.Button(
            parent, text="▶  開始場景腳本", style="Start.TButton",
            command=self._scene_start)
        self._btn_scene_run.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(0, 4))

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            side=tk.BOTTOM, fill=tk.X, padx=PX, pady=6)

        # ── Orb calibration (independent — no need to visit Tab 2) ──────────
        orb_row = tk.Frame(parent, bg=_C["bg"])
        orb_row.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(0, 4))

        ttk.Button(
            orb_row, text="⊙  校準轉珠盤位置", style="Ghost.TButton",
            command=self._orb_calibrate,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._scene_lbl_orb = tk.Label(
            parent, bg=_C["bg"], fg=_C["text_muted"],
            font=("Segoe UI", 8), anchor=tk.W)
        self._scene_lbl_orb.pack(side=tk.BOTTOM, fill=tk.X, padx=PX, pady=(0, 2))
        self._scene_update_orb_label()

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            side=tk.BOTTOM, fill=tk.X, padx=PX, pady=6)

        # ── Status log ──────────────────────────────────────────────────────
        tk.Label(parent, text="執行記錄", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W, padx=PX, pady=(6, 2))

        log_wrap = tk.Frame(parent, bg=_C["card"],
                            highlightthickness=1, highlightbackground=_C["border"])
        log_wrap.pack(fill=tk.BOTH, expand=True, padx=PX, pady=(0, 4))

        sb = tk.Scrollbar(log_wrap, orient=tk.VERTICAL, bg=_C["bg_dark"],
                          troughcolor=_C["bg"], activebackground=_C["accent"])
        self._scene_log_txt = tk.Text(
            log_wrap,
            bg=_C["card"], fg=_C["text_muted"],
            font=("Segoe UI", 8),
            relief="flat", bd=0,
            state=tk.DISABLED,
            wrap=tk.WORD,
            yscrollcommand=sb.set,
        )
        sb.config(command=self._scene_log_txt.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._scene_log_txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)

    # ── scene helpers ─────────────────────────────────────────────────────────

    def _scene_refresh_list(self) -> None:
        self._scene_lb.delete(0, tk.END)
        for r in self._scene_rules:
            chk  = "☑" if r.enabled else "☐"
            name = r.name or r.image_path.split("/")[-1].split("\\")[-1]
            cd   = f"{r.cooldown:g}s"
            if r.action == "orb_solve":
                detail = f"🔮 轉珠  ({cd})"
            elif not r.image_path and r.click_x is not None and r.click_y is not None:
                detail = f"📌 ({r.click_x},{r.click_y})  ({cd})"
            else:
                detail = f"🖼 點擊  ({cd})"
            self._scene_lb.insert(tk.END, f"  {chk}  {name}  →  {detail}")
            fg = _C["text"] if r.enabled else _C["text_muted"]
            self._scene_lb.itemconfig(tk.END, foreground=fg)

    def _scene_on_select(self, _e=None) -> None:
        sel = self._scene_lb.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx >= len(self._scene_rules):
            return
        self._scene_sel = idx
        r = self._scene_rules[idx]
        self._scene_var_name.set(r.name)
        self._scene_var_imgpath.set(r.image_path)
        self._scene_var_action.set(r.action)
        self._scene_var_conf.set(str(r.confidence))
        self._scene_var_cool.set(str(r.cooldown))
        self._scene_var_enabled.set(r.enabled)
        self._scene_var_click_x.set("" if r.click_x is None else str(r.click_x))
        self._scene_var_click_y.set("" if r.click_y is None else str(r.click_y))
        self._scene_update_preview()
        self._scene_update_mode_hint()

    def _scene_update_mode_hint(self) -> None:
        img = self._scene_var_imgpath.get().strip()
        x   = self._scene_var_click_x.get().strip()
        y   = self._scene_var_click_y.get().strip()
        act = self._scene_var_action.get()
        has_coord = bool(x and y)
        if act == "orb_solve":
            hint = "🔮 模式：轉珠 AI — 偵測珠盤後自動轉珠"
        elif not img and has_coord:
            hint = "📌 模式：純座標點擊 — 不需圖片，直接點固定位置"
        elif img and has_coord:
            hint = "🎯 模式：圖片觸發 → 點固定座標"
        elif img and not has_coord:
            hint = "🖼 模式：圖片偵測 → 點圖片中心（可加偏移）"
        else:
            hint = "⚠ 請設定圖片路徑 或 填入固定座標"
        if hasattr(self, "_scene_mode_lbl"):
            self._scene_mode_lbl.config(text=hint)

    def _scene_update_preview(self) -> None:
        path = self._scene_var_imgpath.get().strip()
        if not path:
            self._scene_preview_lbl.config(image="", text="（無圖片）")
            self._scene_preview_photo = None
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            img.thumbnail((200, 80), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._scene_preview_photo = photo
            self._scene_preview_lbl.config(image=photo, text="")
        except Exception:
            self._scene_preview_lbl.config(image="", text="（無法載入）")
            self._scene_preview_photo = None

    def _scene_add(self) -> None:
        r = SceneRule(image_path="", action="click", name="新規則",
                      confidence=0.8, cooldown=3.0, enabled=True,
                      order_idx=len(self._scene_rules))
        self._scene_rules.append(r)
        self._scene_refresh_list()
        self._scene_lb.selection_clear(0, tk.END)
        self._scene_lb.selection_set(tk.END)
        self._scene_lb.see(tk.END)
        self._scene_on_select()
        self._scene_save()

    def _scene_delete(self) -> None:
        if self._scene_sel is None:
            return
        idx = self._scene_sel
        if idx >= len(self._scene_rules):
            return
        self._scene_rules.pop(idx)
        self._scene_sel = None
        self._scene_refresh_list()
        self._scene_save()

    def _scene_duplicate(self) -> None:
        if self._scene_sel is None or self._scene_sel >= len(self._scene_rules):
            return
        src = self._scene_rules[self._scene_sel]
        dup = SceneRule(
            image_path=src.image_path, action=src.action,
            name=src.name + " 副本",
            confidence=src.confidence, cooldown=src.cooldown,
            enabled=src.enabled, order_idx=len(self._scene_rules),
            click_dx=src.click_dx, click_dy=src.click_dy,
            click_x=src.click_x, click_y=src.click_y,
        )
        insert_at = self._scene_sel + 1
        self._scene_rules.insert(insert_at, dup)
        self._scene_sel = insert_at
        self._scene_refresh_list()
        self._scene_lb.selection_set(insert_at)
        self._scene_lb.see(insert_at)
        self._scene_save()

    def _scene_move_up(self) -> None:
        if self._scene_sel is None or self._scene_sel == 0:
            return
        i = self._scene_sel
        self._scene_rules[i], self._scene_rules[i - 1] = \
            self._scene_rules[i - 1], self._scene_rules[i]
        self._scene_sel = i - 1
        self._scene_refresh_list()
        self._scene_lb.selection_set(self._scene_sel)
        self._scene_save()

    def _scene_move_down(self) -> None:
        if self._scene_sel is None or self._scene_sel >= len(self._scene_rules) - 1:
            return
        i = self._scene_sel
        self._scene_rules[i], self._scene_rules[i + 1] = \
            self._scene_rules[i + 1], self._scene_rules[i]
        self._scene_sel = i + 1
        self._scene_refresh_list()
        self._scene_lb.selection_set(self._scene_sel)
        self._scene_save()

    def _scene_add_from_form(self) -> None:
        """Always append a new rule using current form values, regardless of selection."""
        name    = self._scene_var_name.get().strip()
        imgpath = self._scene_var_imgpath.get().strip()
        action  = self._scene_var_action.get()
        enabled = self._scene_var_enabled.get()
        try:
            conf = float(self._scene_var_conf.get() or 0.8)
        except ValueError:
            conf = 0.8
        try:
            cool = float(self._scene_var_cool.get() or 3.0)
        except ValueError:
            cool = 3.0
        try:
            click_x = int(self._scene_var_click_x.get().strip()) \
                if self._scene_var_click_x.get().strip() else None
            click_y = int(self._scene_var_click_y.get().strip()) \
                if self._scene_var_click_y.get().strip() else None
        except ValueError:
            click_x = click_y = None

        r = SceneRule(
            image_path=imgpath, action=action, name=name or "新規則",
            confidence=conf, cooldown=cool, enabled=enabled,
            order_idx=len(self._scene_rules),
            click_x=click_x, click_y=click_y,
        )
        self._scene_rules.append(r)
        idx = len(self._scene_rules) - 1
        self._scene_sel = idx
        self._scene_refresh_list()
        self._scene_lb.selection_set(idx)
        self._scene_lb.see(idx)
        self._scene_save()

    def _scene_apply_edit(self) -> None:
        name     = self._scene_var_name.get().strip()
        imgpath  = self._scene_var_imgpath.get().strip()
        action   = self._scene_var_action.get()
        enabled  = self._scene_var_enabled.get()
        try:
            conf = float(self._scene_var_conf.get() or 0.8)
        except ValueError:
            conf = 0.8
        try:
            cool = float(self._scene_var_cool.get() or 3.0)
        except ValueError:
            cool = 3.0

        try:
            click_x = int(self._scene_var_click_x.get().strip()) \
                if self._scene_var_click_x.get().strip() else None
            click_y = int(self._scene_var_click_y.get().strip()) \
                if self._scene_var_click_y.get().strip() else None
        except ValueError:
            click_x = click_y = None

        if self._scene_sel is not None and self._scene_sel < len(self._scene_rules):
            # Update existing selected rule
            r = self._scene_rules[self._scene_sel]
            r.name = name; r.image_path = imgpath; r.action = action
            r.enabled = enabled; r.confidence = conf; r.cooldown = cool
            r.click_x = click_x; r.click_y = click_y
            idx = self._scene_sel
        else:
            # No selection → append as new rule
            r = SceneRule(
                image_path=imgpath, action=action, name=name or "新規則",
                confidence=conf, cooldown=cool, enabled=enabled,
                order_idx=len(self._scene_rules),
                click_x=click_x, click_y=click_y,
            )
            self._scene_rules.append(r)
            idx = len(self._scene_rules) - 1
            self._scene_sel = idx

        self._scene_refresh_list()
        self._scene_lb.selection_set(idx)
        self._scene_lb.see(idx)
        self._scene_save()

    def _scene_browse(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="選擇圖片",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")],
        )
        if path:
            self._scene_var_imgpath.set(path)

    def _scene_capture(self) -> None:
        def on_done(path, _lx, _ly):
            if path:
                self._scene_var_imgpath.set(path)

        confine = None
        if self._tab3_win.get("title"):
            hwnd = self._resolve_bound_window(self._tab3_win)
            if hwnd:
                from services.window_manager import get_window_rect
                r = get_window_rect(hwnd)
                if r:
                    confine = r  # (x, y, w, h)

        _RegionSelector(self._root,
                        save_dir=os.path.join(_app_dir(), "images", "scene"),
                        on_done=on_done,
                        confine_rect=confine)

    def _scene_save(self) -> None:
        try:
            self._db.save_scene_rules(self._scene_rules, self._scene_profile)
        except Exception:
            logger.exception("Failed to save scene rules")

    # ── Profile management ────────────────────────────────────────────────────

    def _scene_refresh_profiles(self) -> None:
        try:
            names = self._db.list_scene_profile_names()
        except Exception:
            names = [self._scene_profile]
        self._scene_prof_cb["values"] = names
        if self._scene_profile not in names and names:
            self._scene_profile = names[0]
        self._scene_prof_var.set(self._scene_profile)

    def _scene_profile_select(self, _e=None) -> None:
        name = self._scene_prof_var.get()
        if name == self._scene_profile:
            return
        self._scene_profile = name
        try:
            self._scene_rules = self._db.load_scene_rules(name)
        except Exception:
            self._scene_rules = []
        self._scene_sel = None
        self._scene_refresh_list()

    def _scene_profile_new(self) -> None:
        from tkinter.simpledialog import askstring
        name = askstring("新增腳本", "請輸入腳本名稱：", parent=self._root)
        if not name or not name.strip():
            return
        name = name.strip()
        try:
            self._db.create_scene_profile(name)
        except Exception:
            messagebox.showerror("錯誤", f"無法建立腳本「{name}」\n（名稱可能已存在）")
            return
        self._scene_profile = name
        self._scene_rules = []
        self._scene_sel = None
        self._scene_refresh_profiles()
        self._scene_refresh_list()

    def _scene_profile_delete(self) -> None:
        names = self._db.list_scene_profile_names()
        if len(names) <= 1:
            messagebox.showwarning("刪除腳本", "至少須保留一個腳本，無法刪除。")
            return
        if not messagebox.askyesno("刪除腳本",
                f"確定要刪除腳本「{self._scene_profile}」及其所有規則嗎？"):
            return
        try:
            self._db.delete_scene_profile(self._scene_profile)
        except Exception:
            messagebox.showerror("錯誤", "刪除失敗")
            return
        remaining = [n for n in names if n != self._scene_profile]
        self._scene_profile = remaining[0]
        try:
            self._scene_rules = self._db.load_scene_rules(self._scene_profile)
        except Exception:
            self._scene_rules = []
        self._scene_sel = None
        self._scene_refresh_profiles()
        self._scene_refresh_list()

    # ── Built-in presets ──────────────────────────────────────────────────────

    # Shared navigation + dialog rules (used by both presets)
    _CLICK_PRESETS = [
        # ── Post-battle popups ────────────────────────────────────────────────
        ("斷線重連",      "scene_btn_confirm.png",     "click", 0.85,  8.0,  0,   0),
        ("知道了",        "scene_btn_zhidaole.png",    "click", 0.85,  2.0,  0,   0),
        ("知道了(1h提示)", "scene_btn_zhidaole_1h.png", "click", 0.82,  2.0,  0,   0),
        ("確定(升級)",    "scene_btn_ok.png",          "click", 0.85,  2.0,  0,   0),
        ("確定(獎勵)",    "scene_btn_ok2.png",         "click", 0.82,  2.0,  0,   0),
        # ── Stage entry flow ──────────────────────────────────────────────────
        ("選第一個盟友",  "scene_select_ally.png",     "click", 0.85,  3.0,  0,   0),
        ("進入NEW關卡",   "scene_stage_new.png",       "click", 0.85,  5.0, 256,  3),
        ("點擊NEW地城",   "scene_new_badge.png",       "click", 0.82,  5.0, 56,  77),
        ("翻下一頁",      "scene_btn_nextpage.png",    "click", 0.82, 10.0,  0,   0),
        # ── Hub navigation (lowest priority) ──────────────────────────────────
        ("點冒險地圖",    "scene_btn_adventure.png",   "click", 0.80,  5.0,  0,   0),
        ("點摩靈按鈕",    "scene_btn_maling.png",      "click", 0.80,  5.0,  0,   0),
    ]

    def _scene_load_tos_preset(self, profile_name: str = "摩靈傳說") -> None:
        """Load 摩靈轉珠 preset (orb_solve + navigation) into profile_name."""
        presets = [
            ("珠盤就緒", "scene_battle_banner.png", "orb_solve", 0.75, 15.0, 0, 0),
            *self._CLICK_PRESETS,
        ]
        self._scene_apply_preset(presets, profile_name)

    def _scene_load_click_preset(self, profile_name: str = "按鈕點擊") -> None:
        """Load 按鈕點擊 preset (navigation only, no orb_solve) into profile_name."""
        example = [
            # (name, fname, action, conf, cooldown, dx, dy, enabled, click_x, click_y)
            ("📌 固定座標範例（停用）", "", "click", 0.8, 60.0, 0, 0, False, 960, 540),
        ]
        self._scene_apply_preset(example + list(self._CLICK_PRESETS), profile_name)

    def _scene_apply_preset(self, presets: list, profile_name: str) -> None:
        base = os.path.join(_app_dir(), "images", "scene")
        missing = [n for n, f, *_ in presets
                   if f and not os.path.exists(os.path.join(base, f))]
        if missing:
            logger.warning("Preset images missing: %s", missing)

        existing_profiles = self._db.list_scene_profile_names()
        if profile_name not in existing_profiles:
            self._db.create_scene_profile(profile_name)

        self._scene_profile = profile_name
        rules = []
        for i, row in enumerate(presets):
            name, fname, action, conf, cooldown, click_dx, click_dy = row[:7]
            enabled = row[7] if len(row) > 7 else True
            click_x = row[8] if len(row) > 8 else None
            click_y = row[9] if len(row) > 9 else None
            rules.append(SceneRule(
                image_path=os.path.join(base, fname) if fname else "",
                action=action, name=name,
                confidence=conf, cooldown=cooldown,
                enabled=enabled, order_idx=i,
                click_dx=click_dx, click_dy=click_dy,
                click_x=click_x, click_y=click_y,
            ))
        self._scene_rules = rules
        self._scene_sel = None
        self._scene_refresh_profiles()
        self._scene_refresh_list()
        self._scene_save()
        logger.info("Loaded preset (%d rules) into profile '%s'", len(presets), profile_name)

    # ── runner control ────────────────────────────────────────────────────────

    def _scene_get_win_info(self):
        if not self._tab3_win.get("title"):
            return None, None
        hwnd = self._resolve_bound_window(self._tab3_win)
        if not hwnd:
            return None, None
        from services.window_manager import get_window_rect
        rect = get_window_rect(hwnd)
        return hwnd, rect   # rect = (x, y, w, h) or None

    def _scene_start(self) -> None:
        active = [r for r in self._scene_rules if r.enabled and r.image_path]
        if not active:
            messagebox.showwarning("場景腳本", "沒有啟用的規則，請先新增並套用規則")
            return
        if self._scene_runner and self._scene_runner.is_running:
            return
        self._scene_runner = SceneRunner()
        self._scene_runner.start(
            rules=list(self._scene_rules),
            get_orb_config=lambda: self._orb_config,
            on_status=lambda msg: self._root.after(0, lambda m=msg: self._scene_on_status(m)),
            on_fired=lambda rule: self._root.after(
                0, lambda r=rule: self._scene_log_append(
                    f"▶ {r.name or r.image_path.split(chr(47))[-1].split(chr(92))[-1]}"
                    f" → {'點擊' if r.action == 'click' else '轉珠'}\n"
                )),
            get_win_info=self._scene_get_win_info,
            base_dir=_app_dir(),
        )
        self._btn_scene_run.config(
            text="■  停止場景腳本", style="Stop.TButton", command=self._scene_stop)
        self._scene_log_append("═══ 場景腳本已啟動 ═══\n")

    def _scene_stop(self) -> None:
        if self._scene_runner:
            self._scene_runner.stop()
        self._btn_scene_run.config(
            text="▶  開始場景腳本", style="Start.TButton", command=self._scene_start)

    def _scene_update_orb_label(self) -> None:
        if self._scene_lbl_orb is None:
            return
        if self._orb_config:
            cfg = self._orb_config
            self._scene_lbl_orb.config(
                text=f"轉珠盤：已校準 {cfg.rows}×{cfg.cols}  原點({cfg.board_x},{cfg.board_y})",
                fg=_C["success"])
        else:
            self._scene_lbl_orb.config(
                text="轉珠盤：尚未校準（點上方按鈕框選）", fg=_C["warning"])

    def _scene_on_status(self, msg: str) -> None:
        if msg not in ("掃描中…",):   # suppress noisy idle messages from log
            self._scene_log_append(msg + "\n")

    def _scene_log_append(self, msg: str) -> None:
        import time as _t
        ts = _t.strftime("%H:%M:%S")
        self._scene_log_txt.config(state=tk.NORMAL)
        self._scene_log_txt.insert(tk.END, f"[{ts}] {msg}")
        self._scene_log_txt.see(tk.END)
        self._scene_log_txt.config(state=tk.DISABLED)

    # ── orb callbacks ─────────────────────────────────────────────────────────

    def _orb_calibrate(self) -> None:
        from models.orb_config import OrbConfig
        def on_done(path, bx, by):
            if not path:
                return
            try:
                from PIL import Image
                img = Image.open(path)
                rows = int(self._orb_var_rows.get() or 5)
                cols = int(self._orb_var_cols.get() or 6)
                cell_w = img.width  // cols
                cell_h = img.height // rows
                self._orb_config = OrbConfig(
                    name="default",
                    board_x=bx, board_y=by,
                    cell_w=cell_w, cell_h=cell_h,
                    rows=rows, cols=cols,
                    drag_speed_ms=int(self._orb_var_speed.get() or 25),
                    beam_width=int(self._orb_var_beam.get() or 50),
                    max_steps=int(self._orb_var_steps.get() or 50),
                )
                self._orb_board_img = path
                self._orb_show_preview_image(path)
                # Sync ref to window position at calibration time so offset is
                # computed relative to where the window actually was when we captured.
                if self._tab2_win.get("title"):
                    hwnd = self._resolve_bound_window(self._tab2_win)
                    if hwnd:
                        from services.window_manager import get_window_rect
                        rect = get_window_rect(hwnd)
                        if rect:
                            self._tab2_win["ref"]      = (rect[0], rect[1])
                            self._tab2_win["ref_size"] = (rect[2], rect[3])
                try:
                    self._db.save_orb_config(self._orb_config)
                except Exception:
                    logger.exception("Failed to save orb config")
                self._lbl_orb_status.config(
                    text=f"已校準：{rows}×{cols}，格子 {cell_w}×{cell_h}px  原點({bx},{by})",
                    fg=_C["success"])
                self._scene_update_orb_label()
            except Exception as exc:
                self._lbl_orb_status.config(text=f"校準失敗：{exc}", fg=_C["danger"])

        _RegionSelector(self._root,
                        save_dir=os.path.join(_app_dir(), "images", "orb"),
                        on_done=on_done)

    def _orb_recognize_test(self) -> None:
        if not self._orb_config:
            messagebox.showwarning("轉珠", "請先按「📷 框選盤面」校準")
            return
        self._lbl_orb_status.config(text="截圖辨識中…", fg=_C["warning"])
        self._root.update_idletasks()
        try:
            from services.orb_board import OrbBoard
            from services.orb_solver import score_board
            config = self._transform_orb_config(self._orb_config, self._tab2_win)
            board_svc = OrbBoard(config)
            board = board_svc.snapshot()
            self._orb_draw_board(board)
            current = score_board(board)
            self._lbl_orb_combo.config(text=f"目前 combo：{current}")
            self._lbl_orb_status.config(
                text=f"辨識完成，目前盤面 {current} combo",
                fg=_C["success"])
        except Exception as exc:
            self._lbl_orb_status.config(text=f"辨識失敗：{exc}", fg=_C["danger"])

    def _orb_execute(self) -> None:
        if not self._orb_config:
            messagebox.showwarning("轉珠", "請先按「📷 框選盤面」校準")
            return
        if self._orb_executor and self._orb_executor.is_running:
            return
        # Activate continuous loop on first manual call if checkbox is on
        if self._orb_var_loop.get() and not self._orb_loop_active:
            self._orb_loop_active = True
        # Always swap run→stop while executing (single or continuous)
        self._btn_orb_run.pack_forget()
        self._btn_orb_stop.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 8))
        self._lbl_orb_status.config(text="截圖辨識中…", fg=_C["warning"])

        def _worker():
            try:
                from services.orb_board import OrbBoard
                from services.orb_solver import OrbSolver
                from services.orb_executor import OrbExecutor

                # Apply window-binding transform (position + scale) if active
                if self._tab2_win.get("title"):
                    if self._resolve_bound_window(self._tab2_win) is None:
                        self._root.after(0, lambda: messagebox.showwarning(
                            "綁定視窗", "找不到綁定的視窗，請重新整理並選擇"))
                        self._root.after(0, self._orb_reset_run_btn)
                        return
                config = self._transform_orb_config(self._orb_config, self._tab2_win)

                board = OrbBoard(config).snapshot()
                self._root.after(0, lambda: self._orb_draw_board(board))
                self._root.after(0, lambda: self._lbl_orb_status.config(
                    text="求解中…", fg=_C["warning"]))

                path, predicted = OrbSolver(config).solve(board, time_limit=12.0)

                if not path:
                    self._root.after(0, lambda: self._lbl_orb_status.config(
                        text="求解失敗，找不到路線", fg=_C["danger"]))
                    self._root.after(0, self._orb_reset_run_btn)
                    return

                self._root.after(0, lambda: self._lbl_orb_combo.config(
                    text=f"預測 combo：{predicted}"))
                self._root.after(0, lambda: self._lbl_orb_status.config(
                    text=f"執行中… 預測 {predicted} combo，{len(path)} 步",
                    fg=_C["success"]))

                executor = OrbExecutor(config)
                self._orb_executor = executor

                def on_done():
                    if self._orb_loop_active:
                        try:
                            interval = max(1, float(self._orb_var_interval.get() or 6))
                        except ValueError:
                            interval = 6
                        ms = int(interval * 1000)
                        self._root.after(0, lambda: self._lbl_orb_status.config(
                            text=f"轉珠完成，{interval:.0f} 秒後偵測盤面…",
                            fg=_C["success"]))
                        self._orb_loop_after = self._root.after(ms, self._orb_loop_check)
                    else:
                        self._root.after(0, lambda: self._lbl_orb_status.config(
                            text="轉珠完成", fg=_C["success"]))
                        self._root.after(0, self._orb_reset_run_btn)

                def on_error(msg):
                    self._root.after(0, lambda: self._lbl_orb_status.config(
                        text=f"執行錯誤：{msg}", fg=_C["danger"]))
                    self._root.after(0, self._orb_reset_run_btn)

                executor.run(path, on_done, on_error)

            except Exception as exc:
                self._root.after(0, lambda: self._lbl_orb_status.config(
                    text=f"錯誤：{exc}", fg=_C["danger"]))
                self._root.after(0, self._orb_reset_run_btn)

        threading.Thread(target=_worker, daemon=True, name="OrbWorker").start()

    def _orb_loop_check(self) -> None:
        """Snapshot the board region in a worker thread; stop loop if battle has ended."""
        if not self._orb_loop_active or not self._orb_config:
            return

        def _check():
            try:
                from services.orb_board import OrbBoard, EMPTY
                config = self._transform_orb_config(self._orb_config, self._tab2_win)
                board = OrbBoard(config).snapshot()
                flat  = [cell for row in board for cell in row]
                total = len(flat)
                empty_count = flat.count(EMPTY)
                orb_types   = {c for c in flat if c != EMPTY}
                # Covered board: >50% unrecognised OR all cells same type (dialog artifact)
                if empty_count > total * 0.5 or len(orb_types) <= 1:
                    self._root.after(0, lambda: self._lbl_orb_status.config(
                        text="偵測到關卡結束，自動停止連續模式", fg=_C["warning"]))
                    self._root.after(0, self._orb_stop_loop)
                    return
            except Exception:
                pass  # recognition error — just continue
            self._root.after(0, self._orb_execute)

        threading.Thread(target=_check, daemon=True, name="OrbLoopCheck").start()

    def _orb_stop_loop(self) -> None:
        self._orb_loop_active = False
        if self._orb_loop_after:
            self._root.after_cancel(self._orb_loop_after)
            self._orb_loop_after = None
        if self._orb_executor and self._orb_executor.is_running:
            self._orb_executor.abort()
        self._orb_reset_run_btn()
        self._lbl_orb_status.config(text="已停止", fg=_C["text_muted"])

    def _orb_reset_run_btn(self) -> None:
        self._orb_loop_active = False
        self._btn_orb_stop.pack_forget()
        self._btn_orb_run.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 4))
        self._btn_orb_run.config(state=tk.NORMAL)

    def _orb_draw_board(self, board) -> None:
        from services.orb_board import ORB_COLOR
        self._orb_canvas.delete("all")
        rows = len(board)
        cols = len(board[0]) if board else 6
        cw = max(self._orb_canvas.winfo_width(),  200)
        ch = max(self._orb_canvas.winfo_height(), 150)
        cell_w = cw / cols
        cell_h = ch / rows
        pad = max(3, int(min(cell_w, cell_h) * 0.06))
        font_size = max(8, int(min(cell_w, cell_h) * 0.28))
        for r in range(rows):
            for c in range(cols):
                orb = board[r][c]
                color = ORB_COLOR.get(orb, "#334155")
                x1 = c * cell_w + pad
                y1 = r * cell_h + pad
                x2 = (c + 1) * cell_w - pad
                y2 = (r + 1) * cell_h - pad
                self._orb_canvas.create_oval(
                    x1, y1, x2, y2, fill=color, outline="")
                self._orb_canvas.create_text(
                    (x1 + x2) / 2, (y1 + y2) / 2,
                    text=orb, fill="white",
                    font=("Segoe UI", font_size, "bold"))

    def _orb_show_preview_image(self, path: str) -> None:
        try:
            from PIL import Image, ImageTk
            img = Image.open(path)
            cw = max(self._orb_canvas.winfo_width(),  200)
            ch = max(self._orb_canvas.winfo_height(), 150)
            img.thumbnail((cw, ch), Image.LANCZOS)
            self._orb_preview_tk = ImageTk.PhotoImage(img)
            self._orb_canvas.delete("all")
            self._orb_canvas.create_image(
                cw // 2, ch // 2,
                anchor=tk.CENTER, image=self._orb_preview_tk)
        except Exception:
            pass

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

        # ── Drag params (hidden until drag is selected) ──
        self._frm_drag = tk.Frame(parent, bg=_C["bg"])

        tk.Label(self._frm_drag, text="終點座標", bg=_C["bg"],
                 fg=_C["text_muted"], font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W, padx=10, pady=(8, 0))
        drag_row = tk.Frame(self._frm_drag, bg=_C["bg"])
        drag_row.pack(fill=tk.X, padx=10, pady=(2, 4))
        tk.Label(drag_row, text="X", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9), width=2).pack(side=tk.LEFT)
        self._var_drag_x = tk.StringVar()
        self._var_drag_x.trace_add("write", lambda *_: self._auto_norm(self._var_drag_x))
        self._numeric_entry(drag_row, self._var_drag_x, width=7).pack(side=tk.LEFT, padx=(3, 14))
        tk.Label(drag_row, text="Y", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9), width=2).pack(side=tk.LEFT)
        self._var_drag_y = tk.StringVar()
        self._var_drag_y.trace_add("write", lambda *_: self._auto_norm(self._var_drag_y))
        self._numeric_entry(drag_row, self._var_drag_y, width=7).pack(side=tk.LEFT, padx=(3, 0))

        dur_row = tk.Frame(self._frm_drag, bg=_C["bg"])
        dur_row.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(dur_row, text="耗時", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._var_drag_dur = tk.StringVar(value="0.3")
        self._var_drag_dur.trace_add("write", lambda *_: self._auto_norm(self._var_drag_dur))
        self._numeric_entry(dur_row, self._var_drag_dur, width=6).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(dur_row, text="秒", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 0))

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
        if action in _DRAG_ACTIONS:
            self._frm_drag.pack(fill=tk.X, before=self._btn_area)
        else:
            self._frm_drag.pack_forget()
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

        if action in _DRAG_ACTIONS:
            import json as _json
            try:
                tx = int(self._norm(self._var_drag_x.get()))
                ty = int(self._norm(self._var_drag_y.get()))
            except ValueError:
                raise ValueError("終點 X / Y 必須為整數")
            try:
                dur = float(self._norm(self._var_drag_dur.get()) or "0.3")
                if dur <= 0:
                    raise ValueError()
            except ValueError:
                raise ValueError("耗時必須為大於 0 的數字")
            return ClickStep(
                x=x, y=y, count=count, delay=delay,
                action_type="drag",
                extra_json=_json.dumps(
                    {"to_x": tx, "to_y": ty, "duration": dur},
                    ensure_ascii=False,
                ),
            )

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
        self._var_drag_x.set("")
        self._var_drag_y.set("")
        self._var_drag_dur.set("0.3")
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
        if step.action_type in _DRAG_ACTIONS:
            import json as _json
            p = _json.loads(step.extra_json or "{}")
            self._var_drag_x.set(str(p.get("to_x", "")))
            self._var_drag_y.set(str(p.get("to_y", "")))
            self._var_drag_dur.set(str(p.get("duration", 0.3)))
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

    # ── window binding helpers ────────────────────────────────────────────────

    def _build_window_picker(self, parent: tk.Widget, binding: dict, padx: int = 10) -> None:
        """Render a compact window-binding row. Mutates `binding` dict in place."""
        row = tk.Frame(parent, bg=_C["bg"])
        row.pack(fill=tk.X, padx=padx, pady=(4, 2))

        tk.Label(row, text="綁定視窗", bg=_C["bg"], fg=_C["text_muted"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 4))

        win_var = tk.StringVar(value="(不綁定)")
        cb = ttk.Combobox(row, textvariable=win_var, state="readonly",
                          font=("Segoe UI", 8))
        cb.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _refresh():
            from services.window_manager import list_windows
            wins = list_windows()
            win_map: dict[str, int] = {}
            titles = ["(不綁定)"]
            for hwnd, title in wins:
                key = title
                if key in win_map:
                    key = f"{title} [0x{hwnd:X}]"
                win_map[key] = hwnd
                titles.append(key)
            binding["map"] = win_map
            cb["values"] = titles
            if win_var.get() not in titles:
                win_var.set("(不綁定)")
                binding["hwnd"] = None

        def _on_select(_e=None):
            val = win_var.get()
            win_map = binding.get("map", {})
            if val == "(不綁定)" or val not in win_map:
                binding["hwnd"]     = None
                binding["title"]    = None
                binding["ref"]      = (0, 0)
                binding["ref_size"] = None
                return
            hwnd = win_map[val]
            from services.window_manager import get_window_rect
            rect = get_window_rect(hwnd)
            if rect is None:
                messagebox.showwarning("綁定視窗", "無法取得視窗位置，請重新整理")
                win_var.set("(不綁定)")
                return
            binding["hwnd"]     = hwnd
            binding["title"]    = val      # used to re-locate window if hwnd goes stale
            binding["ref"]      = (rect[0], rect[1])
            binding["ref_size"] = (rect[2], rect[3])

        ttk.Button(row, text="↻", style="Ghost.TButton",
                   command=_refresh).pack(side=tk.LEFT, padx=(2, 0))
        cb.bind("<<ComboboxSelected>>", _on_select)
        _refresh()

    def _resolve_bound_window(self, binding: dict) -> int | None:
        """Return a live hwnd for the bound window.
        Tries the cached hwnd first; if stale, searches by title and updates the cache."""
        title = binding.get("title")
        if not title:
            return None
        hwnd = binding.get("hwnd")
        from services.window_manager import is_window_valid, list_windows
        if hwnd and is_window_valid(hwnd):
            return hwnd
        # hwnd gone (window closed & reopened) — search by title
        for h, t in list_windows():
            if t == title:
                binding["hwnd"] = h     # refresh cache
                return h
        return None

    def _get_window_offset(self, binding: dict) -> tuple[int, int]:
        """Return (dx, dy) = current_win_pos - ref_pos. Returns (0,0) if unbound/gone."""
        hwnd = self._resolve_bound_window(binding)
        if hwnd is None:
            return (0, 0)
        from services.window_manager import get_window_rect
        rect = get_window_rect(hwnd)
        if rect is None:
            return (0, 0)
        ref_x, ref_y = binding["ref"]
        return (rect[0] - ref_x, rect[1] - ref_y)

    def _transform_orb_config(self, config, binding: dict):
        """Return a copy of OrbConfig adjusted for window move and/or resize.
        Applies both translation (dx, dy) and scale (sx, sy) so that the board
        region tracks the window even when its size changes.
        Returns the original config unchanged when unbound or window is gone."""
        if not binding.get("title"):
            return config
        hwnd = self._resolve_bound_window(binding)
        if hwnd is None:
            return config
        from services.window_manager import get_window_rect
        rect = get_window_rect(hwnd)
        if rect is None:
            return config
        ref_x, ref_y = binding["ref"]
        ref_size = binding.get("ref_size")
        sx = rect[2] / ref_size[0] if ref_size and ref_size[0] else 1.0
        sy = rect[3] / ref_size[1] if ref_size and ref_size[1] else 1.0
        dx = rect[0] - ref_x
        dy = rect[1] - ref_y
        if dx == 0 and dy == 0 and sx == 1.0 and sy == 1.0:
            return config
        import copy as _copy
        cfg = _copy.copy(config)
        # board_x/y are absolute screen coords at calibration time.
        # new = current_win_origin + scaled_offset_from_ref_origin_to_board
        cfg.board_x = round(rect[0] + (config.board_x - ref_x) * sx)
        cfg.board_y = round(rect[1] + (config.board_y - ref_y) * sy)
        cfg.cell_w  = max(1, round(config.cell_w * sx))
        cfg.cell_h  = max(1, round(config.cell_h * sy))
        return cfg

    @staticmethod
    def _transform_step(step: "ClickStep",
                        ref_x: int, ref_y: int,
                        cur_x: int, cur_y: int,
                        sx: float, sy: float) -> "ClickStep":
        """Return a copy of step with position + scale transform applied.
        Formula: new = cur_win_origin + (coord - ref_origin) * scale"""
        if cur_x == ref_x and cur_y == ref_y and sx == 1.0 and sy == 1.0:
            return step
        import copy, json as _json
        s = copy.copy(step)
        if step.action_type in ("click", "double_click", "right_click", "move"):
            s.x = round(cur_x + (step.x - ref_x) * sx)
            s.y = round(cur_y + (step.y - ref_y) * sy)
        elif step.action_type == "drag":
            s.x = round(cur_x + (step.x - ref_x) * sx)
            s.y = round(cur_y + (step.y - ref_y) * sy)
            if step.extra_json:
                try:
                    extra = _json.loads(step.extra_json)
                    extra["to_x"] = round(cur_x + (extra.get("to_x", 0) - ref_x) * sx)
                    extra["to_y"] = round(cur_y + (extra.get("to_y", 0) - ref_y) * sy)
                    s.extra_json = _json.dumps(extra)
                except Exception:
                    pass
        return s

    # ── execution panel ───────────────────────────────────────────────────────

    def _build_execution_panel(self, parent: ttk.LabelFrame) -> None:
        PX = 10

        # ── Window binding (optional) ─────────────────────────────────────────
        self._build_window_picker(parent, self._tab1_win, PX)
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=PX, pady=(4, 0))

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

        # Pack buttons at BOTTOM first so they always have space
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
            anchor=tk.W, padx=PX, pady=(4, 2))

        self._var_prof_select = tk.StringVar()
        self._cb_profiles = ttk.Combobox(
            parent,
            textvariable=self._var_prof_select,
            state="readonly",
            font=("Segoe UI", 9),
        )
        self._cb_profiles.pack(fill=tk.X, padx=PX, pady=(0, 4))
        self._cb_profiles.bind("<<ComboboxSelected>>", self._on_cb_profile_select)

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
        x, y = str(self._var_mx.get()), str(self._var_my.get())
        # Route to whichever tab is active
        try:
            tab_idx = self._nb.index(self._nb.select())
        except Exception:
            tab_idx = 0
        if tab_idx == 2:  # Tab 3 — 場景腳本: fill fixed coordinate fields
            self._scene_var_click_x.set(x)
            self._scene_var_click_y.set(y)
        else:             # Tab 1 / Tab 2: fill step coordinate fields
            self._var_x.set(x)
            self._var_y.set(y)

    def _capture_region(self) -> None:
        def on_done(path, *_):   # ignore coord args — not needed for image_click
            if not path:
                return
            self._var_img_path.set(path)
            self._update_img_preview(path)
        _RegionSelector(self._root,
                        save_dir=os.path.join(_app_dir(), "images"),
                        on_done=on_done)

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
        steps = list(self._steps)
        if self._tab1_win.get("title"):
            if self._resolve_bound_window(self._tab1_win) is None:
                messagebox.showwarning("綁定視窗", "找不到綁定的視窗，請重新整理並選擇")
                self._btn_start.config(state=tk.NORMAL)
                self._btn_stop.config(state=tk.DISABLED)
                self._btn_record_start.config(state=tk.NORMAL)
                self._set_status("就緒", "idle")
                return
            from services.window_manager import get_window_rect
            rect = get_window_rect(self._resolve_bound_window(self._tab1_win))
            if rect:
                ref_x, ref_y = self._tab1_win["ref"]
                ref_size = self._tab1_win.get("ref_size")
                sx = rect[2] / ref_size[0] if ref_size and ref_size[0] else 1.0
                sy = rect[3] / ref_size[1] if ref_size and ref_size[1] else 1.0
                steps = [self._transform_step(s, ref_x, ref_y, rect[0], rect[1], sx, sy)
                         for s in steps]
        self._executor.start(steps, rounds)
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
        # Called from KeyboardMonitor thread — only threading.Event / plain bool ops here.
        # Tab-1 executor stop (threading.Event — thread-safe)
        if self._executor.is_running:
            self._executor.stop()
        # Tab-2: abort drag immediately and kill loop flag so worker won't reschedule
        if self._orb_executor and self._orb_executor.is_running:
            self._orb_executor.abort()
        self._orb_loop_active = False
        # Tab-3: stop scene runner
        if self._scene_runner and self._scene_runner.is_running:
            self._scene_runner.stop()
        # Tkinter calls must happen on the GUI thread
        self._root.after(0, self._space_stop_cleanup)

    def _space_stop_cleanup(self) -> None:
        """GUI-thread: cancel pending after-timers and reset button state."""
        if self._orb_loop_after:
            self._root.after_cancel(self._orb_loop_after)
            self._orb_loop_after = None
        self._orb_reset_run_btn()
        self._lbl_orb_status.config(text="已停止", fg=_C["text_muted"])
        # Reset Tab-3 scene buttons
        self._scene_stop()

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
        self._cb_profiles["values"] = names
        if self._active_profile in names:
            self._var_prof_select.set(self._active_profile)
        elif names:
            self._var_prof_select.set(names[0])
        else:
            self._var_prof_select.set("")

    def _on_cb_profile_select(self, _e: tk.Event) -> None:
        name = self._var_prof_select.get()
        if name:
            self._var_prof_name.set(name)

    def _load_selected_profile(self) -> None:
        name = self._var_prof_select.get()
        if not name:
            messagebox.showwarning("提示", "請先從下拉選單選取一個操作")
            return
        self._var_prof_name.set(name)
        self._load_profile()

    def _delete_selected_profile(self) -> None:
        name = self._var_prof_select.get()
        if not name:
            messagebox.showwarning("提示", "請先從下拉選單選取一個操作")
            return
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
        self._var_prof_select.set("")
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
