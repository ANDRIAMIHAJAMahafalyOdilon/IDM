import os
import sqlite3
import threading
from datetime import datetime

# La base est stockée dans le profil de l'utilisateur
# (C:\Users\<nom>\AppData\Roaming\FlashDL), là où Windows autorise l'écriture,
# même quand le logiciel est installé dans C:\Program Files.
# Variable FLASHDL_DB_DIR : permet de surcharger le dossier de la base (tests/portable).
_CUSTOM_DIR = os.environ.get("FLASHDL_DB_DIR") or os.environ.get("APPDATA") or os.path.expanduser("~")
_APPDATA = os.path.join(_CUSTOM_DIR, "FlashDL")
os.makedirs(_APPDATA, exist_ok=True)
DB_PATH = os.path.join(_APPDATA, "flashdl.db")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Database:
    def __init__(self, path=DB_PATH):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS downloads (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    name        TEXT,
                    url         TEXT,
                    path        TEXT,
                    category    TEXT,
                    size        INTEGER DEFAULT 0,
                    status      TEXT,
                    sha256      TEXT,
                    error       TEXT,
                    added_at    TEXT,
                    finished_at TEXT
                )""")
            columns = {row[1] for row in self._conn.execute(
                "PRAGMA table_info(downloads)").fetchall()}
            if "format_id" not in columns:
                self._conn.execute(
                    "ALTER TABLE downloads ADD COLUMN format_id TEXT")
            if "has_audio" not in columns:
                self._conn.execute(
                    "ALTER TABLE downloads ADD COLUMN has_audio INTEGER DEFAULT 1")
            self._conn.commit()

    def add(self, name, url, path, category, format_id=None, has_audio=True):
        """Ajoute une ligne, ou réutilise celle d'un téléchargement non terminé."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM downloads WHERE url=? AND path=? AND status!='done' "
                "ORDER BY id DESC LIMIT 1", (url, path)).fetchone()
            if row:
                self._conn.execute(
                    "UPDATE downloads SET status='queued', error=NULL, format_id=?, has_audio=? WHERE id=?",
                    (format_id, int(bool(has_audio)), row["id"]))
                self._conn.commit()
                return row["id"]
            cur = self._conn.execute(
                "INSERT INTO downloads (name, url, path, category, status, added_at, format_id, has_audio) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
                (name, url, path, category, now(), format_id, int(bool(has_audio))))
            self._conn.commit()
            return cur.lastrowid

    def update(self, dl_id, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE downloads SET {cols} WHERE id=?",
                               (*fields.values(), dl_id))
            self._conn.commit()

    def history(self, limit=50):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def unfinished(self):
        """Téléchargements à reprendre (ni terminés, ni annulés, ni en erreur)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM downloads WHERE status IN "
                "('queued','downloading','paused','scheduled') ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def delete(self, dl_id):
        with self._lock:
            self._conn.execute("DELETE FROM downloads WHERE id=?", (dl_id,))
            self._conn.commit()
