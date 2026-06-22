"""state.db: cursor + known applied IPs (separate from outbox.db)."""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path
from threading import Lock
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS applied_ips (ip TEXT PRIMARY KEY, applied_at INTEGER, source TEXT);
"""


class State:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = Lock()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as c:
            c.executescript(SCHEMA)

    def _connect(self):
        c = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def get_cursor(self) -> int:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT v FROM kv WHERE k='blocklist_cursor'").fetchone()
            return int(row[0]) if row else 0

    def set_cursor(self, cursor: int) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('blocklist_cursor', ?)", (str(cursor),))

    def get_global_cursor(self) -> int:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT v FROM kv WHERE k='global_cursor'").fetchone()
            return int(row[0]) if row else 0

    def set_global_cursor(self, cursor: int) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('global_cursor', ?)", (str(cursor),))

    def get_pid_snapshot(self) -> int | None:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT v FROM kv WHERE k='fail2ban_pid'").fetchone()
            return int(row[0]) if row else None

    def set_pid_snapshot(self, pid: int) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('fail2ban_pid', ?)", (str(pid),))

    def add_applied(self, ip: str, source: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO applied_ips (ip, applied_at, source) VALUES (?, ?, ?)",
                      (ip, int(time.time()), source))

    def remove_applied(self, ip: str) -> None:
        with self._lock, self._connect() as c:
            c.execute("DELETE FROM applied_ips WHERE ip = ?", (ip,))

    def all_applied(self) -> list[str]:
        with self._lock, self._connect() as c:
            return [r[0] for r in c.execute("SELECT ip FROM applied_ips").fetchall()]

    def count_applied(self) -> int:
        with self._lock, self._connect() as c:
            return c.execute("SELECT COUNT(*) FROM applied_ips").fetchone()[0]

    def set_snapshot(self, data: dict) -> None:
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('status_snapshot', ?)",
                      (json.dumps(data),))

    def get_snapshot(self) -> dict | None:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT v FROM kv WHERE k='status_snapshot'").fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None

    def count_by_source(self, source: str) -> int:
        with self._lock, self._connect() as c:
            return c.execute("SELECT COUNT(*) FROM applied_ips WHERE source = ?", (source,)).fetchone()[0]

    def applied_by_source(self, source: str) -> list[str]:
        with self._lock, self._connect() as c:
            return [r[0] for r in c.execute("SELECT ip FROM applied_ips WHERE source = ?", (source,)).fetchall()]

    def recent_applied(self, limit: int = 10) -> list[tuple[str, int, str]]:
        with self._lock, self._connect() as c:
            return [(r[0], r[1], r[2]) for r in c.execute(
                "SELECT ip, applied_at, source FROM applied_ips ORDER BY applied_at DESC LIMIT ?",
                (limit,)).fetchall()]

    def set_feed_stats(self, tenant_active: int, global_active: int) -> None:
        payload = json.dumps({"tenant_active": tenant_active, "global_active": global_active})
        with self._lock, self._connect() as c:
            c.execute("INSERT OR REPLACE INTO kv (k, v) VALUES ('feed_stats', ?)", (payload,))

    def get_feed_stats(self) -> dict | None:
        with self._lock, self._connect() as c:
            row = c.execute("SELECT v FROM kv WHERE k='feed_stats'").fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return None
