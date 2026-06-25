# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A **Python Desktop Automation Script Engine** — a commercial-grade GUI tool for recording and replaying mouse/keyboard sequences, with additional modes for game orb-solving and overnight scene automation (摩靈 / Tower of Saviors). Windows 10/11 only (uses `ctypes.windll` in `keyboard_monitor.py`).

The app has **three independent functional modes** selectable via tabs in the main window:
- **Tab 1 — 自動化**: record/replay step sequences (the original ClickFlow feature set)
- **Tab 2 — 🔮 轉珠**: real-time screenshot → colour recognition → Beam Search path → mouse drag
- **Tab 3 — 場景腳本**: rule-based automation; polls screen every 0.5 s for template matches and executes the first matching rule (click or orb_solve)

## Common Commands

```bash
pip install -r requirements.txt
python main.py
pyinstaller ClickFlow.spec   # preferred — spec has complete hiddenimports list
```

After every code change: rebuild `dist/ClickFlow.exe` with PyInstaller and push.

Logs are written to `logs/app.log` (created automatically).  
Database is `clicker.db` (created automatically on first run).

## Architecture (strict MVC)

```
main.py               logging init → DatabaseManager → MainWindow → mainloop
models/               pure dataclasses, no logic
  click_step.py       ClickStep — in-memory step; has display_label() for listbox
  profile.py          Profile — DB record mirror
  orb_config.py       OrbConfig — orb-solver calibration settings (board origin, cell size, etc.)
  scene_rule.py       SceneRule — one rule row; action="click"|"orb_solve"; click_dx/click_dy offsets
services/             all business logic, never import from views/
  database_manager.py SQLite CRUD; uses contextmanager for connections
  click_executor.py   worker thread; ExecutionState dataclass lives here too
  keyboard_monitor.py polls GetAsyncKeyState at 50ms (edge-triggered)
  mouse_tracker.py    polls pyautogui.position() at 100ms
  recorder.py         pynput global listeners; Recorder class; F9 stops recording
  orb_board.py        screenshot → Board (2D OrbType array); vectorized HSV via numpy
  orb_solver.py       4-pass Beam Search solver; returns (path, combo_count)
  orb_executor.py     converts path to mouseDown/moveTo/mouseUp drag sequence
  scene_runner.py     SceneRunner daemon thread — 0.5 s poll loop for Tab 3
  window_manager.py   Win32 helpers: list_windows(), get_window_rect(), is_window_valid()
views/
  main_window.py      entire UI; _C palette dict + ttk.Style("clam")
                      module-level: _Mini, _MiniRecorder, _Tip, _RegionSelector helper classes
                      three-tab layout: Tab1=自動化, Tab2=🔮轉珠, Tab3=場景腳本
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
| SceneRunner | `SceneRunner` | `threading.Event` (`_stop_event`) |

`ClickExecutor._interruptible_sleep` loops in 50ms ticks so the stop event is responsive mid-delay. All background threads are daemon threads.

`KeyboardMonitor` fires callbacks on **rising edge only** (prev=False → now=True), preventing repeated triggers while key is held:
- Space → `on_stop()`
- S → `on_capture()`
- F8 → `on_orb_solve()` (triggers Tab 2 orb-solver pipeline)

## Key Design Patterns

**DatabaseManager**: every query uses `_connect()` context manager which commits on exit and rolls back on `sqlite3.Error`. Profiles are upserted with `ON CONFLICT(name) DO UPDATE`. `save_profile` does a full-replace of actions: it DELETEs all existing rows for the profile then re-INSERTs from scratch — there is no incremental update. `save_scene_rules` uses `executemany` for batch INSERT.

**ClickExecutor.start()**: takes a snapshot copy (`list(self._steps)`) so edits to the UI list during execution don't affect the running sequence. `pyautogui.FAILSAFE` is set to `False` at import time — the corner-of-screen abort is intentionally disabled.

**`pyautogui.PAUSE = 0`** is set in `orb_executor.py` at import time. This removes the hidden 0.1 s inter-call delay that pyautogui inserts by default — without it, a 50-step orb drag wastes ~5 s. The `duration=` argument to `moveTo` already controls timing precisely.

**UI thread safety**: `MouseTracker` and `ClickExecutor` callbacks must never touch tkinter widgets directly. Pattern used throughout:
```python
self._root.after(0, lambda: self._var.set(value))
# or
self._root.after(0, self._some_method)
```

**UI build order**: `_build_status_bar()` is packed with `side=tk.BOTTOM` *before* the mid panel is built, so it claims fixed bottom space before the expandable center takes the rest. The same pattern applies inside `_build_execution_panel`: the "載入/刪除" action button row is packed with `side=tk.BOTTOM` *before* the expandable profile listbox, guaranteeing the buttons are always visible.

**Toggle button pattern** (Tab 3 start/stop): a single `_btn_scene_run` widget uses `.config(text=, style=, command=)` to toggle between Start and Stop states. Never use `pack_forget()` + re-`pack()` for side=BOTTOM buttons — re-packing inserts the widget at the END of the slave list, which places it ABOVE other side=BOTTOM items (wrong position).

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

```sql
-- scene automation (Tab 3)
scene_profiles (id, name UNIQUE, created_at, updated_at)
scene_rules (id, profile_id FK, order_idx, name, image_path, action, confidence,
             cooldown, enabled, click_dx, click_dy, click_x, click_y, created_at)
-- orb solver calibration (Tab 2)
orb_configs (id, name UNIQUE, board_x, board_y, cell_w, cell_h,
             rows, cols, drag_speed_ms, beam_width, max_steps,
             created_at, updated_at)
```

`DatabaseManager._initialize()` runs ALTER TABLE migrations on startup to add any columns that predate the current schema (`click_dx`, `click_dy`, `click_x`, `click_y`, `profile_id`), rename the legacy `'預設'` scene profile to `'摩靈傳說'`, and ensure `scene_profiles.id=1` exists.

## Scene Script Pipeline (Tab 3)

**Two built-in profiles** are auto-created on first run:
- **`摩靈傳說`** — full 12-rule preset including orb_solve; default profile
- **`按鈕點擊`** — 11 navigation-only rules (no orb_solve); for the same dungeon

If `'摩靈傳說'` has zero rules on startup, `_scene_load_tos_preset()` populates it automatically. If `'按鈕點擊'` is missing, `_scene_load_click_preset()` creates and populates it. No manual reload button — changes to hardcoded presets in `views/main_window.py` require deleting `clicker.db` or calling the loader methods directly.

**Data flow every 0.5 s**:
```
SceneRunner._loop()
  # startup (once):
  active = [(rule, img_path, label), ...]   # pre-filtered, paths + labels resolved
  cycle_shot = pyautogui.screenshot()       # ONE screenshot shared across all rules
  hwnd/rect refreshed at most once per second

  for rule, img_path, label in active:
    if cooldown not expired: continue

    if rule.action == "orb_solve":
        is_active, board = _board_is_active(orb_cfg)   # separate orb screenshot
        ≥50% filled AND ≥3 distinct colours → proceed
        _flush_click_rules(active, ...)     # pre-solve: dismiss stacked popups
        SetForegroundWindow(hwnd) → sleep 0.15s
        _do_orb_solve(orb_cfg, board=board) # reuses snapshot; no 2nd screenshot
        _flush_click_rules(active, ...)     # post-solve: dismiss mid-drag popups
        _stop_event.wait(3.0)               # wait for combo animation
    else:  # "click"
        _try_click_rule(rule, img_path, region, cycle_shot)
          → pyautogui.locate(img_path, cycle_shot)  # uses shared screenshot
        if matched: SetForegroundWindow → click → break
  if nothing fired: status = "掃描中…"
```

**`_flush_click_rules()`**: loops until a full pass finds nothing (safety cap: 12 passes). Handles N stacked dialogs (確定 → 知道了 → 確定 → …). Ignores cooldowns. Takes one fresh `pyautogui.screenshot()` per pass (not per rule), since the screen changes after each click. `SetForegroundWindow` is called only once per flush session to avoid 0.15 s × N delays.

**`SceneRule.click_dx / click_dy`**: pixel offset from template centre to actual click target. Used when the template covers only part of the clickable area — e.g.:
- `scene_new_badge.png` (82×50 px) covers only the top-left of the dungeon circle: `click_dx=56, click_dy=77` → circle centre
- `scene_stage_new.png` (76×39 px) captures the red NEW text in the stage-list popup: `click_dx=256, click_dy=3` → 進入冒險 button for that row

**`SceneRule.click_x / click_y`** (`Optional[int]`, default `None`): absolute screen coordinates that override the derived click target. Three modes:
- `click_x/y` both `None` → click at `(template_cx + click_dx, template_cy + click_dy)` (original behaviour)
- `click_x/y` set, `image_path` non-empty → image acts as trigger condition; click at fixed `(click_x, click_y)` regardless of where template was found
- `click_x/y` set, `image_path` empty → **pure coordinate click**, fires every cycle (no template check); use long cooldown to control rate

**`_board_is_active()` guard**: returns `(bool, board | None)`. Requires `non_empty >= total // 2 AND len(colours) >= 3`. The colour-diversity check prevents false-positive orb-solve triggers when the game shows a monochrome map background. The returned board is passed directly to `_do_orb_solve()` to avoid a second screenshot.

**Cooldown key**: `cooldowns[rule.db_id or rule.order_idx]` — uses the stable DB primary key, not `id(rule)`. Using `id()` would silently reset all cooldowns whenever the rule list is rebuilt.

**Post-solve wait**: after `OrbExecutor` finishes, the loop calls `_stop_event.wait(3.0)` before resuming — gives combo animations time to complete so click rules (確定/知道了) don't misfire during the resolve sequence.

**`SetForegroundWindow` delay**: both click and orb_solve paths sleep 0.15 s after focusing the Flash window — Flash Player won't accept mouse events from `pyautogui` without it.

**Preset templates** live in `dist/images/scene/` (shipped alongside the exe, not embedded). `_app_dir()` resolves paths relative to the exe when frozen, relative to the project root when running from source. The current 摩靈傳說 preset (12 rules, checked top-to-bottom):

| Priority | Rule name | Template | Action | click_dx | click_dy |
|---|---|---|---|---|---|
| 1 | 珠盤就緒 | scene_battle_banner.png | orb_solve | — | — |
| 2 | 斷線重連 | scene_btn_confirm.png | click | 0 | 0 |
| 3 | 知道了 | scene_btn_zhidaole.png | click | 0 | 0 |
| 4 | 知道了(1h提示) | scene_btn_zhidaole_1h.png | click | 0 | 0 |
| 5 | 確定(升級) | scene_btn_ok.png | click | 0 | 0 |
| 6 | 確定(獎勵) | scene_btn_ok2.png | click | 0 | 0 |
| 7 | 選第一個盟友 | scene_select_ally.png | click | 0 | 0 |
| 8 | 進入NEW關卡 | scene_stage_new.png | click | 256 | 3 |
| 9 | 點擊NEW地城 | scene_new_badge.png | click | 56 | 77 |
| 10 | 翻下一頁 | scene_btn_nextpage.png | click | 0 | 0 |
| 11 | 點冒險地圖 | scene_btn_adventure.png | click | 0 | 0 |
| 12 | 點摩靈按鈕 | scene_btn_maling.png | click | 0 | 0 |

`scene_btn_zhidaole_1h.png` (157×46 px) — the orange/red-bordered "知道了" button from the hourly online-time notification popup. Separate from `scene_btn_zhidaole.png` which was captured from a different game dialog context.

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
OrbBoard.snapshot()          # pyautogui.screenshot → crop → vectorized HSV (numpy) → Board
    │  Board = list[list[str]]   constants: FIRE/WATER/WOOD/LIGHT/DARK/HEART/EMPTY="?"
    │  All 30 cells classified in one numpy pass (stack → HSV mask → saturation vote)
    ▼
OrbSolver.solve(board, time_limit=12.0)   # 4-pass beam search within 12s budget
    │  Pass 1: base_bw,  all non-EMPTY starts — fast baseline
    │  Pass 2: 6×  bw,   all starts sorted by pass-1 score
    │  Pass 3: 20× bw,   top-10 starts only
    │  Pass 4: 40× bw,   top-5  starts only (deep refinement)
    │  heapq.nlargest selects top-K candidates (O(N log K) vs sort's O(N log N))
    │  score_board() results cached per call (module-level _score_cache dict)
    │  returns (List[Tuple[int,int]], combo_count)
    ▼
OrbExecutor.run(path)        # mouseDown → moveTo × N → mouseUp  (daemon thread)
    │  pyautogui.PAUSE = 0 at module import — eliminates hidden 0.1s per-call overhead
```

**OrbConfig** (`models/orb_config.py`) stores calibration: `board_x/y`, `cell_w/h`, `rows`, `cols`, `drag_speed_ms`, `beam_width`, `max_steps`. Persisted via `DatabaseManager` to the `orb_configs` table. `board_x/y` are **screen coordinates** — must be re-calibrated any time the Flash Player window moves.

**Colour recognition**: all cells are classified in a single vectorized numpy pass inside `OrbBoard.recognize()`. Each cell's centre 30–65% crop is stacked into a `(N, H, W, 3)` array, converted to HSV in one call, then saturation-weighted voting determines the orb type for all cells simultaneously. Cells with no pixels above S>100 threshold → `EMPTY` (`"?"`). Current hue ranges (OpenCV H 0–179): FIRE `(0–12, 170–179)`, LIGHT `(13–44)`, WOOD `(44–92)`, WATER `(92–128)`, DARK `(128–152)`, HEART `(152–170)`. LIGHT and WOOD share hue 44 — saturation vote decides; no gap between them.

**score_board cache**: `_score_cache` is a module-level `dict` cleared with `.clear()` at the start of each `solve()` call (keeps the same dict object so external refs remain valid). Many beam paths converge to identical board states; caching avoids redundant combo simulation.

**Flash focus requirement**: `SetForegroundWindow` must be called on the game window before executing orb drags — Flash Player ignores `pyautogui` mouse events when not the foreground window. `SceneRunner` does this for both click rules and orb_solve rules before acting.

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
