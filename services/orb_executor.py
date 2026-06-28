import time
import threading
import logging
import pyautogui
from typing import Callable, Optional
from models.orb_config import OrbConfig

logger = logging.getLogger(__name__)


class OrbExecutor:
    def __init__(self, config: OrbConfig) -> None:
        self._cfg = config
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(
        self,
        path: list[tuple[int, int]],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._execute,
            args=(path, on_done, on_error),
            daemon=True,
            name="OrbExecutor",
        )
        self._thread.start()

    def abort(self) -> None:
        self._stop_event.set()

    def _to_screen(self, row: int, col: int) -> tuple[int, int]:
        cfg = self._cfg
        x = cfg.board_x + col * cfg.cell_w + cfg.cell_w // 2
        y = cfg.board_y + row * cfg.cell_h + cfg.cell_h // 2
        return x, y

    def _execute(
        self,
        path: list[tuple[int, int]],
        on_done: Callable[[], None],
        on_error: Callable[[str], None],
    ) -> None:
        orig_pause = pyautogui.PAUSE
        try:
            if not path:
                on_done()
                return

            # 暫時將全域延遲設為極小值，使 drag_speed_ms 設定能精確反映在每格的移動時間上
            pyautogui.PAUSE = 0.002
            speed = self._cfg.drag_speed_ms / 1000.0
            sx, sy = self._to_screen(*path[0])

            pyautogui.mouseDown(sx, sy)
            time.sleep(0.08)

            for pos in path[1:]:
                if self._stop_event.is_set():
                    break
                x, y = self._to_screen(*pos)
                pyautogui.moveTo(x, y, duration=speed)
                time.sleep(0.015)

            time.sleep(0.05)
            pyautogui.mouseUp()
            logger.info("OrbExecutor: completed %d steps", len(path))
            on_done()
        except Exception as exc:
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            logger.exception("OrbExecutor error")
            on_error(str(exc))
        finally:
            pyautogui.PAUSE = orig_pause

