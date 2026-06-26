import json
import os
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import List, Callable, Optional

import pyautogui

from models.click_step import ClickStep

pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)


@dataclass
class ExecutionState:
    current_round: int = 0
    current_step: int = 0
    total_steps: int = 0
    current_click: int = 0
    total_clicks: int = 0
    is_running: bool = False


class ClickExecutor:
    def __init__(
        self,
        on_status_update: Callable[[ExecutionState], None],
        on_finished: Callable[[], None],
        on_error: Callable[[str], None],
    ):
        self._on_status_update = on_status_update
        self._on_finished = on_finished
        self._on_error = on_error
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state = ExecutionState()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, steps: List[ClickStep], rounds: int) -> None:
        """Start execution. rounds=0 means infinite."""
        if self.is_running:
            return
        self._stop_event.clear()
        self.state = ExecutionState(is_running=True, total_steps=len(steps))
        self._thread = threading.Thread(
            target=self._run,
            args=(steps, rounds),
            daemon=True,
            name="ClickExecutor",
        )
        self._thread.start()
        logger.info("Execute Start — rounds=%s steps=%d", rounds or "∞", len(steps))

    def stop(self) -> None:
        self._stop_event.set()
        logger.info("Execute Stop requested")

    def _run(self, steps: List[ClickStep], rounds: int) -> None:
        try:
            round_num = 0
            while not self._stop_event.is_set():
                round_num += 1
                self.state.current_round = round_num
                step_idx = 0
                while step_idx < len(steps):
                    if self._stop_event.is_set():
                        break
                    self.state.current_step = step_idx + 1
                    step = steps[step_idx]
                    
                    if step.action_type == "label":
                        step_idx += 1
                        
                    elif step.action_type == "goto":
                        self.state.total_clicks = 1
                        self.state.current_click = 1
                        self._on_status_update(self.state)
                        
                        params = json.loads(step.extra_json or "{}")
                        goto_label = params.get("goto_label")
                        target = None
                        if goto_label:
                            target = next((i for i, s in enumerate(steps) if s.action_type == "label" and s.keyboard_text == goto_label), None)
                        else:
                            target = params.get("goto_step", 1) - 1
                            
                        self._interruptible_sleep(step.delay)
                        
                        if target is not None and 0 <= target < len(steps):
                            step_idx = target
                        else:
                            step_idx += 1
                            
                    elif step.action_type == "if_image_exists":
                        self.state.total_clicks = 1
                        self.state.current_click = 1
                        self._on_status_update(self.state)
                        
                        params = json.loads(step.extra_json or "{}")
                        path = params.get("path", "")
                        conf = float(params.get("confidence", 0.85))
                        goto_label = params.get("goto_label")
                        target = None
                        if goto_label:
                            target = next((i for i, s in enumerate(steps) if s.action_type == "label" and s.keyboard_text == goto_label), None)
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
                        
                        if found and target is not None and 0 <= target < len(steps):
                            step_idx = target
                        else:
                            step_idx += 1
                            
                    else:
                        self._execute_step(step)
                        step_idx += 1
                        
                if rounds != 0 and round_num >= rounds:
                    break
        except pyautogui.PyAutoGUIException as e:
            logger.exception("pyautogui exception during execution")
            self._on_error(f"pyautogui 錯誤: {e}")
        except Exception as e:
            logger.exception("Thread exception during execution")
            self._on_error(str(e))
        finally:
            self.state.is_running = False
            self._on_finished()
            logger.info("Execute Stop — completed at round %d", self.state.current_round)

    def _execute_step(self, step: ClickStep) -> None:
        action = step.action_type

        if action == "move":
            self.state.total_clicks = 1
            self.state.current_click = 1
            self._on_status_update(self.state)
            pyautogui.moveTo(step.x, step.y)
            self._interruptible_sleep(step.delay)

        elif action in ("click", "double_click", "right_click"):
            self.state.total_clicks = step.count
            button = "right" if action == "right_click" else "left"
            for i in range(step.count):
                if self._stop_event.is_set():
                    break
                self.state.current_click = i + 1
                self._on_status_update(self.state)
                if action == "double_click":
                    pyautogui.doubleClick(step.x, step.y)
                else:
                    pyautogui.click(step.x, step.y, button=button)
                if step.delay > 0:
                    self._interruptible_sleep(step.delay)

        elif action == "delay":
            self.state.total_clicks = 1
            self.state.current_click = 1
            self._on_status_update(self.state)
            self._interruptible_sleep(step.delay)

        elif action == "keyboard_input":
            self.state.total_clicks = 1
            self.state.current_click = 1
            self._on_status_update(self.state)
            if step.keyboard_text:
                pyautogui.typewrite(step.keyboard_text, interval=0.05)
            self._interruptible_sleep(step.delay)

        elif action == "hotkey":
            self.state.total_clicks = 1
            self.state.current_click = 1
            self._on_status_update(self.state)
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
                self.state.current_click = i + 1
                self._on_status_update(self.state)
                pyautogui.mouseDown(step.x, step.y)
                import time as _t; _t.sleep(0.05)
                pyautogui.moveTo(to_x, to_y, duration=duration)
                _t.sleep(0.03)
                pyautogui.mouseUp()
                self._interruptible_sleep(step.delay)

        elif action == "image_click":
            self.state.total_clicks = 1
            self.state.current_click = 1
            self._on_status_update(self.state)
            params   = json.loads(step.extra_json or "{}")
            path     = params.get("path", "")
            conf     = float(params.get("confidence", 0.85))
            timeout  = float(params.get("timeout", 10.0))
            if not os.path.isfile(path):
                raise FileNotFoundError(f"找不到參考圖片：{path}")
            deadline = time.monotonic() + timeout
            center   = None
            while time.monotonic() < deadline:
                try:
                    center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                except pyautogui.ImageNotFoundException:
                    center = None
                if center:
                    break
                self._interruptible_sleep(0.5)
                if self._stop_event.is_set():
                    return
            if center is None:
                raise RuntimeError(
                    f"在 {timeout} 秒內找不到圖片：{os.path.basename(path)}"
                )
            pyautogui.click(center.x, center.y)
            self._interruptible_sleep(step.delay)

        else:
            logger.warning("Unknown action type: %s — skipped", action)
            self._on_status_update(self.state)

    def _interruptible_sleep(self, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
