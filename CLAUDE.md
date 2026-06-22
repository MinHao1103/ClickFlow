# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A **Python Desktop Automation Script Engine** — a commercial-grade GUI tool for recording and replaying mouse/keyboard sequences, with a second mode for game orb-solving (摩靈 / Tower of Saviors style). Windows 10/11 only (uses `ctypes.windll` in `keyboard_monitor.py`).

The app has **two independent functional modes** selectable via tabs in the main window:
- **Tab 1 — 自動化**: record/replay step sequences (the original ClickFlow feature set)
- **Tab 2 — 🔮 轉珠**: real-time screenshot → colour recognition → Beam Search path → mouse drag

## Common Commands

```bash
pip install -r requirements.txt
python main.py
pyinstaller ClickFlow.spec   # preferred — spec has complete hiddenimports list
```

Logs are written to `logs/app.log` (created automatically).  
Database is `clicker.db` (created automatically on first run).

## Architecture (strict MVC)

```
main.py               logging init → DatabaseManager → MainWindow → mainloop
models/               pure dataclasses, no logic
  click_step.py       ClickStep — in-memory step; has display_label() for listbox
  profile.py          Profile — DB record mirror
  orb_config.py       OrbConfig — orb-solver calibration settings (board origin, cell size, etc.)
services/             all business logic, never import from views/
  database_manager.py SQLite CRUD; uses contextmanager for connections
  click_executor.py   worker thread; ExecutionState dataclass lives here too
  keyboard_monitor.py polls GetAsyncKeyState at 50ms (edge-triggered)
  mouse_tracker.py    polls pyautogui.position() at 100ms
  recorder.py         pynput global listeners; Recorder class; F9 stops recording
  orb_board.py        screenshot → Board (2D OrbType array); colour recognition via HSV
  orb_solver.py       Beam Search solver; returns List[(row,col)] path maximising combos
  orb_executor.py     converts path to mouseDown/moveTo/mouseUp drag sequence
  window_manager.py   Win32 helpers: list_windows(), get_window_rect(), is_window_valid()
views/
  main_window.py      entire UI; _C palette dict + ttk.Style("clam")
                      module-level: _Mini, _MiniRecorder, _Tip, _RegionSelector helper classes
                      two-tab layout: Tab1=自動化, Tab2=🔮轉珠
  orb_calibrate.py    Toplevel for board area selection, grid preview, colour-recognition test
```

**Rule**: business logic never enters `views/`. All thread-to-GUI communication must go through `root.after(0, fn)`.

**Orb-solver isolation rule**: `services/orb_*.py` are completely independent of the automation pipeline. They share only `pyautogui`, `PIL`, and `models/orb_config.py`. They never import `ClickStep`, `ClickExecutor`, or anything from `views/`.

## Threading Model

| Thread | Name | Stop mechanism |
|--------|------|----------------|
| GUI | main | `root.mainloop()` |
| ClickExecutor | `ClickExecutor` | `threading.Event` (`_stop_event`) |
| MouseTracker | `MouseTracker` | `self._running = False` |
| KeyboardMonitor | `KeyboardMonitor` | `self._running = False` |
| OrbExecutor | `OrbExecutor` | `threading.Event` (`_stop_event`); always calls `mouseUp()` on abort |

`ClickExecutor._interruptible_sleep` loops in 50ms ticks so the stop event is responsive mid-delay. All three background threads are daemon threads.

`KeyboardMonitor` fires callbacks on **rising edge only** (prev=False → now=True), preventing repeated triggers while key is held:
- Space → `on_stop()`
- S → `on_capture()`
- F8 → `on_orb_solve()` (triggers Tab 2 orb-solver pipeline)

## Key Design Patterns

**DatabaseManager**: every query uses `_connect()` context manager which commits on exit and rolls back on `sqlite3.Error`. Profiles are upserted with `ON CONFLICT(name) DO UPDATE`. `save_profile` does a full-replace of actions: it DELETEs all existing rows for the profile then re-INSERTs from scratch — there is no incremental update.

**ClickExecutor.start()**: takes a snapshot copy (`list(self._steps)`) so edits to the UI list during execution don't affect the running sequence. `pyautogui.FAILSAFE` is set to `False` at import time — the corner-of-screen abort is intentionally disabled.

**UI thread safety**: `MouseTracker` and `ClickExecutor` callbacks must never touch tkinter widgets directly. Pattern used throughout:
```python
self._root.after(0, lambda: self._var.set(value))
# or
self._root.after(0, self._some_method)
```

**UI build order**: `_build_status_bar()` is packed with `side=tk.BOTTOM` *before* the mid panel is built, so it claims fixed bottom space before the expandable center takes the rest. The same pattern applies inside `_build_execution_panel`: the "載入/刪除" action button row is packed with `side=tk.BOTTOM` *before* the expandable profile listbox, guaranteeing the buttons are always visible.

**Step list empty state**: uses two sibling frames (`_frame_empty` / `_frame_list`) in the same wrapper; `pack_forget()` / `pack()` toggle between them in `_refresh_list()`.

**hotkey execution**: keys are split on `+` then unpacked to `pyautogui.hotkey()` — e.g. `ctrl+shift+s` → `pyautogui.hotkey("ctrl", "shift", "s")`.

**IME decimal normalization — two layers**:
1. `_disable_ime(widget)` calls `ctypes.windll.imm32.ImmAssociateContext(hwnd, 0)` to fully disassociate Windows IME from a widget on `<Map>`. `_numeric_entry(parent, textvariable, **kw)` wraps `ttk.Entry` and attaches this binding; all six numeric fields (X, Y, count, delay, max_delay, rounds) are created through it.
2. `_auto_norm(var)` is still registered as a `trace_add("write", ...)` fallback that converts `。`/`．` → `.` on any write. `_norm()` (static) does the same plus `.strip()` and is called before `float()`/`int()` parsing in `_parse_step()` and `_start_recording()`.

## Window Binding (Tab 1)

Tab 1 supports binding a profile's playback to a named OS window so that step coordinates track the window if it is moved between recording and replay.

- `MainWindow._build_window_binding_row()` renders a Combobox populated by `window_manager.list_windows()`. On selection, `get_window_rect()` captures the window's top-left as the reference position (`binding["ref"]`).
- At execution time, `_get_window_offset(binding)` calls `_resolve_bound_window()` → `get_window_rect()` and returns `(dx, dy)` = current position − reference position.
- `_offset_step(step, dx, dy)` returns a shallow-copied step with coordinates shifted; only coord-bearing action types (`click`, `double_click`, `right_click`, `move`, `drag`) are adjusted.
- `_resolve_bound_window()` refreshes a stale `hwnd` by re-scanning window titles, so playback survives the target app being restarted.
- No binding (`不綁定`) leaves `binding["hwnd"]` as `None` and offset as `(0, 0)`, a no-op.

## Overlay Windows

Two `tk.Toplevel` overlay windows live as module-level classes in `views/main_window.py`:

**`_Mini`** (230×112 px) — shown during execution:
- `wm_attributes("-topmost", True)` + `overrideredirect(True)` + `alpha=0.93`
- Displays ↺ 輪 / ▶ 步驟 / ◎ 點擊 live counters bound to `StringVar`s owned by `MainWindow`
- Draggable via `<ButtonPress-1>` / `<B1-Motion>` on the header frame
- `MainWindow._show_mini()` hides main window and shows overlay; `_close_mini()` reverses

**`_MiniRecorder`** (300×340 px) — shown during recording:
- Same topmost/frameless/alpha setup
- Contains a scrollable `Listbox` that receives every recorded step via `update(step, total)`
- New row flashes green (`success_bg`/`success` colours) for 400 ms then resets to alternating row colours
- `on_rect_changed(x, y, w, h)` callback fires on every drag event so `MainWindow` can update `Recorder._app_rect` in real time, preventing recorded clicks landing on the overlay
- `MainWindow._show_mini_recorder()` initialises the overlay with a 150 ms `after` delay for `winfo_rootx/y` to settle, then passes the rect to `Recorder`

## Status Bar States

`_set_status(msg, state)` accepts five states:
- `"idle"` — muted text on dark card bg
- `"run"` — bright green text on very dark green bg
- `"done"` — lighter green text on dark green bg
- `"error"` — soft red text on very dark red bg
- `"record"` — soft red text on very dark red bg

## Database Schema

```sql
profiles (id, name UNIQUE, description, created_at, updated_at)
actions  (id, profile_id FK, order_idx, action_type, x, y,
          click_count, delay_seconds, keyboard_text, extra_json, created_at)
```

Action types: `click`, `double_click`, `right_click`, `move`, `delay`, `keyboard_input`, `hotkey`, `image_click`, `drag`

`drag` uses `extra_json`: `{"to_x": int, "to_y": int, "duration": float}` — start coord from `x`/`y` fields.

`image_click` uses `extra_json`: `{"path": str, "confidence": float, "timeout": float}`.

Reserved for future extension via `extra_json`: `OCR`, conditional logic, random delay.

```sql
-- orb solver calibration (Tab 2)
orb_configs (id, name UNIQUE, board_x, board_y, cell_w, cell_h,
             rows, cols, drag_speed_ms, beam_width, max_steps,
             created_at, updated_at)
```

## Recorder Behaviour

`Recorder` (pynput global listeners) runs during recording and is distinct from `KeyboardMonitor`. When recording starts, `KeyboardMonitor` is stopped to avoid conflicts; it restarts when recording stops.

Key details:
- **App-rect exclusion**: clicks whose screen coordinates fall inside the app window are silently dropped (`_in_app_rect`). During recording, the mini recorder overlay's rect is also excluded and updated in real time on drag.
- **Double-click detection**: a left click is held for up to 300 ms (`_DOUBLE_CLICK_INTERVAL`). If a second click arrives within 300 ms and within 5 px (`_DOUBLE_CLICK_MAX_DIST`), both are merged into one `double_click` step; otherwise the first is emitted as `click` and the timer resets.
- **Move throttling**: `_on_move` skips events where both Δx and Δy are less than `_MOVE_MIN_DIST` (10 px) relative to the last pending move step, preventing excessive steps.
- **Key buffering**: consecutive printable characters (no modifiers held) accumulate in `_key_buffer` and are flushed as a single `keyboard_input` step when a mouse event, hotkey, or `stop()` interrupts the run.
- **Hotkey emission**: modifier state is tracked in a `set`; when a non-modifier key is pressed with modifiers held, a `hotkey` step is emitted with keys in `ctrl → alt → shift → key` order.
- **Delay capping**: inter-event delays are capped at `max_delay` (default 5 s) and floored at 50 ms (below that, delay is recorded as 0).
- **F9** stops recording from within the pynput keyboard thread by spawning a daemon thread that calls `Recorder.stop()`.

## Orb Solver Pipeline (Tab 2)

Full spec: `docs/orb_solver_spec.md`. Summary of the data flow:

```
F8 / UI button
    │
    ▼
OrbBoard.snapshot()          # pyautogui.screenshot → crop → HSV per cell → Board
    │  Board = list[list[OrbType]]   OrbType: FIRE/WATER/WOOD/LIGHT/DARK/HEART/EMPTY
    ▼
OrbSolver.solve(board)       # Beam Search (width=10, max_steps=30)
    │  returns List[Tuple[int,int]]  — (row,col) sequence
    ▼
OrbExecutor.run(path)        # mouseDown → moveTo × N → mouseUp  (daemon thread)
```

**OrbConfig** (`models/orb_config.py`) stores calibration: `board_x/y`, `cell_w/h`, `rows`, `cols`, `drag_speed_ms`, `beam_width`, `max_steps`. Persisted via `DatabaseManager` to the `orb_configs` table.

**Colour recognition**: each cell's centre 60% crop → average HSV → match against `ORB_HSV` hue ranges. Cells with low saturation → `EMPTY`.

**Combo scoring**: simulate gravity-drop loop after placing orb; count total match rounds. 6+ same-colour in one round counts as 2 combos.

**Drag execution**: `drag_speed_ms` (default 25 ms) per cell. Always calls `mouseUp()` even on abort via `_stop_event`.

**Calibration UI** (`views/orb_calibrate.py`): reuses `_RegionSelector` to let user drag-select the board area, then overlays a colour-coded grid preview after recognition test.

## UI Style System

`MainWindow._apply_styles()` sets `ttk.Style` theme to `"clam"` (required for custom button colours on Windows) and defines named button variants: `Accent`, `Start`, `Stop`, `Ghost`, `GhostDanger`, `Record`.

All colours are in the `_C` dict at the top of `views/main_window.py`. The current theme is a **deep navy dark theme**:
- `bg` `#0f172a` — main background
- `card` `#1e293b` — panel / card surfaces
- `accent` `#818cf8` — light indigo (readable on dark)
- `success` `#4ade80`, `danger` `#f87171`, `warning` `#fbbf24`, `purple` `#c084fc`, `teal` `#22d3ee`

`_ACTION_FG` (module-level dict) maps each action type to its listbox foreground colour using `_C` references — it is evaluated at import time, so it always matches the active palette.

Combobox dropdown listbox colours are set via `self._root.option_add('*TCombobox*Listbox*...', ...)` inside `_apply_styles()` because ttk styles don't reach the popup widget.

`ClickStep.display_label()` uses `f"{delay:g}"` to suppress trailing `.0` (2.0 → "2").
