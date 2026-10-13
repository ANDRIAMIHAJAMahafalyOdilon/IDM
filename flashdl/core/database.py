import os
import sqlite3
import threading
from datetime import datetime

from .categories import DEFAULT, DEFAULT_CATEGORIES

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
        self._lock = threading.RLock()
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
            migrations = {
                "initial_url": "TEXT",
                "extension": "TEXT DEFAULT ''",
                "description": "TEXT DEFAULT ''",
                "mime_type": "TEXT DEFAULT ''",
                "downloaded": "INTEGER DEFAULT 0",
                "started_at": "TEXT",
                "attempts": "INTEGER DEFAULT 0",
                "queue_name": "TEXT DEFAULT 'Principale'",
                "etag": "TEXT DEFAULT ''",
                "last_modified": "TEXT DEFAULT ''",
                "supports_range": "INTEGER DEFAULT 0",
                "duration": "REAL",
                "scheduled_at": "REAL",
            }
            for name, definition in migrations.items():
                if name not in columns:
                    self._conn.execute(
                        f"ALTER TABLE downloads ADD COLUMN {name} {definition}")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS categories (
                    name TEXT PRIMARY KEY,
                    folder TEXT NOT NULL,
                    remember_path INTEGER NOT NULL DEFAULT 0,
                    is_custom INTEGER NOT NULL DEFAULT 0
                )""")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS queues (
                    name TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    max_parallel INTEGER NOT NULL DEFAULT 3
                )""")
            for category in DEFAULT_CATEGORIES:
                self._conn.execute(
                    "INSERT OR IGNORE INTO categories (name, folder, is_custom) "
                    "VALUES (?, ?, 0)", (category, category))
            old_categories = self._conn.execute(
                "SELECT DISTINCT category FROM downloads WHERE category IS NOT NULL AND category!=''"
            ).fetchall()
            for row in old_categories:
                self._conn.execute(
                    "INSERT OR IGNORE INTO categories (name, folder, is_custom) "
                    "VALUES (?, ?, 0)", (row[0], row[0]))
            self._conn.execute(
                "INSERT OR IGNORE INTO queues (name, enabled, max_parallel) "
                "VALUES ('Principale', 1, 3)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_downloads_status "
                               "ON downloads(status)")
            self._conn.commit()

    def add(self, name, url, path, category, format_id=None, has_audio=True,
            **metadata):
        """Ajoute une ligne, ou réutilise celle d'un téléchargement non terminé."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM downloads WHERE url=? AND path=? AND status!='done' "
                "ORDER BY id DESC LIMIT 1", (url, path)).fetchone()
            if row:
                allowed = {"initial_url", "extension", "description", "mime_type",
                           "downloaded", "size", "started_at", "attempts", "queue_name",
                           "etag", "last_modified", "supports_range", "scheduled_at"}
                values = {key: value for key, value in metadata.items() if key in allowed}
                values.update(status="queued", error=None, format_id=format_id,
                              has_audio=int(bool(has_audio)), name=name,
                              category=category)
                assignments = ", ".join(f"{key}=?" for key in values)
                self._conn.execute(f"UPDATE downloads SET {assignments} WHERE id=?",
                                   (*values.values(), row["id"]))
                self._conn.commit()
                return row["id"]
            allowed = {"initial_url", "extension", "description", "mime_type",
                       "downloaded", "size", "started_at", "attempts", "queue_name",
                       "etag", "last_modified", "supports_range", "scheduled_at"}
            values = {key: value for key, value in metadata.items() if key in allowed}
            values.update(name=name, url=url, path=path, category=category,
                          status="queued", added_at=now(), format_id=format_id,
                          has_audio=int(bool(has_audio)))
            keys = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            cur = self._conn.execute(
                f"INSERT INTO downloads ({keys}) VALUES ({marks})", tuple(values.values()))
            self._conn.commit()
            return cur.lastrowid

    def update(self, dl_id, **fields):
        if not fields:
            return
        with self._lock:
            allowed = {row[1] for row in self._conn.execute(
                "PRAGMA table_info(downloads)").fetchall()}
            fields = {key: value for key, value in fields.items() if key in allowed}
            if not fields:
                return
            cols = ", ".join(f"{k}=?" for k in fields)
            self._conn.execute(f"UPDATE downloads SET {cols} WHERE id=?",
                               (*fields.values(), dl_id))
            self._conn.commit()

    def get(self, dl_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM downloads WHERE id=?",
                                     (dl_id,)).fetchone()
        return dict(row) if row else None

    def history(self, limit=10000):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM downloads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def unfinished(self):
        """Téléchargements à reprendre (ni terminés, ni annulés, ni en erreur)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM downloads WHERE status IN "
                "('queued','downloading','paused','scheduled','deferred','interrupted','error') "
                "ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def delete(self, dl_id):
        with self._lock:
            self._conn.execute("DELETE FROM downloads WHERE id=?", (dl_id,))
            self._conn.commit()

    def preference(self, key, default=None):
        with self._lock:
            row = self._conn.execute("SELECT value FROM preferences WHERE key=?",
                                     (key,)).fetchone()
        return row["value"] if row else default

    def set_preference(self, key, value):
        with self._lock:
            self._conn.execute(
                "INSERT INTO preferences (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value)))
            self._conn.commit()

    def categories(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, folder, remember_path, is_custom FROM categories "
                "ORDER BY is_custom, name").fetchall()
        return [dict(row) for row in rows]

    def save_category(self, name, folder, remember_path=False, is_custom=False):
        with self._lock:
            self._conn.execute(
                "INSERT INTO categories (name, folder, remember_path, is_custom) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                "folder=excluded.folder, remember_path=excluded.remember_path, "
                "is_custom=MAX(categories.is_custom, excluded.is_custom)",
                (name, folder, int(bool(remember_path)), int(bool(is_custom))))
            self._conn.commit()

    def add_queue(self, name):
        with self._lock:
            self._conn.execute("INSERT OR IGNORE INTO queues (name) VALUES (?)", (name,))
            self._conn.commit()

    def queues(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, enabled, max_parallel FROM queues ORDER BY name").fetchall()
        return [dict(row) for row in rows]

    def set_queue_enabled(self, name, enabled):
        with self._lock:
            self._conn.execute("UPDATE queues SET enabled=? WHERE name=?",
                               (int(bool(enabled)), name))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
