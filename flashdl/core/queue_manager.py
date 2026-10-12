import os
import threading
import time

from .categories import destination_folder
from .database import now
from .downloader import Downloader, filename_from_url
from .hls import HlsDownloader, is_hls_url
from .config import get_headers_for_url
from .resume import load_state, part_path
from .extractors import extract, is_extractable_url, _safe_filename
from .youtube_downloader import YoutubeDownloader


def make_downloader(url, dest, n_threads, limiter, headers=None, stream=False):
    """
    Factory pour les téléchargements simples et les flux HLS
    """
    headers = headers or {}

    if stream or is_hls_url(url):
        return HlsDownloader(url, dest, n_threads=n_threads, limiter=limiter, headers=headers)
    else:
        return Downloader(url, dest, n_threads=n_threads, limiter=limiter, headers=headers)


class Item:
    def __init__(self, item_id, url, dest, category, dl, start_at=None,
                 source_url: str = None, format_id: str = None,
                 has_audio: bool = True, ext: str = "mp4"):
        self.id = item_id
        self.url = url
        self.dest = dest
        self.name = os.path.basename(dest)
        self.category = category
        self.dl = dl
        self.start_at = start_at
        self.state = "queued"
        self.source_url = source_url  # URL source (page web)
        self.format_id = format_id
        self.has_audio = has_audio
        self.ext = ext


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
    def add(self, url, start_at=None, headers=None, filename=None, stream=False,
            source_url=None):
        """Ajoute un téléchargement. Si l'URL est extractible (YouTube…),
        fait l'extraction automatiquement et prend la meilleure qualité."""
        if is_extractable_url(url):
            ids, info = self.add_video(url, start_at=start_at)
            if ids:
                return ids[0], info
            return None, info

        return self._add_single(url, start_at, headers, filename, stream, source_url), None

    def add_video(self, url, start_at=None, quality_label=None, prefer_format=None,
                  video_info=None, format_index=None, extra_headers=None):
        """
        Extrait la vidéo depuis une URL de page et l'ajoute à la file.
        - quality_label : ex "1080p", "720p"… sinon meilleure qualité auto
        - prefer_format : "mp4", "webm"… (priorité si égalité)
        Retourne : ([item_ids], VideoInfo)
        """
        info = video_info or extract(url, extra_headers=extra_headers)
        if not info.success:
            return [], info

        # Choisir le format
        fmt = None
        if format_index is not None and 0 <= format_index < len(info.formats):
            fmt = info.formats[format_index]
        if not fmt and quality_label:
            for f in info.formats:
                if quality_label.lower() in f.label.lower():
                    fmt = f
                    break
        if not fmt and prefer_format:
            for f in info.formats:
                if f.ext == prefer_format:
                    fmt = f
                    break
        if not fmt:
            fmt = info.best_format()
        if not fmt:
            return [], info

        # Construire le nom de fichier propre
        name = _safe_filename(info.title)
        if not name.lower().endswith(f".{fmt.ext}"):
            name = f"{name}.{fmt.ext}"

        # Headers : ceux du site source + extra_headers attaches par l'extracteur
        # (Referer Vidzy obligatoire sur les segments CDN : anti-403)
        headers = get_headers_for_url(fmt.url)
        if getattr(fmt, "extra_headers", None):
            merged = dict(headers)
            merged.update(fmt.extra_headers)
            headers = merged

        youtube_format_id = fmt.format_id if info.extractor == "youtube" else None
        download_url = info.source_url if youtube_format_id else fmt.url
        item_id = self._add_single(
            download_url,
            start_at=start_at,
            headers=headers,
            filename=name,
            stream=fmt.is_hls,
            source_url=info.source_url,
            format_id=youtube_format_id,
            has_audio=fmt.has_audio,
            video_ext=fmt.ext,
        )
        return [item_id], info

    def _add_single(self, url, start_at=None, headers=None, filename=None,
                    stream=False, source_url=None, format_id=None,
                    has_audio=True, video_ext="mp4"):
        """Ajoute un téléchargement unique"""
        name = filename or filename_from_url(url)
        if not name or name == "download":
            name = filename_from_url(url)

        # Extension selon le type
        if stream or is_hls_url(url):
            stem, ext = os.path.splitext(name)
            if ext.lower() not in (".mp4", ".mkv", ".avi"):
                name = stem + ".mp4"

        folder, cat = destination_folder(name, self.base_dir)
        dest = os.path.join(folder, name)

        # Éviter les doublons
        counter = 1
        original_dest = dest
        while os.path.exists(dest) and not self._is_in_queue(dest):
            base, ext = os.path.splitext(original_dest)
            dest = f"{base}_{counter}{ext}"
            counter += 1

        item_id = self.db.add(name, url, dest, cat, format_id=format_id,
                              has_audio=has_audio)

        # Headers appropriés pour l'URL
        if not headers:
            headers = get_headers_for_url(url)

        if format_id:
            dl = YoutubeDownloader(url, dest, format_id, has_audio,
                                   video_ext, headers, self.limiter,
                                   self.n_threads)
        else:
            dl = make_downloader(url, dest, self.n_threads, self.limiter,
                                headers, stream)

        it = Item(item_id, url, dest, cat, dl, start_at, source_url,
                  format_id, has_audio, video_ext)

        if start_at and start_at > time.time():
            it.state = "scheduled"
            self.db.update(item_id, status="scheduled")

        with self._lock:
            self.items[item_id] = it

        return item_id

    def _is_in_queue(self, dest):
        """Vérifie si un fichier est déjà dans la file"""
        for item in self.items.values():
            if item.dest == dest:
                return True
        return False

    def load_unfinished(self):
        """Recharge les téléchargements interrompus"""
        for row in self.db.unfinished():
            if row["id"] in self.items:
                continue

            headers = get_headers_for_url(row["url"])
            format_id = row.get("format_id")
            if format_id:
                ext = os.path.splitext(row["path"])[1].lstrip(".") or "mp4"
                dl = YoutubeDownloader(row["url"], row["path"], format_id,
                                       bool(row.get("has_audio", 1)), ext,
                                       headers, self.limiter, self.n_threads)
            else:
                dl = make_downloader(row["url"], row["path"], self.n_threads,
                                    self.limiter, headers)

            state = load_state(row["path"])
            if state:
                dl.total = state.get("total", 0)
                got = 0
                for s in state.get("segments", []):
                    p = part_path(row["path"], s["i"])
                    if os.path.exists(p):
                        got += min(os.path.getsize(p), s["end"] - s["start"] + 1)
                dl.downloaded = got

            it = Item(row["id"], row["url"], row["path"], row["category"], dl,
                      format_id=format_id,
                      has_audio=bool(row.get("has_audio", 1)),
                      ext=os.path.splitext(row["path"])[1].lstrip(".") or "mp4")
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
                it.dl.pause()
            elif it.state in ("queued", "scheduled"):
                it.state = "paused"
                self.db.update(it.id, status="paused")

    def resume(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if it and it.state in ("paused", "error"):
                # Recréer le downloader pour repartir proprement
                # Conserver les en-têtes ajoutés par l'extracteur (Referer,
                # jetons anti-hotlink, etc.) pour que le serveur accepte la reprise.
                headers = dict(getattr(it.dl, "headers", {}) or {})
                if not headers:
                    headers = get_headers_for_url(it.url)
                is_stream = is_hls_url(it.url)
                if it.format_id:
                    it.dl = YoutubeDownloader(it.url, it.dest, it.format_id,
                                              it.has_audio, it.ext, headers,
                                              self.limiter, self.n_threads)
                else:
                    it.dl = make_downloader(it.url, it.dest, self.n_threads,
                                            self.limiter, headers, is_stream)
                it.start_at = None
                it.state = "queued"
                self.db.update(it.id, status="queued", error=None)

    def remove(self, item_id):
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
                pct = 100 if it.state == "done" else (
                    it.dl.downloaded * 100 / total if total else 0)

                out.append({"id": it.id, "name": it.name, "category": it.category,
                            "state": it.state, "percent": pct,
                            "downloaded": it.dl.downloaded, "total": total,
                            "start_at": it.start_at, "error": it.dl.error,
                            "info": "", "source": it.source_url})
            return out

    def all_finished(self):
        with self._lock:
            return all(i.state in ("done", "error") for i in self.items.values())

    def shutdown(self):
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
