"""Small SQLite stores: the lookup cache and the corrections Rennix learns from you."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class _DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")

    def execute(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            cur = self._conn.execute(sql, args)
            rows = cur.fetchall()
            self._conn.commit()
            return rows

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class Cache(_DB):
    """Remembers TheMovieDB answers so repeat scans are instant.

    Entries refresh after ``ttl_days`` in case TheMovieDB has updated them.
    """

    def __init__(self, path: Path | str, ttl_days: float = 21):
        super().__init__(path)
        self.ttl = ttl_days * 86400
        self.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, saved REAL)"
        )

    def get(self, key: str, allow_stale: bool = False) -> Any:
        rows = self.execute("SELECT value, saved FROM cache WHERE key=?", (key,))
        if not rows:
            return None
        value, saved = rows[0]
        if not allow_stale and time.time() - saved > self.ttl:
            return None
        try:
            return json.loads(value)
        except ValueError:
            return None

    def set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT OR REPLACE INTO cache (key, value, saved) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time()),
        )

    def clear(self) -> None:
        self.execute("DELETE FROM cache")

    def size(self) -> int:
        return self.execute("SELECT COUNT(*) FROM cache")[0][0]


class Learned(_DB):
    """Matches you chose by hand, remembered against the file's name and size."""

    def __init__(self, path: Path | str):
        super().__init__(path)
        self.execute(
            "CREATE TABLE IF NOT EXISTS corrections (key TEXT PRIMARY KEY, kind TEXT, "
            "tmdb_id INTEGER, extra TEXT, saved REAL)"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS folder_rules (folder TEXT PRIMARY KEY, kind TEXT, "
            "tmdb_id INTEGER, saved REAL)"
        )
        self.execute(
            "CREATE TABLE IF NOT EXISTS stats (name TEXT PRIMARY KEY, value INTEGER)"
        )

    @staticmethod
    def key_for(name: str, size: int) -> str:
        return f"{name.lower()}|{size}"

    def remember(self, name: str, size: int, kind: str, tmdb_id: int, extra: dict | None = None) -> None:
        self.execute(
            "INSERT OR REPLACE INTO corrections VALUES (?, ?, ?, ?, ?)",
            (self.key_for(name, size), kind, tmdb_id, json.dumps(extra or {}), time.time()),
        )

    def lookup(self, name: str, size: int) -> dict | None:
        rows = self.execute(
            "SELECT kind, tmdb_id, extra FROM corrections WHERE key=?", (self.key_for(name, size),)
        )
        if not rows:
            return None
        kind, tmdb_id, extra = rows[0]
        return {"kind": kind, "tmdb_id": tmdb_id, "extra": json.loads(extra or "{}")}

    def set_folder_rule(self, folder: str, kind: str, tmdb_id: int) -> None:
        self.execute(
            "INSERT OR REPLACE INTO folder_rules VALUES (?, ?, ?, ?)",
            (folder.lower(), kind, tmdb_id, time.time()),
        )

    def folder_rule(self, folder: str) -> dict | None:
        rows = self.execute("SELECT kind, tmdb_id FROM folder_rules WHERE folder=?", (folder.lower(),))
        return {"kind": rows[0][0], "tmdb_id": rows[0][1]} if rows else None

    def bump(self, name: str, by: int = 1) -> None:
        self.execute(
            "INSERT INTO stats (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = MAX(0, value + ?)",
            (name, max(0, by), by),
        )

    def stats(self) -> dict:
        return {k: v for k, v in self.execute("SELECT name, value FROM stats")}
