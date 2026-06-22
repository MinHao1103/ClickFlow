from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SceneRule:
    image_path: str
    action: str = "click"       # "click" | "orb_solve"
    name: str = ""
    confidence: float = 0.8
    cooldown: float = 3.0
    enabled: bool = True
    order_idx: int = 0
    db_id: Optional[int] = None
