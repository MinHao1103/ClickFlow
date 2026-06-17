from dataclasses import dataclass
from typing import Optional
from datetime import datetime


@dataclass
class Profile:
    name: str
    description: str = ""
    db_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
