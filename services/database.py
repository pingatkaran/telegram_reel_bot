from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class Database:
    def __init__(self, database_url: str) -> None:
        if not database_url.startswith("sqlite:///"):
            raise ValueError("Only sqlite:/// DATABASE_URL is supported in this starter project")
        self.path = Path(database_url.replace("sqlite:///", "", 1))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id INTEGER UNIQUE,
                    chat_id TEXT NOT NULL,
                    input_type TEXT NOT NULL,
                    source TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    public_url TEXT,
                    instagram_media_id TEXT,
                    instagram_permalink TEXT,
                    caption TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def create_job(self, update_id: int, chat_id: int | str, input_type: str, source: str | None) -> int | None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    INSERT INTO jobs (update_id, chat_id, input_type, source, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (update_id, str(chat_id), input_type, source, "queued", now, now),
                )
            except sqlite3.IntegrityError:
                return None
            return int(cursor.lastrowid)

    def update_job(self, job_id: int, **fields: str | None) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [job_id]
        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
