"""Local persistent outbox for ban events (SQLite WAL).

Survives reboots, crashes, network outages. Producer (reader thread) enqueues,
consumer (sender thread) drains in FIFO order.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import List, Tuple

from almc_shield.parser import BanEvent


SCHEMA = """
CREATE TABLE IF NOT EXISTS bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jail TEXT NOT NULL,
    ip TEXT NOT NULL,
    banned_at TEXT NOT NULL,
    enqueued_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bans_enqueued_at_idx ON bans(enqueued_at);
"""


class Outbox:
    """SQLite-backed FIFO queue for ban events.

    Thread-safe via a single Lock (we have one producer + one consumer thread).
    WAL mode is enabled to allow concurrent reads while writing.
    """

    def __init__(self, db_path: str, max_size_mb: int = 100, max_age_days: int = 7) -> None:
        self.db_path = db_path
        self.max_size_bytes = max_size_mb * 1024 * 1024
        self.max_age_seconds = max_age_days * 86400
        self._lock = Lock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(SCHEMA)

    def enqueue(self, event: BanEvent) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO bans (jail, ip, banned_at, enqueued_at) VALUES (?, ?, ?, ?)",
                (event.jail, event.ip, event.banned_at.strftime("%Y-%m-%dT%H:%M:%SZ"), int(time.time())),
            )

    def fetch_batch(self, n: int) -> List[Tuple[int, BanEvent]]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT id, jail, ip, banned_at FROM bans ORDER BY id ASC LIMIT ?",
                (n,),
            )
            out: List[Tuple[int, BanEvent]] = []
            for row in cur.fetchall():
                rid, jail, ip, banned_at = row
                try:
                    dt = datetime.strptime(banned_at, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue
                out.append((rid, BanEvent(jail=jail, ip=ip, banned_at=dt)))
            return out

    def delete_batch(self, ids: List[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        with self._lock, self._connect() as conn:
            conn.execute(f"DELETE FROM bans WHERE id IN ({placeholders})", tuple(ids))

    def size(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM bans")
            return int(cur.fetchone()[0])

    def oldest_age_seconds(self) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT MIN(enqueued_at) FROM bans")
            row = cur.fetchone()
            if not row or row[0] is None:
                return 0
            age = int(time.time()) - int(row[0])
            return max(0, age)

    def drop_oldest_if_over_quota(self) -> int:
        """If outbox.db exceeds max_size_bytes, delete oldest rows in chunks until under quota.

        Returns number of rows dropped.
        """
        try:
            current_size = os.path.getsize(self.db_path)
        except OSError:
            return 0

        if current_size <= self.max_size_bytes:
            return 0

        dropped = 0
        # Drop in chunks of 100
        while True:
            with self._lock, self._connect() as conn:
                cur = conn.execute("SELECT id FROM bans ORDER BY id ASC LIMIT 100")
                ids = [r[0] for r in cur.fetchall()]
                if not ids:
                    break
                placeholders = ",".join("?" * len(ids))
                conn.execute(f"DELETE FROM bans WHERE id IN ({placeholders})", tuple(ids))
                dropped += len(ids)
            try:
                new_size = os.path.getsize(self.db_path)
            except OSError:
                break
            if new_size <= self.max_size_bytes:
                break
        return dropped

    def drop_older_than(self, max_age_seconds: int) -> int:
        cutoff = int(time.time()) - max_age_seconds
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM bans WHERE enqueued_at < ?", (cutoff,))
            return cur.rowcount or 0
