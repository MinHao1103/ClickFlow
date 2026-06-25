import ctypes
import os
import time
import threading
import logging
import pyautogui
from typing import Callable, List, Optional, Tuple

from models.scene_rule import SceneRule
from services.orb_board import OrbBoard, EMPTY      # top-level import (fix #4)

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
        get_orb_config: Callable,
        on_status: Callable[[str], None],
        on_fired: Callable[[SceneRule], None],
        get_win_info: Optional[Callable[[], WinInfo]] = None,
        base_dir: str = "",
    ) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            args=(rules, get_orb_config, on_status, on_fired, get_win_info, base_dir),
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
        base_dir: str,
    ) -> None:
        cooldowns: dict[int, float] = {}

        on_status("場景腳本執行中…")
        logger.info("SceneRunner started with %d rules", len(rules))

        # Pre-filter enabled rules and resolve image paths once (fix #1 + #2)
        active = []
        for r in rules:
            if not r.enabled or not r.image_path:
                continue
            img = r.image_path
            if base_dir and not os.path.isabs(img):
                img = os.path.join(base_dir, img)
            active.append((r, img))

        # Cache hwnd between cycles; refresh only when stale (fix #6)
        cached_hwnd: Optional[int] = None
        cached_rect: Optional[tuple] = None
        hwnd_refresh_at: float = 0.0

        while not self._stop_event.is_set():
            try:
                if not active:
                    self._stop_event.wait(0.5)
                    continue

                # Refresh window binding at most once per second
                now = time.time()
                if get_win_info and now >= hwnd_refresh_at:
                    cached_hwnd, cached_rect = get_win_info()
                    hwnd_refresh_at = now + 1.0
                hwnd, region = cached_hwnd, cached_rect

                fired = False

                # One screenshot shared across all click-rule checks this cycle
                try:
                    cycle_shot = pyautogui.screenshot()
                except Exception:
                    cycle_shot = None

                for rule, img_path in active:
                    key = self._rule_key_static(rule)
                    if cooldowns.get(key, 0) > now:
                        continue

                    label = rule.name or img_path.split("/")[-1].split("\\")[-1]

                    if rule.action == "orb_solve":
                        orb_cfg = get_orb_config()
                        if orb_cfg is None:
                            continue
                        is_active, board_snapshot = self._board_is_active(orb_cfg)
                        if not is_active:
                            continue

                        # Pre-solve: clear lingering popups before dragging
                        self._flush_click_rules(
                            active, cooldowns, region, hwnd, on_status, on_fired)

                        cooldowns[key] = time.time() + rule.cooldown
                        on_fired(rule)
                        on_status(f"轉珠：{label} — 辨識中…")
                        logger.info("SceneRunner orb_solve triggered")
                        if hwnd:
                            try:
                                ctypes.windll.user32.SetForegroundWindow(hwnd)
                                time.sleep(0.15)
                            except Exception:
                                pass
                        self._do_orb_solve(orb_cfg, on_status, board=board_snapshot)

                        # Post-solve: dismiss popups that appeared during drag
                        self._flush_click_rules(
                            active, cooldowns, region, hwnd, on_status, on_fired)

                        # Wait for combo animation
                        self._stop_event.wait(3.0)
                        fired = True
                        break

                    # ── click rule ───────────────────────────────────────────
                    matched, target_x, target_y = self._try_click_rule(
                        rule, img_path, region, cycle_shot)
                    if not matched:
                        continue

                    cooldowns[key] = time.time() + rule.cooldown
                    on_fired(rule)
                    if hwnd:
                        try:
                            ctypes.windll.user32.SetForegroundWindow(hwnd)
                            time.sleep(0.15)
                        except Exception:
                            pass
                    pyautogui.click(target_x, target_y)
                    on_status(f"點擊：{label} → ({target_x}, {target_y})")
                    logger.info("SceneRunner click rule=%r target=(%d,%d)", label, target_x, target_y)
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

    # ── shared click-rule matcher (fix #3 — single implementation) ───────────

    @staticmethod
    def _try_click_rule(rule: SceneRule, img_path: str,
                        region, haystack=None) -> tuple[bool, int, int]:
        """Try to match a click rule. Returns (matched, target_x, target_y).

        When haystack is a pre-taken PIL screenshot (full screen), locate() is
        used directly — avoids a redundant screenshot per rule per cycle.
        """
        has_abs = rule.click_x is not None and rule.click_y is not None

        if has_abs and not img_path:
            return True, rule.click_x, rule.click_y

        if not img_path:
            return False, 0, 0

        try:
            if haystack is not None:
                # haystack is a full-screen PIL image; locate returns screen-absolute coords
                loc = pyautogui.locate(img_path, haystack,
                                       confidence=rule.confidence)
            else:
                loc = pyautogui.locateOnScreen(img_path, region=region,
                                               confidence=rule.confidence)
        except Exception:
            return False, 0, 0

        if loc is None:
            return False, 0, 0

        if has_abs:
            return True, rule.click_x, rule.click_y

        cx, cy = pyautogui.center(loc)
        return True, cx + rule.click_dx, cy + rule.click_dy

    # ── popup flush (pre/post orb_solve) ─────────────────────────────────────

    def _flush_click_rules(self, active, cooldowns, region,
                           hwnd, on_status, on_fired, max_passes: int = 12) -> None:
        """Keep clicking matching click rules until a full pass finds nothing.

        Handles stacked dialogs (確定 → 知道了 → 確定 → …).
        Ignores cooldowns so fresh dialogs are never skipped.
        Safety cap: max_passes clicks before giving up.
        """
        focused = False  # only focus window once per flush session
        for pass_num in range(max_passes):
            if self._stop_event.is_set():
                break
            # Fresh screenshot per pass (screen changed after each click)
            try:
                pass_shot = pyautogui.screenshot()
            except Exception:
                pass_shot = None
            clicked = False
            for rule, img_path in active:
                if not rule.enabled or rule.action != "click":
                    continue
                matched, target_x, target_y = self._try_click_rule(
                    rule, img_path, region, pass_shot)
                if not matched:
                    continue

                label = rule.name or img_path.split("/")[-1].split("\\")[-1]
                on_fired(rule)
                if hwnd and not focused:
                    try:
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        time.sleep(0.15)
                        focused = True
                    except Exception:
                        pass
                pyautogui.click(target_x, target_y)
                cooldowns[self._rule_key_static(rule)] = time.time() + rule.cooldown
                on_status(f"[彈窗{pass_num+1}] {label} → ({target_x}, {target_y})")
                logger.info("flush pass=%d clicked %r at (%d,%d)",
                            pass_num + 1, label, target_x, target_y)
                clicked = True
                time.sleep(0.4)
                break  # restart from rule[0] after each click

            if not clicked:
                break  # clean pass — no more popups

    @staticmethod
    def _rule_key_static(rule) -> int:
        return rule.db_id if rule.db_id is not None else rule.order_idx

    # ── battle detection ──────────────────────────────────────────────────────

    def _board_is_active(self, orb_cfg) -> tuple:
        """Return (True, board) when ≥50% filled AND ≥3 distinct orb colours."""
        try:
            board = OrbBoard(orb_cfg).snapshot()
            total = orb_cfg.rows * orb_cfg.cols
            colours: set = set()
            non_empty = 0
            for row in board:
                for cell in row:
                    if cell != EMPTY:
                        non_empty += 1
                        colours.add(cell)
            if non_empty >= total // 2 and len(colours) >= 3:
                return True, board
            return False, None
        except Exception:
            return False, None

    # ── orb solve ─────────────────────────────────────────────────────────────

    def _do_orb_solve(self, orb_cfg, on_status: Callable[[str], None], board=None) -> None:
        from services.orb_solver import OrbSolver
        from services.orb_executor import OrbExecutor

        done_event = threading.Event()
        exec_ref: list = [None]

        try:
            if board is None:
                board = OrbBoard(orb_cfg).snapshot()
            path, predicted = OrbSolver(orb_cfg).solve(board, time_limit=12.0)

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
