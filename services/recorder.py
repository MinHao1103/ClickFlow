import threading
import time
import logging
from typing import Callable, Optional

from pynput import mouse as pmouse, keyboard as pkeyboard

from models.click_step import ClickStep

logger = logging.getLogger(__name__)

_DOUBLE_CLICK_INTERVAL = 0.3   # seconds
_DOUBLE_CLICK_MAX_DIST = 5     # pixels

# Normalised names for modifier keys
_MODIFIER_KEYS: dict = {}

def _build_modifier_map() -> dict:
    m = {}
    for attr, name in (
        ("ctrl",    "ctrl"), ("ctrl_l",  "ctrl"), ("ctrl_r",  "ctrl"),
        ("alt",     "alt"),  ("alt_l",   "alt"),  ("alt_r",   "alt"),
        ("alt_gr",  "alt"),
        ("shift",   "shift"), ("shift_l", "shift"), ("shift_r", "shift"),
    ):
        try:
            m[getattr(pkeyboard.Key, attr)] = name
        except AttributeError:
            pass
    return m

_MODIFIER_KEYS = _build_modifier_map()

# Preferred output order when building hotkey strings
_MOD_ORDER = ["ctrl", "alt", "shift"]


class Recorder:
    def __init__(
        self,
        on_step: Callable[[ClickStep], None],
        on_stopped: Callable[[], None],
        app_rect: tuple,          # (x, y, w, h) of the app window
        max_delay: float = 5.0,
        record_move: bool = False,
    ) -> None:
        self._on_step = on_step
        self._on_stopped = on_stopped
        self._app_rect = app_rect
        self._max_delay = max_delay
        self._record_move = record_move

        self._recording = False
        self._lock = threading.Lock()

        # Core timing / buffering state
        self._pending_step: Optional[ClickStep] = None
        self._last_time: float = 0.0

        self._key_buffer: list[str] = []
        self._key_buffer_start: float = 0.0   # monotonic time of first char in current run
        self._key_buffer_end: float = 0.0     # monotonic time of last char in current run

        self._modifiers: set[str] = set()

        # Double-click detection
        self._pending_click: Optional[tuple] = None   # (event_time, x, y)
        self._pending_click_timer: Optional[threading.Timer] = None

        self._mouse_listener: Optional[pmouse.Listener] = None
        self._keyboard_listener: Optional[pkeyboard.Listener] = None

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def is_recording(self) -> bool:
        return self._recording

    def start(self) -> None:
        if self._recording:
            return
        self._recording = True
        self._last_time = time.monotonic()
        self._pending_step = None
        self._key_buffer.clear()
        self._modifiers.clear()
        self._pending_click = None
        self._pending_click_timer = None

        self._mouse_listener = pmouse.Listener(
            on_click=self._on_click,
            on_move=self._on_move if self._record_move else None,
        )
        self._keyboard_listener = pkeyboard.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()
        logger.info("Recorder started (max_delay=%.1f record_move=%s)", self._max_delay, self._record_move)

    def stop(self) -> None:
        with self._lock:
            if not self._recording:
                return
            self._recording = False

            # Cancel any pending double-click timer
            if self._pending_click_timer:
                self._pending_click_timer.cancel()
                self._pending_click_timer = None

            # Flush pending single click
            if self._pending_click:
                t, x, y = self._pending_click
                self._pending_click = None
                self._do_emit(ClickStep(action_type="click", x=x, y=y), t)

            # Flush accumulated key buffer
            self._flush_key_buffer()

            # Emit the last pending step with its remaining delay (0.0)
            if self._pending_step is not None:
                self._on_step(self._pending_step)
                self._pending_step = None

        # Stop pynput listeners outside the lock (they may call back into our handlers)
        if self._mouse_listener:
            self._mouse_listener.stop()
            self._mouse_listener = None
        if self._keyboard_listener:
            self._keyboard_listener.stop()
            self._keyboard_listener = None

        self._on_stopped()
        logger.info("Recorder stopped")

    # ── internal helpers ──────────────────────────────────────────────────────

    def _in_app_rect(self, x: int, y: int) -> bool:
        ax, ay, aw, ah = self._app_rect
        return ax <= x <= ax + aw and ay <= y <= ay + ah

    def _calc_delay(self, event_time: float) -> float:
        delay = event_time - self._last_time
        delay = min(delay, self._max_delay)
        return 0.0 if delay < 0.05 else round(delay, 2)

    def _do_emit(self, step: ClickStep, event_time: float) -> None:
        """Finalise the previous pending step's delay, then queue *step* as the new pending one."""
        delay = self._calc_delay(event_time)
        if self._pending_step is not None:
            self._pending_step.delay = delay
            self._on_step(self._pending_step)
        self._pending_step = step
        self._last_time = event_time

    def _flush_key_buffer(self) -> None:
        """Emit accumulated keyboard text as a keyboard_input step (must hold _lock)."""
        if not self._key_buffer:
            return
        text = "".join(self._key_buffer)
        self._key_buffer.clear()
        self._do_emit(
            ClickStep(action_type="keyboard_input", keyboard_text=text),
            self._key_buffer_end,   # use time of last keypress so delay-after-typing is accurate
        )

    def _flush_pending_click(self) -> None:
        """Flush pending click as a single click (must hold _lock)."""
        if self._pending_click_timer:
            self._pending_click_timer.cancel()
            self._pending_click_timer = None
        if self._pending_click:
            t, x, y = self._pending_click
            self._pending_click = None
            self._do_emit(ClickStep(action_type="click", x=x, y=y), t)

    # ── mouse callbacks (pynput mouse thread) ─────────────────────────────────

    def _on_click(self, x: int, y: int, button: pmouse.Button, pressed: bool) -> None:
        if not pressed or not self._recording:
            return
        if self._in_app_rect(x, y):
            return

        event_time = time.monotonic()

        with self._lock:
            if button == pmouse.Button.right:
                self._flush_key_buffer()
                self._flush_pending_click()
                self._do_emit(ClickStep(action_type="right_click", x=x, y=y), event_time)

            elif button == pmouse.Button.left:
                self._flush_key_buffer()

                if self._pending_click is not None:
                    prev_t, prev_x, prev_y = self._pending_click
                    close_in_time = (event_time - prev_t) < _DOUBLE_CLICK_INTERVAL
                    close_in_space = (abs(x - prev_x) < _DOUBLE_CLICK_MAX_DIST and
                                      abs(y - prev_y) < _DOUBLE_CLICK_MAX_DIST)
                    if close_in_time and close_in_space:
                        # Upgrade to double_click
                        if self._pending_click_timer:
                            self._pending_click_timer.cancel()
                            self._pending_click_timer = None
                        self._pending_click = None
                        self._do_emit(
                            ClickStep(action_type="double_click", x=prev_x, y=prev_y),
                            prev_t,
                        )
                        return
                    else:
                        # Second click is too far/late — flush first as single click
                        self._flush_pending_click()

                # Queue this click; wait to see if a second follows
                self._pending_click = (event_time, x, y)
                timer = threading.Timer(_DOUBLE_CLICK_INTERVAL, self._timer_flush_pending_click)
                self._pending_click_timer = timer
                timer.daemon = True
                timer.start()

    def _timer_flush_pending_click(self) -> None:
        """Timer callback: 300 ms passed, so the queued click is a true single click."""
        with self._lock:
            if not self._recording or self._pending_click is None:
                return
            self._pending_click_timer = None
            t, x, y = self._pending_click
            self._pending_click = None
            self._do_emit(ClickStep(action_type="click", x=x, y=y), t)

    def _on_move(self, x: int, y: int) -> None:
        if not self._recording:
            return
        event_time = time.monotonic()
        with self._lock:
            self._do_emit(ClickStep(action_type="move", x=x, y=y), event_time)

    # ── keyboard callbacks (pynput keyboard thread) ───────────────────────────

    def _on_key_press(self, key) -> None:
        if not self._recording:
            return

        # Modifier key → track and return
        if key in _MODIFIER_KEYS:
            self._modifiers.add(_MODIFIER_KEYS[key])
            return

        # F9 → stop recording (do NOT record the key itself)
        if key == pkeyboard.Key.f9:
            threading.Thread(target=self.stop, daemon=True).start()
            return

        event_time = time.monotonic()

        # Hotkey (modifier held + any key)
        if self._modifiers:
            key_name = self._key_to_name(key)
            if key_name:
                hotkey_str = "+".join(
                    [m for m in _MOD_ORDER if m in self._modifiers] + [key_name]
                )
                with self._lock:
                    self._flush_key_buffer()
                    self._do_emit(
                        ClickStep(action_type="hotkey", keyboard_text=hotkey_str),
                        event_time,
                    )
            return

        # Printable character (no modifiers)
        char = self._printable_char(key)
        if char:
            with self._lock:
                if not self._key_buffer:
                    self._key_buffer_start = event_time
                self._key_buffer_end = event_time
                self._key_buffer.append(char)

    def _on_key_release(self, key) -> None:
        if key in _MODIFIER_KEYS:
            self._modifiers.discard(_MODIFIER_KEYS[key])

    # ── key name helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _printable_char(key) -> Optional[str]:
        if isinstance(key, pkeyboard.KeyCode):
            c = key.char
            if c and c.isprintable():
                return c
        return None

    @staticmethod
    def _key_to_name(key) -> Optional[str]:
        """Return a pyautogui-compatible name for *key* (used in hotkey strings)."""
        if isinstance(key, pkeyboard.Key):
            return key.name   # e.g. 'f1', 'delete', 'enter'
        if isinstance(key, pkeyboard.KeyCode):
            # Prefer the raw char (e.g. 'c') over the possibly-control-mangled version
            if key.vk is not None:
                c = chr(key.vk).lower()
                if c.isalpha() or c.isdigit():
                    return c
            if key.char and key.char.isprintable():
                return key.char.lower()
        return None
