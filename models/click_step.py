import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ClickStep:
    x: int = 0
    y: int = 0
    count: int = 1
    delay: float = 0.0
    action_type: str = "click"
    keyboard_text: Optional[str] = None
    extra_json: Optional[str] = None
    db_id: Optional[int] = None

    def display_label(self) -> str:
        d = f"{self.delay:g}"          # 2.0 → "2", 1.5 → "1.5"
        if self.action_type == "label":
            return f"[label] 📌 {self.keyboard_text or ''}"
        if self.action_type in ("keyboard_input", "hotkey"):
            return f"[{self.action_type}] {self.keyboard_text or ''}  間隔{d}秒"
        if self.action_type == "delay":
            return f"[delay] 間隔{d}秒"
        if self.action_type == "goto":
            p = json.loads(self.extra_json or "{}")
            target = p.get("goto_label") or p.get("goto_step", "?")
            target_str = f"標籤 📌 {target}" if isinstance(target, str) else f"步驟 {target}"
            return f"[goto] 跳轉至 {target_str}  間隔{d}秒"
        if self.action_type == "if_image_exists":
            p = json.loads(self.extra_json or "{}")
            name = os.path.basename(p.get("path", "未設定"))
            conf = p.get("confidence", 0.85)
            target = p.get("goto_label") or p.get("goto_step", "?")
            target_str = f"標籤 📌 {target}" if isinstance(target, str) else f"步驟 {target}"
            return f"[if_image_exists] 偵測到 {name}({conf}) 則跳至 {target_str}  間隔{d}秒"
        if self.action_type == "image_click":
            p = json.loads(self.extra_json or "{}")
            name = os.path.basename(p.get("path", "未設定"))
            conf = p.get("confidence", 0.85)
            timeout = p.get("timeout", 10)
            return f"[image_click] {name}  相似度{conf}  超時{timeout}s  間隔{d}秒"
        if self.action_type == "drag":
            p = json.loads(self.extra_json or "{}")
            tx, ty = p.get("to_x", "?"), p.get("to_y", "?")
            dur = p.get("duration", 0.3)
            return f"[drag] ({self.x},{self.y}) → ({tx},{ty})  耗時{dur}s  間隔{d}秒"
        return f"[{self.action_type}] X={self.x} Y={self.y} ×{self.count}次  間隔{d}秒"
