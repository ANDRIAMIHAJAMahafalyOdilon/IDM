import os
import threading
import time

from .categories import destination_folder
from .database import now
from .downloader import Downloader, filename_from_url
from .resume import load_state, part_path


class Item:
    def __init__(self, item_id, url, dest, category, dl, start_at=None):
        self.id = item_id
        self.url = url
        self.dest = dest
        self.name = os.path.basename(dest)
        self.category = category
        self.dl = dl
        self.start_at = start_at
        self.state = "queued"    # scheduled / queued / active / paused / done / error


class QueueManager:
    def __init__(self, db, base_dir, max_parallel=3, n_threads=8, limiter=None):
        self.db = db
        self.base_dir = base_dir
        self.max_parallel = max_parallel
        self.n_threads = n_threads
        self.limiter = limiter
        self.items = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._loop_thread = threading.Thread(target=self._loop, daemon=True)
        self._loop_thread.start()

    # ---------- ajout / contrôle ----------
    def add(self, url, start_at=None, headers=None, filename=None):
        name = filename or filename_from_url(url)
        folder, cat = destination_folder(name, self.base_dir)
        dest = os.path.join(folder, name)
        item_id = self.db.add(name, url, dest, cat)
        dl = Downloader(url, dest, n_threads=self.n_threads,
                        limiter=self.limiter, headers=headers)
        it = Item(item_id, url, dest, cat, dl, start_at)
        if start_at and start_at > time.time():
            it.state = "scheduled"
            self.db.update(item_id, status="scheduled")
        with self._lock:
            self.items[item_id] = it
        return item_id

    def load_unfinished(self):
        """Recharge les téléchargements interrompus (en pause, à cliquer sur Reprendre)."""
        for row in self.db.unfinished():
            if row["id"] in self.items:
                continue
            dl = Downloader(row["url"], row["path"], n_threads=self.n_threads,
                            limiter=self.limiter)
            state = load_state(row["path"])
            if state:
                dl.total = state.get("total", 0)
                got = 0
                for s in state.get("segments", []):
                    p = part_path(row["path"], s["i"])
                    if os.path.exists(p):
                        got += min(os.path.getsize(p), s["end"] - s["start"] + 1)
                dl.downloaded = got
            it = Item(row["id"], row["url"], row["path"], row["category"], dl)
            it.state = "paused"
            with self._lock:
                self.items[row["id"]] = it
            self.db.update(row["id"], status="paused")

    def pause(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if not it:
                return
            if it.state == "active":
                it.dl.pause()           # l'état passera à "paused" tout seul
            elif it.state in ("queued", "scheduled"):
                it.state = "paused"
                self.db.update(it.id, status="paused")

    def resume(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if it and it.state in ("paused", "error"):
                it.start_at = None
                it.state = "queued"
                self.db.update(it.id, status="queued", error=None)

    def remove(self, item_id):
        """Retire un téléchargement de la file (l'historique est conservé)."""
        with self._lock:
            it = self.items.pop(item_id, None)
        if not it:
            return
        if it.state == "active":
            it.dl.pause()
            it.dl.join()
        if it.state != "done":
            self.db.update(item_id, status="cancelled")

    # ---------- boucle interne ----------
    def _loop(self):
        while not self._stop.is_set():
            with self._lock:
                self._refresh()
                t = time.time()
                for it in self.items.values():
                    if it.state == "scheduled" and t >= it.start_at:
                        it.state = "queued"
                        self.db.update(it.id, status="queued")
                running = sum(1 for i in self.items.values() if i.state == "active")
                for it in self.items.values():
                    if running >= self.max_parallel:
                        break
                    if it.state == "queued":
                        it.dl.start()
                        it.state = "active"
                        self.db.update(it.id, status="downloading")
                        running += 1
            time.sleep(0.3)

    def _refresh(self):
        """Détecte les téléchargements terminés et met l'historique à jour."""
        for it in self.items.values():
            if it.state != "active" or it.dl.is_alive():
                continue
            s = it.dl.status
            if s == "done":
                it.state = "done"
                self.db.update(it.id, status="done", size=it.dl.total,
                               sha256=it.dl.sha256, finished_at=now())
            elif s == "paused":
                it.state = "paused"
                self.db.update(it.id, status="paused")
            else:
                it.state = "error"
                self.db.update(it.id, status="error", error=it.dl.error)

    # ---------- infos ----------
    def snapshot(self):
        with self._lock:
            out = []
            for it in self.items.values():
                total = it.dl.total
                pct = it.dl.downloaded * 100 / total if total else 0
                out.append({"id": it.id, "name": it.name, "category": it.category,
                            "state": it.state, "percent": pct,
                            "downloaded": it.dl.downloaded, "total": total,
                            "start_at": it.start_at, "error": it.dl.error})
            return out

    def all_finished(self):
        with self._lock:
            return all(i.state in ("done", "error") for i in self.items.values())

    def shutdown(self):
        """Met tout en pause proprement (la reprise reste possible)."""
        self._stop.set()
        self._loop_thread.join()
        with self._lock:
            for it in self.items.values():
                if it.state == "active":
                    it.dl.pause()
                elif it.state in ("queued", "scheduled"):
                    it.state = "paused"
                    self.db.update(it.id, status="paused")
            for it in self.items.values():
                it.dl.join()
            self._refresh()