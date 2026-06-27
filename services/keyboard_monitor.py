import ctypes
import threading
import time
import logging
from typing import Callable

logger = logging.getLogger(__name__)

_VK_SPACE = 0x20
_VK_F8    = 0x77
_VK_F11   = 0x7A
_VK_F10   = 0x79
_VK_F9    = 0x78


class KeyboardMonitor:
    """Polls keyboard state via GetAsyncKeyState at 50ms intervals."""

    def __init__(
        self,
        on_stop: Callable[[], None],
        on_capture: Callable[[], None],
        on_orb_solve: Callable[[], None] | None = None,
        on_record_toggle: Callable[[], None] | None = None,
        on_run_toggle: Callable[[], None] | None = None,
    ):
        self._on_stop          = on_stop
        self._on_capture       = on_capture
        self._on_orb_solve     = on_orb_solve
        self._on_record_toggle = on_record_toggle
        self._on_run_toggle    = on_run_toggle
        self._running = False
        self._thread: threading.Thread | None = None
        self._user32 = ctypes.windll.user32
        self._space_prev = False
        self._f8_prev    = False
        self._f11_prev   = False
        self._f10_prev   = False
        self._f9_prev    = False

    def start(self) -> None:
        if self._running:
            return
        # Sync initial state to prevent edge-triggering if keys are already held down
        try:
            self._space_prev = self._pressed(_VK_SPACE)
            self._f8_prev    = self._pressed(_VK_F8)
            self._f11_prev   = self._pressed(_VK_F11)
            self._f10_prev   = self._pressed(_VK_F10)
            self._f9_prev    = self._pressed(_VK_F9)
        except Exception:
            pass
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="KeyboardMonitor"
        )
        self._thread.start()
        logger.debug("KeyboardMonitor started")

    def stop(self) -> None:
        self._running = False

    def _pressed(self, vk: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(vk) & 0x8000)

    def _run(self) -> None:
        while self._running:
            try:
                space_now = self._pressed(_VK_SPACE)
                if space_now and not self._space_prev:
                    self._on_stop()
                self._space_prev = space_now

                f8_now = self._pressed(_VK_F8)
                if f8_now and not self._f8_prev:
                    self._on_capture()
                self._f8_prev = f8_now

                f11_now = self._pressed(_VK_F11)
                if f11_now and not self._f11_prev and self._on_orb_solve:
                    self._on_orb_solve()
                self._f11_prev = f11_now

                f10_now = self._pressed(_VK_F10)
                if f10_now and not self._f10_prev and self._on_run_toggle:
                    self._on_run_toggle()
                self._f10_prev = f10_now

                f9_now = self._pressed(_VK_F9)
                if f9_now and not self._f9_prev and self._on_record_toggle:
                    self._on_record_toggle()
                self._f9_prev = f9_now
            except Exception:
                logger.exception("KeyboardMonitor poll error")
            time.sleep(0.05)

