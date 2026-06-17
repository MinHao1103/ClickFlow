from dataclasses import dataclass
from typing import Optional


@dataclass
class Action:
    profile_id: int
    order_idx: int
    action_type: str
    x: Optional[int] = None
    y: Optional[int] = None
    click_count: int = 1
    delay_seconds: float = 0.0
    keyboard_text: Optional[str] = None
    extra_json: Optional[str] = None
    db_id: Optional[int] = None
