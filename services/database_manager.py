import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

from models.click_step import ClickStep
from models.profile import Profile

logger = logging.getLogger(__name__)

DB_PATH = Path("clicker.db")

_DDL = """
CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL,
    order_idx INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    x INTEGER,
    y INTEGER,
    click_count INTEGER DEFAULT 1,
    delay_seconds REAL DEFAULT 0,
    keyboard_text TEXT,
    extra_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(profile_id) REFERENCES profiles(id)
);

CREATE INDEX IF NOT EXISTS idx_actions_profile ON actions(profile_id);
CREATE INDEX IF NOT EXISTS idx_actions_order ON actions(profile_id, order_idx);
"""


class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._initialize()

    def _initialize(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_DDL)
            logger.info("Database initialized: %s", self._db_path)
        except sqlite3.Error:
            logger.exception("Database initialization failed")
            raise

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_profile(self, profile: Profile, steps: List[ClickStep]) -> int:
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO profiles (name, description, updated_at)
                       VALUES (?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(name) DO UPDATE SET
                           description = excluded.description,
                           updated_at  = CURRENT_TIMESTAMP""",
                    (profile.name, profile.description),
                )
                row = conn.execute(
                    "SELECT id FROM profiles WHERE name = ?", (profile.name,)
                ).fetchone()
                profile_id: int = row["id"]

                conn.execute("DELETE FROM actions WHERE profile_id = ?", (profile_id,))

                for i, step in enumerate(steps):
                    conn.execute(
                        """INSERT INTO actions
                           (profile_id, order_idx, action_type, x, y,
                            click_count, delay_seconds, keyboard_text, extra_json)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            profile_id, i, step.action_type,
                            step.x, step.y, step.count, step.delay,
                            step.keyboard_text, step.extra_json,
                        ),
                    )
            logger.info("Save Profile: %s (%d steps)", profile.name, len(steps))
            return profile_id
        except sqlite3.Error:
            logger.exception("Failed to save profile: %s", profile.name)
            raise

    def load_profile(self, name: str) -> Optional[Tuple[Profile, List[ClickStep]]]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM profiles WHERE name = ?", (name,)
                ).fetchone()
                if row is None:
                    return None
                profile = Profile(
                    name=row["name"],
                    description=row["description"] or "",
                    db_id=row["id"],
                )
                action_rows = conn.execute(
                    "SELECT * FROM actions WHERE profile_id = ? ORDER BY order_idx",
                    (profile.db_id,),
                ).fetchall()
                steps = [
                    ClickStep(
                        x=r["x"] or 0,
                        y=r["y"] or 0,
                        count=r["click_count"] or 1,
                        delay=r["delay_seconds"] or 0.0,
                        action_type=r["action_type"],
                        keyboard_text=r["keyboard_text"],
                        extra_json=r["extra_json"],
                        db_id=r["id"],
                    )
                    for r in action_rows
                ]
            logger.info("Load Profile: %s (%d steps)", name, len(steps))
            return profile, steps
        except sqlite3.Error:
            logger.exception("Failed to load profile: %s", name)
            raise

    def delete_profile(self, name: str) -> bool:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM profiles WHERE name = ?", (name,)
                ).fetchone()
                if row is None:
                    return False
                conn.execute("DELETE FROM actions WHERE profile_id = ?", (row["id"],))
                conn.execute("DELETE FROM profiles WHERE id = ?", (row["id"],))
            logger.info("Delete Profile: %s", name)
            return True
        except sqlite3.Error:
            logger.exception("Failed to delete profile: %s", name)
            raise

    def list_profile_names(self) -> List[str]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT name FROM profiles ORDER BY name"
                ).fetchall()
            return [r["name"] for r in rows]
        except sqlite3.Error:
            logger.exception("Failed to list profiles")
            raise
