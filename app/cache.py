"""SQLite 기반 key/value 캐시. ttl=None이면 영구 보관."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable


class Cache:
    def __init__(self, path: Path, now: Callable[[], float] = time.time):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at REAL NOT NULL, ttl REAL)"
        )
        self._conn.commit()
        self._now = now

    def get(self, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value, fetched_at, ttl FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value, fetched_at, ttl = row
        if ttl is not None and self._now() - fetched_at > ttl:
            return None
        return json.loads(value)

    def set(self, key: str, value: Any, ttl: float | None) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, fetched_at, ttl) VALUES (?, ?, ?, ?)",
            (key, json.dumps(value, ensure_ascii=False), self._now(), ttl),
        )
        self._conn.commit()
