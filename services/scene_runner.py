import ctypes
import os
import time
import threading
import logging
import json
import pyautogui
from typing import Callable, List, Optional, Tuple

try:
    import mss as _mss_mod
    from PIL import Image as _PIL_Image
    _HAS_MSS = True
except ImportError:
    _HAS_MSS = False


def _grab_screen(sct, region=None) -> "Optional[object]":
    """Screenshot of screen (or region if specified) -> PIL Image."""
    if region:
        # region is (x, y, w, h)
        if _HAS_MSS and sct is not None:
            try:
                monitor = {"left": region[0], "top": region[1], "width": region[2], "height": region[3]}
                shot = sct.grab(monitor)
                return _PIL_Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            except Exception:
                pass
        try:
            return pyautogui.screenshot(region=region)
        except Exception:
            return None

    if _HAS_MSS and sct is not None:
        try:
            shot = sct.grab(sct.monitors[1])
            return _PIL_Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        except Exception:
            pass
    try:
        return pyautogui.screenshot()
    except Exception:
        return None

def _safe_click(x: int, y: int, button: str = "left", double: bool = False) -> None:
    """Simulates a human click with mouse down and up delays to prevent Flash from dropping events."""
    try:
        pyautogui.moveTo(x, y)
        time.sleep(0.05)
        if double:
            pyautogui.mouseDown(button=button)
            time.sleep(0.05)
            pyautogui.mouseUp(button=button)
            time.sleep(0.08)
            pyautogui.mouseDown(button=button)
            time.sleep(0.05)
            pyautogui.mouseUp(button=button)
        else:
            pyautogui.mouseDown(button=button)
            time.sleep(0.08)
            pyautogui.mouseUp(button=button)
    except Exception:
        pass

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
        load_profile_steps: Optional[Callable] = None,
    ) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            args=(rules, get_orb_config, on_status, on_fired, get_win_info, base_dir, load_profile_steps),
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
        load_profile_steps: Optional[Callable],
    ) -> None:
        cooldowns: dict[int, float] = {}
        sct = _mss_mod.MSS() if _HAS_MSS else None

        on_status("場景腳本執行中…")
        logger.info("SceneRunner started with %d rules (mss=%s)", len(rules), _HAS_MSS)

        # Pre-filter enabled rules and resolve image paths + labels once
        active = []
        for r in rules:
            if not r.enabled:
                continue
            # Allow if has image_path OR is absolute click OR is run_profile with target_profile_name
            if not r.image_path and not (r.click_x is not None and r.click_y is not None) and not (r.action == "run_profile" and r.target_profile_name):
                continue
            img = r.image_path
            if img:
                if base_dir and not os.path.isabs(img):
                    img = os.path.join(base_dir, img)
                lbl = r.name or img.split("/")[-1].split("\\")[-1]
            else:
                lbl = r.name or (f"腳本({r.target_profile_name})" if r.action == "run_profile" else f"座標({r.click_x},{r.click_y})")
            active.append((r, img, lbl))

        # Cache hwnd between cycles; refresh only when stale (fix #6)
        cached_hwnd: Optional[int] = None
        cached_rect: Optional[tuple] = None
        hwnd_refresh_at: float = 0.0

        try:
          while not self._stop_event.is_set():
            try:
                if not active:
                    self._stop_event.wait(0.5)
                    continue

                # Refresh window binding at most once per second
                now = time.time()
                if get_win_info and now >= hwnd_refresh_at:
                    win_info = get_win_info()
                    if win_info and len(win_info) == 4:
                        cached_hwnd, cached_rect, cached_sx, cached_sy = win_info
                    elif win_info:
                        cached_hwnd, cached_rect = win_info[:2]
                        cached_sx, cached_sy = 1.0, 1.0
                    hwnd_refresh_at = now + 1.0
                hwnd, region = cached_hwnd, cached_rect
                sx, sy = cached_sx, cached_sy

                fired = False

                # One screenshot shared across all click-rule checks this cycle
                cycle_shot = _grab_screen(sct, region=region)

                for rule, img_path, label in active:
                    key = self._rule_key_static(rule)
                    if cooldowns.get(key, 0) > now:
                        continue

                    if rule.action == "orb_solve":
                        orb_cfg = get_orb_config()
                        if orb_cfg is None:
                            continue
                        is_active, board_snapshot = self._board_is_active(orb_cfg)
                        if not is_active:
                            continue

                        # Pre-solve: clear lingering popups before dragging
                        self._flush_click_rules(
                            active, cooldowns, region, hwnd, on_status, on_fired, sct=sct, sx=sx, sy=sy)

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
                            active, cooldowns, region, hwnd, on_status, on_fired, sct=sct, sx=sx, sy=sy)

                        # Wait for combo animation
                        self._stop_event.wait(3.0)
                        fired = True
                        break
                    if rule.action == "run_profile":
                        matched = False
                        if img_path:
                            matched, _, _ = self._try_click_rule(rule, img_path, region, cycle_shot)
                        else:
                            matched = True
                        if not matched:
                            continue

                        if load_profile_steps is None:
                            continue
                        steps_to_run = load_profile_steps(rule.target_profile_name, region)
                        if not steps_to_run:
                            continue

                        cooldowns[key] = time.time() + rule.cooldown
                        on_fired(rule)
                        on_status(f"執行設定檔：{rule.target_profile_name}")
                        logger.info("SceneRunner run_profile=%r triggered", rule.target_profile_name)
                        if hwnd:
                            try:
                                ctypes.windll.user32.SetForegroundWindow(hwnd)
                                time.sleep(0.15)
                            except Exception:
                                pass
                        step_idx = 0
                        while step_idx < len(steps_to_run):
                            if self._stop_event.is_set():
                                break
                            step = steps_to_run[step_idx]
                            if step.action_type == "label":
                                step_idx += 1
                            elif step.action_type == "goto":
                                on_status(f"執行中：{step.display_label()}")
                                params = json.loads(step.extra_json or "{}")
                                goto_label = params.get("goto_label")
                                target = None
                                if goto_label:
                                    target = next((i for i, s in enumerate(steps_to_run) if s.action_type == "label" and s.keyboard_text == goto_label), None)
                                else:
                                    target = params.get("goto_step", 1) - 1
                                self._interruptible_sleep(step.delay)
                                if target is not None and 0 <= target < len(steps_to_run):
                                    step_idx = target
                                else:
                                    step_idx += 1
                            elif step.action_type == "if_image_exists":
                                on_status(f"執行中：{step.display_label()}")
                                params = json.loads(step.extra_json or "{}")
                                path = params.get("path", "")
                                if path and base_dir and not os.path.isabs(path):
                                    path = os.path.join(base_dir, path)
                                conf = float(params.get("confidence", 0.85))
                                goto_label = params.get("goto_label")
                                target = None
                                if goto_label:
                                    target = next((i for i, s in enumerate(steps_to_run) if s.action_type == "label" and s.keyboard_text == goto_label), None)
                                else:
                                    target = params.get("goto_step", 1) - 1
                                found = False
                                if path and os.path.isfile(path):
                                    try:
                                        center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                                        if center is not None:
                                            found = True
                                    except Exception:
                                        found = False
                                self._interruptible_sleep(step.delay)
                                if found and target is not None and 0 <= target < len(steps_to_run):
                                    step_idx = target
                                else:
                                    step_idx += 1
                            else:
                                self._execute_step(step, on_status)
                                step_idx += 1
                        fired = True
                        break

                    # ── click rule ───────────────────────────────────────────
                    matched, target_x, target_y = self._try_click_rule(
                        rule, img_path, region, cycle_shot, sx, sy)
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
                    _safe_click(target_x, target_y)
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

        finally:
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass

        on_status("場景腳本已停止")
        logger.info("SceneRunner stopped")

    # ── shared click-rule matcher (fix #3 — single implementation) ───────────

    @staticmethod
    def _try_click_rule(rule: SceneRule, img_path: str,
                        region, haystack=None, sx: float = 1.0, sy: float = 1.0) -> tuple[bool, int, int]:
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
            import cv2
            
            # Load template image
            template = cv2.imread(img_path)
            if template is None:
                return False, 0, 0
                
            # Resize template dynamically based on window scale factors
            if abs(sx - 1.0) > 0.01 or abs(sy - 1.0) > 0.01:
                tw = max(1, int(round(template.shape[1] * sx)))
                th = max(1, int(round(template.shape[0] * sy)))
                template = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)

            if haystack is not None:
                # haystack is a full-screen PIL image; locate accepts numpy array as template
                loc = pyautogui.locate(template, haystack,
                                       confidence=rule.confidence)
            else:
                loc = pyautogui.locateOnScreen(template, region=region,
                                               confidence=rule.confidence)
        except Exception:
            return False, 0, 0

        if loc is None:
            return False, 0, 0

        if has_abs:
            return True, rule.click_x, rule.click_y

        cx, cy = pyautogui.center(loc)
        if haystack is not None and region is not None:
            return True, region[0] + cx + int(round(rule.click_dx * sx)), region[1] + cy + int(round(rule.click_dy * sy))
        else:
            return True, cx + int(round(rule.click_dx * sx)), cy + int(round(rule.click_dy * sy))

    # ── popup flush (pre/post orb_solve) ─────────────────────────────────────

    def _flush_click_rules(self, active, cooldowns, region,
                           hwnd, on_status, on_fired, max_passes: int = 12, sct=None, sx: float = 1.0, sy: float = 1.0) -> None:
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
            pass_shot = _grab_screen(sct, region=region)
            clicked = False
            for rule, img_path, label in active:
                if not rule.enabled or rule.action != "click":
                    continue
                matched, target_x, target_y = self._try_click_rule(
                    rule, img_path, region, pass_shot, sx, sy)
                if not matched:
                    continue

                on_fired(rule)
                if hwnd and not focused:
                    try:
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        time.sleep(0.15)
                        focused = True
                    except Exception:
                        pass
                _safe_click(target_x, target_y)
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

    # ── profile execution helpers ─────────────────────────────────────────────

    def _execute_step(self, step, on_status) -> None:
        action = step.action_type
        on_status(f"執行中：{step.display_label()}")
        logger.debug("SceneRunner executing step: %s", step.display_label())

        if action == "move":
            pyautogui.moveTo(step.x, step.y)
            self._interruptible_sleep(step.delay)

        elif action in ("click", "double_click", "right_click"):
            button = "right" if action == "right_click" else "left"
            for i in range(step.count):
                if self._stop_event.is_set():
                    break
                if action == "double_click":
                    _safe_click(step.x, step.y, button=button, double=True)
                else:
                    _safe_click(step.x, step.y, button=button)
                if step.delay > 0:
                    self._interruptible_sleep(step.delay)

        elif action == "delay":
            self._interruptible_sleep(step.delay)

        elif action == "keyboard_input":
            if step.keyboard_text:
                pyautogui.typewrite(step.keyboard_text, interval=0.05)
            self._interruptible_sleep(step.delay)

        elif action == "hotkey":
            if step.keyboard_text:
                keys = [k.strip() for k in step.keyboard_text.split("+")]
                pyautogui.hotkey(*keys)
            self._interruptible_sleep(step.delay)

        elif action == "drag":
            params   = json.loads(step.extra_json or "{}")
            to_x     = int(params.get("to_x", step.x))
            to_y     = int(params.get("to_y", step.y))
            duration = float(params.get("duration", 0.3))
            for i in range(step.count):
                if self._stop_event.is_set():
                    break
                pyautogui.mouseDown(step.x, step.y)
                time.sleep(0.05)
                pyautogui.moveTo(to_x, to_y, duration=duration)
                time.sleep(0.03)
                pyautogui.mouseUp()
                self._interruptible_sleep(step.delay)

        elif action == "image_click":
            params   = json.loads(step.extra_json or "{}")
            path     = params.get("path", "")
            conf     = float(params.get("confidence", 0.85))
            timeout  = float(params.get("timeout", 10.0))
            if not os.path.isfile(path):
                raise FileNotFoundError(f"找不到參考圖片：{path}")
            deadline = time.monotonic() + timeout
            center   = None
            while time.monotonic() < deadline and not self._stop_event.is_set():
                try:
                    center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                except pyautogui.ImageNotFoundException:
                    center = None
                if center:
                    break
                self._interruptible_sleep(0.5)
            if center is None:
                raise RuntimeError(
                    f"在 {timeout} 秒內找不到圖片：{os.path.basename(path)}"
                )
            _safe_click(center.x, center.y)
            self._interruptible_sleep(step.delay)

    def _interruptible_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
