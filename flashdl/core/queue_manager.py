import os
import glob
import shutil
import threading
import time
from urllib.parse import urlsplit

from .categories import category_for, destination_folder
from .database import now
from .downloader import Downloader, filename_from_url
from .metadata import clean_filename, probe_download
from .hls import HlsDownloader, is_hls_url
from .config import get_headers_for_url
from .resume import load_state, part_path
from .extractors import extract, is_extractable_url, _safe_filename
from .youtube_downloader import YoutubeDownloader


def make_downloader(url, dest, n_threads, limiter, headers=None, stream=False,
                    estimated_size=0):
    """
    Factory pour les téléchargements simples et les flux HLS
    """
    headers = headers or {}

    if stream or is_hls_url(url):
        return HlsDownloader(url, dest, n_threads=n_threads, limiter=limiter,
                             headers=headers, estimated_size=estimated_size)
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
        self.description = ""
        self.mime_type = ""
        self.initial_url = url
        self.added_at = None
        self.queue_name = "Principale"
        self.prepared = None


class QueueManager:
    def __init__(self, db, base_dir, max_parallel=3, n_threads=4, limiter=None):
        self.db = db
        self.base_dir = base_dir
        self.max_parallel = max_parallel
        self.n_threads = max(1, min(int(n_threads or 1), 8))
        self.limiter = limiter
        self.items = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._loop_thread = threading.Thread(target=self._loop, daemon=True)
        self._loop_thread.start()

    # ---------- ajout / contrôle ----------
    def add(self, url, start_at=None, headers=None, filename=None, stream=False,
            source_url=None, **options):
        """Prepare a URL and add it to the queue without bypassing caller policy."""
        prepared = self.prepare(url, headers=headers, filename=filename,
                                stream=stream, source_url=source_url,
                                video_info=options.pop("video_info", None),
                                format_index=options.pop("format_index", None))
        if prepared.get("error"):
            return None, prepared["error"]
        options.setdefault("auto_rename", True)
        return self.add_prepared(prepared, start_at=start_at, **options), \
            prepared.get("video_info")

    def prepare(self, url, headers=None, filename=None, stream=False,
                source_url=None, video_info=None, format_index=None,
                extra_headers=None):
        """Inspect metadata and create a transfer description, without starting it."""
        if video_info is not None or format_index is not None or is_extractable_url(url):
            info = video_info or extract(url, extra_headers=extra_headers or headers)
            if not info.success:
                return {"error": info.error or "Impossible d’analyser cette vidéo.",
                        "initial_url": url}
            formats = info.formats or []
            fmt = formats[format_index] if (format_index is not None and
                                            0 <= format_index < len(formats)) else None
            fmt = fmt or info.best_format()
            if not fmt:
                return {"error": "Aucun format vidéo disponible.", "initial_url": url}
            name = clean_filename(info.title) or "video"
            ext = (fmt.ext or "").lower().lstrip(".")
            if ext and os.path.splitext(name)[1].lower() != "." + ext:
                name += "." + ext
            request_headers = get_headers_for_url(fmt.url)
            if getattr(fmt, "extra_headers", None):
                request_headers.update(fmt.extra_headers)
            format_id = (fmt.format_id if info.extractor in ("youtube", "yt-dlp")
                         else None)
            return {
                "initial_url": url,
                "url": info.source_url if format_id else fmt.url,
                "source_url": info.source_url or source_url or url,
                "name": name, "size": fmt.size,
                "mime_type": "video/" + ext if ext else "",
                "extension": "." + ext if ext else "",
                "etag": "", "last_modified": "", "supports_range": None,
                "headers": request_headers, "stream": bool(fmt.is_hls),
                "format_id": format_id, "has_audio": fmt.has_audio,
                "video_ext": ext or "mp4", "video_info": info,
                "format_index": info.formats.index(fmt),
            }

        if urlsplit(url).path.lower().endswith(".mpd"):
            return {"error": "Les flux DASH (.mpd) ne sont pas encore pris en charge. "
                             "Les téléchargements HTTP directs et les flux HLS sont disponibles.",
                    "initial_url": url}

        request_headers = get_headers_for_url(url, headers or extra_headers)
        if stream or is_hls_url(url):
            name = clean_filename(filename) or filename_from_url(url)
            stem, ext = os.path.splitext(name)
            if ext.lower() not in {".mp4", ".mkv", ".avi", ".mov", ".webm"}:
                name = (stem or name) + ".mp4"
            return {
                "initial_url": url, "url": url,
                "source_url": source_url, "name": name, "size": None,
                "mime_type": "application/vnd.apple.mpegurl",
                "extension": os.path.splitext(name)[1], "etag": "",
                "last_modified": "", "supports_range": False,
                "headers": request_headers, "stream": True,
                "format_id": None, "has_audio": True,
                "video_ext": os.path.splitext(name)[1].lstrip(".") or "mp4",
            }

        meta = probe_download(url, headers=request_headers,
                              explicit_name=filename or "")
        name = meta.name
        return {
            "initial_url": url, "url": meta.final_url or url,
            "source_url": source_url, "name": name, "size": meta.size,
            "mime_type": meta.mime_type, "extension": os.path.splitext(name)[1],
            "etag": meta.etag, "last_modified": meta.last_modified,
            "supports_range": meta.supports_range, "headers": request_headers,
            "stream": False, "format_id": None, "has_audio": True,
            "video_ext": os.path.splitext(name)[1].lstrip(".") or "",
        }

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

        prepared = self.prepare(url, video_info=info,
                                format_index=info.formats.index(fmt),
                                extra_headers=extra_headers)
        item_id = self.add_prepared(prepared, start_at=start_at)
        return [item_id], info

    def _add_single(self, url, start_at=None, headers=None, filename=None,
                    stream=False, source_url=None, format_id=None,
                    has_audio=True, video_ext="mp4"):
        """Ajoute un téléchargement unique"""
        prepared = self.prepare(url, headers=headers, filename=filename,
                                stream=stream, source_url=source_url)
        prepared.update(format_id=format_id, has_audio=has_audio,
                        video_ext=video_ext)
        return self.add_prepared(prepared, start_at=start_at)

    def add_prepared(self, prepared, filename=None, path=None, category=None,
                     description="", deferred=False, start_at=None,
                     queue_name="Principale", replace_existing=False,
                     auto_rename=False):
        """Persist a prepared transfer only after the confirmation dialog."""
        name = clean_filename(filename or prepared.get("name"))
        if not name:
            raise ValueError("Le nom du fichier est invalide.")
        category = category or category_for(name, prepared.get("mime_type"))
        remembered = {row["name"]: row["folder"] for row in self.db.categories()
                      if row["remember_path"]}
        default_folder = remembered.get(category) or os.path.join(self.base_dir, category)
        dest = os.path.abspath(os.path.normpath(path or os.path.join(default_folder, name)))
        if os.path.basename(dest) != name:
            name = clean_filename(os.path.basename(dest))
            dest = os.path.join(os.path.dirname(dest), name)
        if not os.path.basename(dest):
            raise ValueError("Le chemin de destination est invalide.")
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        with self._lock:
            queued_conflict = self._is_in_queue(dest)
            conflict = os.path.exists(dest) or queued_conflict
            if conflict and auto_rename:
                stem, extension = os.path.splitext(dest)
                index = 1
                while os.path.exists(dest) or self._is_in_queue(dest):
                    dest = f"{stem} ({index}){extension}"
                    index += 1
                name = os.path.basename(dest)
                queued_conflict = self._is_in_queue(dest)
            if queued_conflict or (os.path.exists(dest) and not replace_existing):
                raise FileExistsError(dest)
            if replace_existing and os.path.exists(dest):
                for old in self.db.history():
                    if (os.path.normcase(old.get("path") or "") == os.path.normcase(dest)
                            and old.get("status") == "done"):
                        self.db.update(old["id"], status="replaced",
                                       error="Le fichier a été remplacé par un nouveau téléchargement.")

            headers = dict(prepared.get("headers") or {})
            url = prepared.get("url") or prepared.get("initial_url")
            format_id = prepared.get("format_id")
            has_audio = bool(prepared.get("has_audio", True))
            ext = prepared.get("video_ext") or os.path.splitext(name)[1].lstrip(".")
            item_id = self.db.add(
                name, url, dest, category, format_id=format_id,
                has_audio=has_audio,
                initial_url=prepared.get("initial_url") or url,
                extension=os.path.splitext(name)[1], description=description,
                mime_type=prepared.get("mime_type", ""),
                size=prepared.get("size"), downloaded=0,
                etag=prepared.get("etag", ""),
                last_modified=prepared.get("last_modified", ""),
                supports_range=int(bool(prepared.get("supports_range"))),
                queue_name=queue_name,
                scheduled_at=(start_at if start_at and start_at > time.time()
                              and not deferred else None))

            if format_id:
                dl = YoutubeDownloader(url, dest, format_id, has_audio, ext,
                                       headers, self.limiter, self.n_threads)
            else:
                dl = make_downloader(url, dest, self.n_threads, self.limiter,
                                     headers, prepared.get("stream", False),
                                     prepared.get("estimated_total", 0))
            dl.etag = prepared.get("etag", "")
            dl.last_modified = prepared.get("last_modified", "")
            state = "deferred" if deferred else "queued"
            it = Item(item_id, url, dest, category, dl, start_at,
                      prepared.get("source_url"), format_id, has_audio, ext)
            it.state = state
            if start_at and start_at > time.time() and not deferred:
                it.state = "scheduled"
            it.initial_url = prepared.get("initial_url") or url
            it.description = description
            it.mime_type = prepared.get("mime_type", "")
            it.added_at = now()
            it.queue_name = queue_name
            it.prepared = prepared
            self.items[item_id] = it
            self.db.update(item_id, status=it.state)
            return item_id

    def _is_in_queue(self, dest):
        """Vérifie si un fichier est déjà dans la file"""
        for item in self.items.values():
            if (os.path.normcase(item.dest) == os.path.normcase(dest) and
                    item.state in ("queued", "scheduled", "deferred", "active",
                                   "paused", "interrupted", "error")):
                return True
        return False

    def load_unfinished(self):
        """Recharge les téléchargements interrompus"""
        self._reconcile_completed()
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
            dl.etag = row.get("etag") or ""
            dl.last_modified = row.get("last_modified") or ""

            state = load_state(row["path"])
            if state:
                dl.total = state.get("total", 0)
                got = 0
                for s in state.get("segments", []):
                    p = part_path(row["path"], s["i"])
                    if os.path.exists(p):
                        got += min(os.path.getsize(p), s["end"] - s["start"] + 1)
                dl.downloaded = got

            scheduled_at = row.get("scheduled_at")
            it = Item(row["id"], row["url"], row["path"], row["category"], dl,
                      start_at=scheduled_at,
                      format_id=format_id,
                      has_audio=bool(row.get("has_audio", 1)),
                      ext=os.path.splitext(row["path"])[1].lstrip(".") or "mp4")
            it.initial_url = row.get("initial_url") or row["url"]
            it.description = row.get("description") or ""
            it.mime_type = row.get("mime_type") or ""
            it.added_at = row.get("added_at")
            it.queue_name = row.get("queue_name") or "Principale"
            saved_state = row.get("status")
            if saved_state in ("deferred", "interrupted"):
                it.state = saved_state
            elif saved_state == "scheduled" and scheduled_at and scheduled_at > time.time():
                it.state = "scheduled"
            elif saved_state == "scheduled":
                it.state = "queued"
                self.db.update(row["id"], status="queued", scheduled_at=None)
            else:
                it.state = "paused"
            with self._lock:
                self.items[row["id"]] = it
            self.db.update(row["id"], status=it.state)

    def _reconcile_completed(self):
        for row in self.db.history():
            if row.get("status") != "done":
                continue
            path = row.get("path") or ""
            if not os.path.isfile(path):
                self.db.update(row["id"], status="error",
                               error="Le fichier terminé est introuvable.")
                continue
            actual = os.path.getsize(path)
            expected = row.get("size")
            if expected and actual != expected:
                self.db.update(row["id"], status="error",
                               error="Le fichier enregistré est incomplet ou sa taille a changé.",
                               size=actual, downloaded=actual)

    def pause(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if not it:
                return
            if it.state == "active":
                it.dl.pause()
            elif it.state in ("queued", "scheduled"):
                it.state = "paused"
                self.db.update(it.id, status="paused", scheduled_at=None)

    def stop(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if not it:
                self.db.update(item_id, status="interrupted")
                return
            if it.state == "active":
                it.dl.pause()
                it.dl.join()
            if it.state in ("active", "queued", "scheduled"):
                it.state = "interrupted"
                self.db.update(item_id, status="interrupted",
                               downloaded=getattr(it.dl, "downloaded", 0),
                               scheduled_at=None)

    def cancel(self, item_id):
        """Request cancellation without joining network workers on the GUI thread."""
        with self._lock:
            it = self.items.get(item_id)
            if not it:
                self.db.update(item_id, status="cancelled")
                return
            if it.state in ("active", "queued", "scheduled", "paused"):
                it.state = "cancelling"
                it.dl.pause()
                self.db.update(item_id, status="cancelled", scheduled_at=None,
                               downloaded=getattr(it.dl, "downloaded", 0))

    def get_item(self, item_id):
        with self._lock:
            item = self.items.get(item_id)
        return item or self.db.get(item_id)

    def activate_deferred(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if it and it.state == "deferred":
                it.state = "queued"
                self.db.update(item_id, status="queued")
                return True
        return False

    def update_url(self, item_id, url, prepared=None):
        if not url.lower().startswith(("http://", "https://")):
            raise ValueError("L’adresse doit commencer par http:// ou https://.")
        with self._lock:
            it = self.items.get(item_id)
            row = self.db.get(item_id)
            if not row:
                raise KeyError("Téléchargement introuvable.")
            if it and it.state == "active":
                raise RuntimeError("Mets le téléchargement en pause avant de changer l’URL.")
            old_url = row.get("url")
            old_validators = (row.get("etag"), row.get("last_modified"))
            new_validators = ((prepared or {}).get("etag"),
                              (prepared or {}).get("last_modified"))
            old_size = row.get("size")
            new_size = (prepared or {}).get("size")
            has_validator = any(old_validators) and any(new_validators)
            if (old_url != url and
                    (not has_validator or old_validators != new_validators or
                     old_size is not None and new_size is not None and old_size != new_size)):
                if it:
                    it.dl.pause()
                    it.dl.join()
                from .resume import clear_state, remove_parts
                clear_state(row["path"])
                remove_parts(row["path"])
                shutil.rmtree(row["path"] + ".hls", ignore_errors=True)
                if it:
                    it.dl.downloaded = 0
                    it.dl.total = 0
            self.db.update(item_id, url=url, etag=new_validators[0] or "",
                           last_modified=new_validators[1] or "", downloaded=0,
                           size=(prepared or {}).get("size"),
                           mime_type=(prepared or {}).get("mime_type") or row.get("mime_type"),
                           supports_range=int(bool((prepared or {}).get("supports_range"))),
                           status="paused")
            if it:
                it.url = url
                it.state = "paused"
                if prepared:
                    it.prepared = prepared
                    it.dl = make_downloader(url, it.dest, self.n_threads,
                                            self.limiter, prepared.get("headers"),
                                            prepared.get("stream", False))
                    it.dl.etag = new_validators[0] or ""
                    it.dl.last_modified = new_validators[1] or ""
            return {"changed": old_url != url,
                    "validators_changed": old_validators != new_validators}

    def move_rename(self, item_id, new_path):
        new_path = os.path.abspath(os.path.normpath(new_path))
        if not os.path.basename(new_path):
            raise ValueError("Le chemin de destination est invalide.")
        with self._lock:
            it = self.items.get(item_id)
            row = self.db.get(item_id)
            if not row:
                raise KeyError("Téléchargement introuvable.")
            if it and it.state in ("active", "queued", "scheduled"):
                raise RuntimeError("Mets le téléchargement en pause avant de le déplacer.")
            if os.path.exists(new_path):
                raise FileExistsError(new_path)
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            old_path = row["path"]
            moves = []
            if os.path.exists(old_path):
                moves.append((old_path, new_path))
            # Include only sidecars that belong to this download, and validate
            # every destination before moving anything on disk.
            for old_aux in glob.glob(old_path + ".part*") + [old_path + ".flashdl"]:
                if os.path.exists(old_aux):
                    moves.append((old_aux, new_path + old_aux[len(old_path):]))
            if os.path.isdir(old_path + ".hls"):
                moves.append((old_path + ".hls", new_path + ".hls"))
            collision = next((target for _, target in moves if os.path.exists(target)), None)
            if collision:
                raise FileExistsError(collision)
            moved = []
            try:
                for source, target in moves:
                    os.replace(source, target)
                    moved.append((source, target))
            except OSError:
                # Keep the database and disk paths aligned if a later sidecar
                # move fails after the main file has already moved.
                for source, target in reversed(moved):
                    try:
                        os.replace(target, source)
                    except OSError:
                        pass
                raise
            self.db.update(item_id, path=new_path,
                           name=os.path.basename(new_path),
                           extension=os.path.splitext(new_path)[1],
                           category=row.get("category"))
            if it:
                it.dest = new_path
                it.name = os.path.basename(new_path)
                it.dl.dest = new_path
            return new_path

    def change_queue(self, item_id, queue_name):
        self.db.add_queue(queue_name)
        self.db.update(item_id, queue_name=queue_name)
        with self._lock:
            it = self.items.get(item_id)
            if it:
                it.queue_name = queue_name

    def remove_entry(self, item_id, delete_file=False, delete_partial=False):
        with self._lock:
            it = self.items.get(item_id)
            if it and it.state == "active":
                it.dl.pause()
                it.dl.join()
            row = self.db.get(item_id)
            if not row:
                return
            if delete_file and os.path.isfile(row["path"]):
                try:
                    os.remove(row["path"])
                except FileNotFoundError:
                    pass  # A concurrent external delete is already the desired state.
            if delete_partial:
                from .resume import clear_state, remove_parts
                clear_state(row["path"])
                remove_parts(row["path"])
                for partial in glob.glob(row["path"] + ".part*") + [
                        row["path"] + ".flashdl.tmp",
                        row["path"] + ".join.ts"]:
                    if os.path.isfile(partial):
                        try:
                            os.remove(partial)
                        except FileNotFoundError:
                            pass
                    elif os.path.isdir(partial):
                        try:
                            shutil.rmtree(partial)
                        except FileNotFoundError:
                            pass
                shutil.rmtree(row["path"] + ".hls", ignore_errors=True)
            if it:
                self.items.pop(item_id, None)
            self.db.delete(item_id)

    def queue_enabled(self, queue_name):
        queue = next((q for q in self.db.queues() if q["name"] == queue_name), None)
        return bool(queue and queue["enabled"])

    def resume(self, item_id):
        with self._lock:
            it = self.items.get(item_id)
            if it and it.state == "deferred":
                it.state = "queued"
                self.db.update(it.id, status="queued", error=None)
            elif it and it.state in ("paused", "interrupted", "error"):
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
                row = self.db.get(item_id) or {}
                it.dl.etag = row.get("etag") or ""
                it.dl.last_modified = row.get("last_modified") or ""
                it.start_at = None
                it.state = "queued"
                self.db.update(it.id, status="queued", error=None,
                               attempts=(row.get("attempts") or 0) + 1,
                               scheduled_at=None)

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
                        self.db.update(it.id, status="queued", scheduled_at=None)

                running = sum(1 for i in self.items.values()
                              if i.state in ("active", "cancelling"))
                queue_config = {q["name"]: q for q in self.db.queues()}
                queue_running = {}
                for current in self.items.values():
                    if current.state in ("active", "cancelling"):
                        queue_running[current.queue_name] = queue_running.get(current.queue_name, 0) + 1
                for it in self.items.values():
                    if running >= self.max_parallel:
                        break
                    queue = queue_config.get(it.queue_name, {"enabled": 1,
                                                              "max_parallel": self.max_parallel})
                    if (it.state == "queued" and queue["enabled"] and
                            queue_running.get(it.queue_name, 0) < queue["max_parallel"]):
                        it.dl.start()
                        it.state = "active"
                        row = self.db.get(it.id) or {}
                        self.db.update(it.id, status="downloading",
                                       started_at=row.get("started_at") or now())
                        running += 1
                        queue_running[it.queue_name] = queue_running.get(it.queue_name, 0) + 1
            time.sleep(0.3)

    def _refresh(self):
        for it in self.items.values():
            if it.state == "cancelling":
                if not it.dl.is_alive():
                    it.state = "cancelled"
                    self.db.update(it.id, status="cancelled",
                                   downloaded=getattr(it.dl, "downloaded", 0),
                                   finished_at=now())
                continue
            if it.state == "active":
                downloaded = getattr(it.dl, "downloaded", 0)
                bucket = downloaded // (256 * 1024)
                attempts = getattr(it.dl, "attempts", 0)
                if (bucket != getattr(it, "_persisted_bucket", -1) or
                        attempts != getattr(it, "_persisted_attempts", 0)):
                    it._persisted_bucket = bucket
                    it._persisted_attempts = attempts
                    measured_total = getattr(it.dl, "total", 0)
                    if getattr(it.dl, "total_is_estimated", False):
                        measured_total = None
                    self.db.update(it.id, downloaded=downloaded,
                                   size=measured_total or None,
                                   attempts=attempts)
            if it.state != "active" or it.dl.is_alive():
                continue
            s = it.dl.status
            if s == "done":
                if not os.path.isfile(it.dest):
                    it.state = "error"
                    self.db.update(it.id, status="error",
                                   error="Le moteur a terminé sans créer le fichier.")
                    continue
                actual_size = os.path.getsize(it.dest)
                if getattr(it.dl, "total", 0) and actual_size != it.dl.total:
                    it.state = "error"
                    self.db.update(it.id, status="error",
                                   error="Le fichier final est incomplet ou sa taille est incohérente.")
                    continue
                it.state = "done"
                row = self.db.get(it.id) or {}
                duration = None
                try:
                    started = time.mktime(time.strptime(row.get("started_at"),
                                                        "%Y-%m-%d %H:%M:%S"))
                    duration = max(0, time.time() - started)
                except (TypeError, ValueError, OverflowError):
                    pass
                self.db.update(it.id, status="done", size=actual_size,
                               downloaded=actual_size,
                               sha256=it.dl.sha256, finished_at=now(),
                               duration=duration)
            elif s == "paused":
                it.state = "paused"
                self.db.update(it.id, status="paused",
                               downloaded=getattr(it.dl, "downloaded", 0))
            else:
                it.state = "error"
                self.db.update(it.id, status="error", error=it.dl.error)

    # ---------- infos ----------
    def snapshot(self):
        with self._lock:
            out = []
            present = set()
            for it in self.items.values():
                row = self.db.get(it.id) or {}
                if it.state == "done":
                    if not os.path.isfile(it.dest):
                        it.state = "error"
                        row["error"] = "Le fichier terminé a été supprimé ou déplacé."
                        self.db.update(it.id, status="error",
                                       error=row["error"])
                    else:
                        actual = os.path.getsize(it.dest)
                        expected = row.get("size") or getattr(it.dl, "total", 0)
                        if expected and actual != expected:
                            it.state = "error"
                            row["error"] = "Le fichier terminé a été modifié ou est incomplet."
                            self.db.update(it.id, status="error", size=actual,
                                           downloaded=actual,
                                           error=row["error"])
                total = it.dl.total
                pct = 100 if it.state == "done" else (
                    it.dl.downloaded * 100 / total if total else 0)

                downloaded = getattr(it.dl, "downloaded", row.get("downloaded", 0) or 0)
                shown_total = total or row.get("size")
                if it.state == "done" and os.path.isfile(it.dest):
                    shown_total = os.path.getsize(it.dest)
                out.append({"id": it.id, "name": it.name, "category": it.category,
                            "state": it.state, "percent": pct,
                            "downloaded": downloaded, "total": shown_total,
                            "size_estimated": bool(getattr(it.dl, "total_is_estimated", False)),
                            "start_at": it.start_at, "error": it.dl.error or row.get("error"),
                            "info": "", "source": it.source_url,
                            "url": row.get("url", it.url), "path": it.dest,
                            "description": getattr(it, "description", ""),
                            "mime_type": getattr(it, "mime_type", ""),
                            "added_at": row.get("added_at"),
                            "started_at": row.get("started_at"),
                            "finished_at": row.get("finished_at"),
                            "queue_name": getattr(it, "queue_name", "Principale"),
                            "attempts": row.get("attempts", 0),
                            "engine_status": getattr(it.dl, "status", ""),
                            "metrics": (it.dl.metrics_snapshot()
                                        if hasattr(it.dl, "metrics_snapshot") else {})})
                present.add(it.id)
            for row in self.db.history():
                if row["id"] in present:
                    continue
                status = row.get("status") or "error"
                total = row.get("size")
                downloaded = row.get("downloaded") or 0
                if status == "done":
                    path = row.get("path") or ""
                    if not os.path.isfile(path):
                        status = "error"
                        row["error"] = "Le fichier terminé a été supprimé ou déplacé."
                        self.db.update(row["id"], status="error",
                                       error=row["error"])
                    else:
                        actual = os.path.getsize(path)
                        if total and actual != total:
                            status = "error"
                            row["error"] = "Le fichier terminé a été modifié ou est incomplet."
                            self.db.update(row["id"], status="error", size=actual,
                                           downloaded=actual,
                                           error=row["error"])
                        else:
                            downloaded = total = actual
                percent = 100 if status == "done" else (
                    downloaded * 100 / total if total else 0)
                out.append({"id": row["id"], "name": row.get("name") or "Fichier",
                            "category": row.get("category") or "Général",
                            "state": status, "percent": percent,
                            "downloaded": downloaded, "total": total,
                            "size_estimated": False,
                            "start_at": None, "error": row.get("error"),
                            "info": "", "source": None, "url": row.get("url"),
                            "path": row.get("path"),
                            "description": row.get("description") or "",
                            "mime_type": row.get("mime_type") or "",
                            "added_at": row.get("added_at"),
                            "started_at": row.get("started_at"),
                            "finished_at": row.get("finished_at"),
                            "queue_name": row.get("queue_name") or "Principale",
                            "attempts": row.get("attempts", 0),
                            "engine_status": status})
            out.sort(key=lambda item: item["id"], reverse=True)
            return out

    def all_finished(self):
        with self._lock:
            return all(i.state in ("done", "error", "cancelled", "interrupted",
                                   "deferred") for i in self.items.values())

    def shutdown(self):
        self._stop.set()
        self._loop_thread.join()
        with self._lock:
            for it in self.items.values():
                if it.state == "active":
                    it.dl.pause()
                elif it.state == "queued":
                    it.state = "paused"
                    self.db.update(it.id, status="paused")
            for it in self.items.values():
                it.dl.join()
            self._refresh()
