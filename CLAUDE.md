# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A **Python Desktop Automation Script Engine** — a commercial-grade GUI tool for recording and replaying mouse/keyboard sequences. Windows 10/11 only (uses `ctypes.windll` in `keyboard_monitor.py`).

## Common Commands

```bash
pip install -r requirements.txt
python main.py
pyinstaller --onefile --windowed --name AutomationScriptEngine main.py
```

Logs are written to `logs/app.log` (created automatically).  
Database is `clicker.db` (created automatically on first run).

## Architecture (strict MVC)

```
main.py               logging init → DatabaseManager → MainWindow → mainloop
models/               pure dataclasses, no logic
  click_step.py       ClickStep — in-memory step; has display_label() for listbox
  profile.py          Profile — DB record mirror
services/             all business logic, never import from views/
  database_manager.py SQLite CRUD; uses contextmanager for connections
  click_executor.py   worker thread; ExecutionState dataclass lives here too
  keyboard_monitor.py polls GetAsyncKeyState at 50ms (edge-triggered)
  mouse_tracker.py    polls pyautogui.position() at 100ms
  recorder.py         pynput global listener; Recorder class; F9 stops recording
views/
  main_window.py      entire UI; _C palette dict + ttk.Style("clam"); _Tip tooltip
```

**Rule**: business logic never enters `views/`. All thread-to-GUI communication must go through `root.after(0, fn)`.

## Threading Model

| Thread | Name | Stop mechanism |
|--------|------|----------------|
| GUI | main | `root.mainloop()` |
| ClickExecutor | `ClickExecutor` | `threading.Event` (`_stop_event`) |
| MouseTracker | `MouseTracker` | `self._running = False` |
| KeyboardMonitor | `KeyboardMonitor` | `self._running = False` |

`ClickExecutor._interruptible_sleep` loops in 50ms ticks so the stop event is responsive mid-delay. All three background threads are daemon threads.

`KeyboardMonitor` fires callbacks on **rising edge only** (prev=False → now=True), preventing repeated triggers while key is held:
- Space → `on_stop()`
- S → `on_capture()`

## Key Design Patterns

**DatabaseManager**: every query uses `_connect()` context manager which commits on exit and rolls back on `sqlite3.Error`. Profiles are upserted with `ON CONFLICT(name) DO UPDATE`. `save_profile` does a full-replace of actions: it DELETEs all existing rows for the profile then re-INSERTs from scratch — there is no incremental update.

**ClickExecutor.start()**: takes a snapshot copy (`list(self._steps)`) so edits to the UI list during execution don't affect the running sequence. `pyautogui.FAILSAFE` is set to `False` at import time — the corner-of-screen abort is intentionally disabled.

**UI thread safety**: `MouseTracker` and `ClickExecutor` callbacks must never touch tkinter widgets directly. Pattern used throughout:
```python
self._root.after(0, lambda: self._var.set(value))
# or
self._root.after(0, self._some_method)
```

**UI build order**: `_build_status_bar()` is packed with `side=tk.BOTTOM` *before* the mid panel is built, so it claims fixed bottom space before the expandable center takes the rest.

**Step list empty state**: uses two sibling frames (`_frame_empty` / `_frame_list`) in the same wrapper; `pack_forget()` / `pack()` toggle between them in `_refresh_list()`.

**hotkey execution**: keys are split on `+` then unpacked to `pyautogui.hotkey()` — e.g. `ctrl+shift+s` → `pyautogui.hotkey("ctrl", "shift", "s")`.

## Status Bar States

`_set_status(msg, state)` accepts five states:
- `"idle"` — gray (app ready / just started)
- `"run"` — green (executing)
- `"done"` — dark green (completed successfully)
- `"error"` — red (exception occurred)
- `"record"` — red tint (recording in progress)

## Database Schema

```sql
profiles (id, name UNIQUE, description, created_at, updated_at)
actions  (id, profile_id FK, order_idx, action_type, x, y,
          click_count, delay_seconds, keyboard_text, extra_json, created_at)
```

Action types: `click`, `double_click`, `right_click`, `move`, `delay`, `keyboard_input`, `hotkey`  
Reserved for future extension via `extra_json`: `image_search`, `OCR`, conditional logic, random delay.

## Recorder Behaviour

`Recorder` (pynput global listeners) runs during recording and is distinct from `KeyboardMonitor`. When recording starts, `KeyboardMonitor` is stopped to avoid conflicts; it restarts when recording stops.

Key details:
- **App-rect exclusion**: clicks whose screen coordinates fall inside the app window are silently dropped (`_in_app_rect`).
- **Double-click detection**: a left click is held for up to 300 ms (`_DOUBLE_CLICK_INTERVAL`). If a second click arrives within 300 ms and within 5 px (`_DOUBLE_CLICK_MAX_DIST`), both are merged into one `double_click` step; otherwise the first is emitted as `click` and the timer resets.
- **Key buffering**: consecutive printable characters (no modifiers held) accumulate in `_key_buffer` and are flushed as a single `keyboard_input` step when a mouse event, hotkey, or `stop()` interrupts the run.
- **Hotkey emission**: modifier state is tracked in a `set`; when a non-modifier key is pressed with modifiers held, a `hotkey` step is emitted with keys in `ctrl → alt → shift → key` order.
- **Delay capping**: inter-event delays are capped at `max_delay` (default 5 s) and floored at 50 ms (below that, delay is recorded as 0).
- **F9** stops recording from within the pynput keyboard thread by spawning a daemon thread that calls `Recorder.stop()`.

## UI Style System

`MainWindow._apply_styles()` sets `ttk.Style` theme to `"clam"` (required for custom button colours on Windows) and defines named button variants: `Accent`, `Start`, `Stop`, `Ghost`, `GhostDanger`, `Record`.

All colours are in the `_C` dict at the top of `views/main_window.py`. Edit there to retheme.

`ClickStep.display_label()` uses `f"{delay:g}"` to suppress trailing `.0` (2.0 → "2").
