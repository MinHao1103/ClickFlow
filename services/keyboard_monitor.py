import ctypes
import threading
import time
import logging
from typing import Callable

logger = logging.getLogger(__name__)

_VK_SPACE = 0x20
_VK_S     = 0x53
_VK_F8    = 0x77


class KeyboardMonitor:
    """Polls keyboard state via GetAsyncKeyState at 50ms intervals."""

    def __init__(
        self,
        on_stop: Callable[[], None],
        on_capture: Callable[[], None],
        on_orb_solve: Callable[[], None] | None = None,
    ):
        self._on_stop      = on_stop
        self._on_capture   = on_capture
        self._on_orb_solve = on_orb_solve
        self._running = False
        self._thread: threading.Thread | None = None
        self._user32 = ctypes.windll.user32
        self._space_prev = False
        self._s_prev     = False
        self._f8_prev    = False

    def start(self) -> None:
        if self._running:
            return
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

                s_now = self._pressed(_VK_S)
                if s_now and not self._s_prev:
                    self._on_capture()
                self._s_prev = s_now

                f8_now = self._pressed(_VK_F8)
                if f8_now and not self._f8_prev and self._on_orb_solve:
                    self._on_orb_solve()
                self._f8_prev = f8_now
            except Exception:
                logger.exception("KeyboardMonitor poll error")
            time.sleep(0.05)
