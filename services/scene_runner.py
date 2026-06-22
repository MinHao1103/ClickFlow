import ctypes
import time
import threading
import logging
import pyautogui
from typing import Callable, List, Optional, Tuple

from models.scene_rule import SceneRule

logger = logging.getLogger(__name__)

# (hwnd, (x, y, w, h)) or (None, None)
WinInfo = Tuple[Optional[int], Optional[Tuple[int, int, int, int]]]


class SceneRunner:
    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(
        self,
        rules: List[SceneRule],
        get_orb_config: Callable,              # () -> OrbConfig | None
        on_status: Callable[[str], None],       # caller wraps root.after
        on_fired: Callable[[SceneRule], None],
        get_win_info: Optional[Callable[[], WinInfo]] = None,  # () -> (hwnd, rect) | (None,None)
    ) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            args=(rules, get_orb_config, on_status, on_fired, get_win_info),
            daemon=True,
            name="SceneRunner",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    # ── main loop ─────────────────────────────────────────────────────────────

    def _loop(
        self,
        rules: List[SceneRule],
        get_orb_config: Callable,
        on_status: Callable[[str], None],
        on_fired: Callable[[SceneRule], None],
        get_win_info: Optional[Callable[[], WinInfo]],
    ) -> None:
        cooldowns: dict[int, float] = {}

        on_status("場景腳本執行中…")
        logger.info("SceneRunner started with %d rules", len(rules))

        while not self._stop_event.is_set():
            try:
                active = [r for r in rules if r.enabled and r.image_path]
                if not active:
                    self._stop_event.wait(0.5)
                    continue

                # Resolve window binding (region for screenshot crop)
                hwnd, rect = get_win_info() if get_win_info else (None, None)
                region = rect  # (x, y, w, h) or None → full screen

                now = time.time()
                fired = False

                for rule in active:
                    if cooldowns.get(id(rule), 0) > now:
                        continue
                    try:
                        loc = pyautogui.locateOnScreen(
                            rule.image_path,
                            region=region,
                            confidence=rule.confidence,
                        )
                    except Exception:
                        loc = None

                    if loc is None:
                        continue

                    cooldowns[id(rule)] = time.time() + rule.cooldown
                    label = rule.name or rule.image_path.split("/")[-1].split("\\")[-1]
                    on_fired(rule)

                    # Bring window to front before acting
                    if hwnd:
                        try:
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                            time.sleep(0.12)
                        except Exception:
                            pass

                    if rule.action == "click":
                        cx, cy = pyautogui.center(loc)
                        pyautogui.click(cx, cy)
                        on_status(f"點擊：{label}")
                        logger.info("SceneRunner click rule=%r loc=%s", label, loc)
                    elif rule.action == "orb_solve":
                        orb_cfg = get_orb_config()
                        if orb_cfg is None:
                            on_status("轉珠：尚未校準，略過")
                        else:
                            on_status(f"轉珠：{label} — 辨識中…")
                            self._do_orb_solve(orb_cfg, on_status)

                    fired = True
                    break

                if not fired:
                    on_status("掃描中…")

            except Exception as exc:
                logger.exception("SceneRunner loop error")
                on_status(f"錯誤：{exc}")

            self._stop_event.wait(0.5)

        on_status("場景腳本已停止")
        logger.info("SceneRunner stopped")

    # ── orb solve ─────────────────────────────────────────────────────────────

    def _do_orb_solve(self, orb_cfg, on_status: Callable[[str], None]) -> None:
        from services.orb_board import OrbBoard
        from services.orb_solver import OrbSolver
        from services.orb_executor import OrbExecutor

        done_event = threading.Event()
        exec_ref: list = [None]

        try:
            board = OrbBoard(orb_cfg).snapshot()
            path, predicted = OrbSolver(orb_cfg).solve(board)

            if not path:
                on_status("轉珠：找不到路線")
                return

            exec_ = OrbExecutor(orb_cfg)
            exec_ref[0] = exec_

            exec_.run(
                path,
                on_done=lambda: done_event.set(),
                on_error=lambda _e: done_event.set(),
            )

            while not done_event.is_set() and not self._stop_event.is_set():
                time.sleep(0.1)

            if self._stop_event.is_set() and exec_ref[0]:
                exec_ref[0].abort()
            else:
                on_status(f"轉珠完成：預測 {predicted} combo")

        except Exception as exc:
            try:
                pyautogui.mouseUp()
            except Exception:
                pass
            logger.exception("SceneRunner orb solve error")
            on_status(f"轉珠失敗：{exc}")
