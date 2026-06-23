import sqlite3
import logging
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional, Tuple

from models.click_step import ClickStep
from models.orb_config import OrbConfig
from models.profile import Profile
from models.scene_rule import SceneRule

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

CREATE TABLE IF NOT EXISTS scene_rules (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_idx  INTEGER NOT NULL DEFAULT 0,
    name       TEXT    NOT NULL DEFAULT '',
    image_path TEXT    NOT NULL,
    action     TEXT    NOT NULL DEFAULT 'click',
    confidence REAL    NOT NULL DEFAULT 0.8,
    cooldown   REAL    NOT NULL DEFAULT 3.0,
    enabled    INTEGER NOT NULL DEFAULT 1,
    click_dx   INTEGER NOT NULL DEFAULT 0,
    click_dy   INTEGER NOT NULL DEFAULT 0,
    click_x    INTEGER,
    click_y    INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orb_configs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    UNIQUE NOT NULL,
    board_x       INTEGER NOT NULL DEFAULT 0,
    board_y       INTEGER NOT NULL DEFAULT 0,
    cell_w        INTEGER NOT NULL DEFAULT 0,
    cell_h        INTEGER NOT NULL DEFAULT 0,
    rows          INTEGER NOT NULL DEFAULT 5,
    cols          INTEGER NOT NULL DEFAULT 6,
    drag_speed_ms INTEGER NOT NULL DEFAULT 25,
    beam_width    INTEGER NOT NULL DEFAULT 50,
    max_steps     INTEGER NOT NULL DEFAULT 50,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


class DatabaseManager:
    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._initialize()

    def _initialize(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(_DDL)
                # Migration: add click_dx/click_dy if table was created before this version
                existing = {r[1] for r in conn.execute("PRAGMA table_info(scene_rules)")}
                for col, ddl in [("click_dx", "INTEGER NOT NULL DEFAULT 0"),
                                  ("click_dy", "INTEGER NOT NULL DEFAULT 0"),
                                  ("click_x",  "INTEGER"),
                                  ("click_y",  "INTEGER")]:
                    if col not in existing:
                        conn.execute(f"ALTER TABLE scene_rules ADD COLUMN {col} {ddl}")
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

    # ── Scene rules ───────────────────────────────────────────────────────────

    def save_scene_rules(self, rules: List[SceneRule]) -> None:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM scene_rules")
                for i, r in enumerate(rules):
                    conn.execute(
                        """INSERT INTO scene_rules
                           (order_idx, name, image_path, action, confidence, cooldown, enabled,
                            click_dx, click_dy, click_x, click_y)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (i, r.name, r.image_path, r.action,
                         r.confidence, r.cooldown, int(r.enabled),
                         r.click_dx, r.click_dy, r.click_x, r.click_y),
                    )
            logger.info("Saved %d scene rules", len(rules))
        except sqlite3.Error:
            logger.exception("Failed to save scene rules")
            raise

    def load_scene_rules(self) -> List[SceneRule]:
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM scene_rules ORDER BY order_idx"
                ).fetchall()
            return [
                SceneRule(
                    image_path=r["image_path"],
                    action=r["action"],
                    name=r["name"] or "",
                    confidence=r["confidence"],
                    cooldown=r["cooldown"],
                    enabled=bool(r["enabled"]),
                    order_idx=r["order_idx"],
                    db_id=r["id"],
                    click_dx=r["click_dx"] if "click_dx" in r.keys() else 0,
                    click_dy=r["click_dy"] if "click_dy" in r.keys() else 0,
                    click_x=r["click_x"] if "click_x" in r.keys() else None,
                    click_y=r["click_y"] if "click_y" in r.keys() else None,
                )
                for r in rows
            ]
        except sqlite3.Error:
            logger.exception("Failed to load scene rules")
            raise

    # ── Orb config ────────────────────────────────────────────────────────────

    def save_orb_config(self, cfg: OrbConfig) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """INSERT INTO orb_configs
                       (name, board_x, board_y, cell_w, cell_h,
                        rows, cols, drag_speed_ms, beam_width, max_steps, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(name) DO UPDATE SET
                           board_x=excluded.board_x, board_y=excluded.board_y,
                           cell_w=excluded.cell_w,   cell_h=excluded.cell_h,
                           rows=excluded.rows,        cols=excluded.cols,
                           drag_speed_ms=excluded.drag_speed_ms,
                           beam_width=excluded.beam_width,
                           max_steps=excluded.max_steps,
                           updated_at=CURRENT_TIMESTAMP""",
                    (cfg.name, cfg.board_x, cfg.board_y, cfg.cell_w, cfg.cell_h,
                     cfg.rows, cfg.cols, cfg.drag_speed_ms, cfg.beam_width, cfg.max_steps),
                )
            logger.info("Saved orb config: %s", cfg.name)
        except sqlite3.Error:
            logger.exception("Failed to save orb config")
            raise

    def load_orb_config(self, name: str = "default") -> Optional[OrbConfig]:
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM orb_configs WHERE name = ?", (name,)
                ).fetchone()
            if row is None:
                return None
            return OrbConfig(
                name=row["name"],
                board_x=row["board_x"], board_y=row["board_y"],
                cell_w=row["cell_w"],   cell_h=row["cell_h"],
                rows=row["rows"],       cols=row["cols"],
                drag_speed_ms=row["drag_speed_ms"],
                beam_width=row["beam_width"],
                max_steps=row["max_steps"],
                db_id=row["id"],
            )
        except sqlite3.Error:
            logger.exception("Failed to load orb config")
            return None
