"""SQLite: заявки, связь «сообщение в группе админов → гость», кэш file_id фото."""
import json
import sqlite3
import time

from .config import DB_PATH


class Storage:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY, created REAL, user_id INTEGER, kind TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS relay (
                admin_message_id TEXT PRIMARY KEY, user_id INTEGER);
            CREATE TABLE IF NOT EXISTS photos (
                room TEXT, idx INTEGER, file_id TEXT, PRIMARY KEY (room, idx));
        """)

    def add_lead(self, user_id: int, kind: str, data: dict) -> int:
        cur = self.db.execute("INSERT INTO leads (created, user_id, kind, data) VALUES (?, ?, ?, ?)",
                              (time.time(), user_id, kind, json.dumps(data, ensure_ascii=False)))
        self.db.commit()
        return cur.lastrowid

    def link(self, admin_message_id, user_id: int) -> None:
        """id сообщений: в Telegram это числа, в MAX — строки, поэтому храним текстом."""
        self.db.execute("INSERT OR REPLACE INTO relay VALUES (?, ?)", (str(admin_message_id), user_id))
        self.db.commit()

    def user_for(self, admin_message_id):
        row = self.db.execute("SELECT user_id FROM relay WHERE admin_message_id = ?", (str(admin_message_id),)).fetchone()
        return row[0] if row else None

    def photo_ids(self, room: str):
        rows = self.db.execute("SELECT file_id FROM photos WHERE room = ? ORDER BY idx", (room,)).fetchall()
        return [r[0] for r in rows]

    def save_photo_ids(self, room: str, file_ids) -> None:
        self.db.execute("DELETE FROM photos WHERE room = ?", (room,))
        self.db.executemany("INSERT INTO photos VALUES (?, ?, ?)", [(room, i, f) for i, f in enumerate(file_ids)])
        self.db.commit()
