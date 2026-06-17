import threading
import time
import logging
from typing import Callable

import pyautogui

logger = logging.getLogger(__name__)


class MouseTracker:
    def __init__(self, callback: Callable[[int, int], None], interval_ms: int = 100):
        self._callback = callback
        self._interval = interval_ms / 1000.0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="MouseTracker"
        )
        self._thread.start()
        logger.debug("MouseTracker started")

    def stop(self) -> None:
        self._running = False

    def _run(self) -> None:
        while self._running:
            try:
                x, y = pyautogui.position()
                self._callback(x, y)
            except Exception:
                logger.exception("MouseTracker poll error")
            time.sleep(self._interval)
