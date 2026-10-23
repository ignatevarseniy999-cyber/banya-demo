"""Хранилище поисков и уже виденных объявлений (SQLite)."""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    source TEXT NOT NULL,
    label TEXT NOT NULL,
    price_min INTEGER,
    price_max INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    initialized INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_check REAL,
    last_error TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    search_id INTEGER NOT NULL,
    listing_id TEXT NOT NULL,
    price INTEGER,
    first_seen REAL NOT NULL,
    PRIMARY KEY (search_id, listing_id)
);
CREATE TABLE IF NOT EXISTS owners (chat_id INTEGER PRIMARY KEY);
"""


@dataclass
class Search:
    id: int
    chat_id: int
    url: str
    source: str
    label: str
    price_min: int | None
    price_max: int | None
    active: bool
    initialized: bool
    fail_count: int
    last_check: float | None
    last_error: str | None


class Database:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock, self._conn:
            return self._conn.execute(sql, params)

    @staticmethod
    def _search(row) -> Search:
        return Search(
            id=row["id"],
            chat_id=row["chat_id"],
            url=row["url"],
            source=row["source"],
            label=row["label"],
            price_min=row["price_min"],
            price_max=row["price_max"],
            active=bool(row["active"]),
            initialized=bool(row["initialized"]),
            fail_count=row["fail_count"],
            last_check=row["last_check"],
            last_error=row["last_error"],
        )

    def add_search(self, chat_id, url, source, label, price_min=None, price_max=None) -> int:
        cur = self._exec(
            "INSERT INTO searches (chat_id, url, source, label, price_min, price_max, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, url, source, label, price_min, price_max, time.time()),
        )
        return cur.lastrowid

    def get_search(self, search_id: int) -> Search | None:
        row = self._exec("SELECT * FROM searches WHERE id = ?", (search_id,)).fetchone()
        return self._search(row) if row else None

    def searches(self, chat_id: int | None = None, active_only=False) -> list[Search]:
        sql, params = "SELECT * FROM searches WHERE 1=1", []
        if chat_id is not None:
            sql += " AND chat_id = ?"
            params.append(chat_id)
        if active_only:
            sql += " AND active = 1"
        return [self._search(r) for r in self._exec(sql + " ORDER BY id", params).fetchall()]

    def delete_search(self, search_id: int) -> None:
        self._exec("DELETE FROM searches WHERE id = ?", (search_id,))
        self._exec("DELETE FROM seen WHERE search_id = ?", (search_id,))

    def set_active(self, search_id: int, active: bool) -> None:
        self._exec("UPDATE searches SET active = ? WHERE id = ?", (int(active), search_id))

    def mark_checked(self, search_id: int, error: str | None) -> int:
        """Записывает итог проверки, возвращает число ошибок подряд."""
        if error is None:
            self._exec(
                "UPDATE searches SET last_check = ?, last_error = NULL, fail_count = 0,"
                " initialized = 1 WHERE id = ?",
                (time.time(), search_id),
            )
            return 0
        self._exec(
            "UPDATE searches SET last_check = ?, last_error = ?, fail_count = fail_count + 1"
            " WHERE id = ?",
            (time.time(), error, search_id),
        )
        return self.get_search(search_id).fail_count

    def seen_prices(self, search_id: int) -> dict[str, int | None]:
        rows = self._exec(
            "SELECT listing_id, price FROM seen WHERE search_id = ?", (search_id,)
        ).fetchall()
        return {r["listing_id"]: r["price"] for r in rows}

    def remember(self, search_id: int, listing_id: str, price: int | None) -> None:
        self._exec(
            "INSERT INTO seen (search_id, listing_id, price, first_seen) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (search_id, listing_id) DO UPDATE SET price = excluded.price",
            (search_id, listing_id, price, time.time()),
        )

    def owners(self) -> set[int]:
        return {r["chat_id"] for r in self._exec("SELECT chat_id FROM owners").fetchall()}

    def add_owner(self, chat_id: int) -> None:
        self._exec("INSERT OR IGNORE INTO owners (chat_id) VALUES (?)", (chat_id,))
