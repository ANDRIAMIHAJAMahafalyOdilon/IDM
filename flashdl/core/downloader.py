import hashlib
import os
import re
import threading
import time
from urllib.parse import urlparse, unquote

import requests

from .config import get_headers_for_url
from .resume import (save_state, load_state, clear_state,
                     part_path, remove_parts)

CHUNK = 256 * 1024
MIN_SIZE_FOR_SPLIT = 4 * 1024 * 1024   # éviter le coût de plages minuscules
MAX_RETRIES = 6
TIMEOUT = 15
RETRYABLE_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


class DownloadError(Exception):
    pass


class _Retry(Exception):
    """Erreur temporaire (429, 503) : on réessaie après une pause."""
    def __init__(self, wait=None):
        super().__init__("serveur occupé (HTTP 429/503)")
        self.wait = wait


class _RestartSingle(Exception):
    """The server contradicted its Range probe; safely restart as one stream."""


def _retry_after(r):
    try:
        return int(r.headers.get("Retry-After", ""))
    except ValueError:
        return None


def probe(url, timeout=TIMEOUT, headers=None, return_headers=False):
    """Renvoie (taille, supporte_range)."""
    try:
        r = requests.get(url, headers={**(headers or {}), "Range": "bytes=0-0"},
                         stream=True, timeout=timeout)
    except requests.RequestException as e:
        raise DownloadError(f"Connexion impossible : {e}")
    try:
        if r.status_code not in (200, 206):
            raise DownloadError(f"Le serveur a répondu HTTP {r.status_code}")
        ctype = r.headers.get("Content-Type", "").lower()
        if ctype.startswith("text/html"):
            raise DownloadError(
                "Ce lien est une page web, pas un fichier. "
                "Utilise un lien direct vers le fichier.")
        if r.status_code == 206:
            try:
                content_range = r.headers["Content-Range"]
                match = re.fullmatch(r"bytes\s+0-0/(\d+)", content_range.strip(), re.I)
                result = (int(match.group(1)), True) if match else (None, False)
            except (KeyError, ValueError):
                result = None, False
        else:
            size = r.headers.get("Content-Length")
            result = (int(size) if size else None), False
        if return_headers:
            return (*result, dict(r.headers))
        return result
    finally:
        r.close()


def filename_from_url(url):
    name = os.path.basename(unquote(urlparse(url).path))
    return name or "download"


def filename_from_response(url, headers=None, timeout=4):
    """Essaie de lire le nom envoyé par le serveur (Content-Disposition)."""
    try:
        response = requests.head(url, headers=headers or {}, allow_redirects=True,
                                 timeout=timeout)
        try:
            disposition = response.headers.get("Content-Disposition", "")
        finally:
            response.close()
    except requests.RequestException:
        return None

    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", disposition, re.I)
    if match:
        candidate = unquote(match.group(1).strip().strip('"'))
    else:
        match = re.search(r'filename\s*=\s*(?:"([^"]+)"|([^;]+))',
                          disposition, re.I)
        if not match:
            return None
        candidate = (match.group(1) or match.group(2)).strip()

    candidate = os.path.basename(candidate.replace("\\", "/"))
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", candidate).strip(" .")
    return candidate or None


class Downloader:
    def __init__(self, url, dest, n_threads=4, expected_sha256=None, limiter=None,
                 headers=None, etag="", last_modified=""):
        # Utiliser les headers appropriés pour l'URL si non fournis
        self.headers = get_headers_for_url(url, headers)
        self.limiter = limiter
        self.url = url
        self.dest = dest
        self.n_threads = max(1, min(int(n_threads or 1), 8))
        self.expected_sha256 = expected_sha256
        self.etag = etag or ""
        self.last_modified = last_modified or ""
        self._if_range = ""
        self.status = "waiting"   # waiting/downloading/merging/paused/done/error
        self.error = None
        self.sha256 = None
        self.total = 0
        self.downloaded = 0
        self.attempts = 0
        self._ranged = False
        self.segments = []
        self._active_segments = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._errors = []
        self._thread = None
        self._force_single = False
        self._network_bytes = 0
        self._server_wait_seconds = 0.0
        self._disk_write_seconds = 0.0
        self._started_monotonic = None
        self._finished_monotonic = None
        self._worker_sessions = []

    # ---------- contrôle ----------
    def start(self):
        """Lance le téléchargement dans un thread (pour l'interface)."""
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def _thread_main(self):
        try:
            self.run()
        except Exception:
            pass   # l'erreur est déjà dans self.error

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def join(self):
        if self._thread:
            self._thread.join()

    def pause(self):
        self._stop.set()

    def segment_snapshot(self):
        """Return byte counts and states for the real engine ranges."""
        if not self._ranged:
            return [{"i": 0, "downloaded": self.downloaded,
                     "total": self.total or None,
                     "state": ("Réception des données..." if 0 in self._active_segments
                               else "Reçu" if self.status == "done"
                               else self.error if self.status == "error"
                               else "En pause" if self.status == "paused"
                               else "En attente")}]
        segments = list(self.segments)
        if not segments:
            return [{"i": 0, "downloaded": self.downloaded,
                     "total": self.total or None,
                     "state": "Réception des données..." if self.status == "downloading"
                     else self.status}]
        rows = []
        for seg in segments:
            path = part_path(self.dest, seg["i"])
            count = min(os.path.getsize(path), seg["end"] - seg["start"] + 1) if os.path.exists(path) else 0
            total = seg["end"] - seg["start"] + 1
            if count >= total:
                state = "Reçu"
            elif seg["i"] in self._active_segments:
                state = "Réception des données..."
            elif self.status == "error":
                state = self.error or "Erreur"
            else:
                state = "En attente"
            rows.append({"i": seg["i"], "downloaded": count,
                         "total": total, "state": state})
        return rows

    def metrics_snapshot(self):
        end_time = self._finished_monotonic or time.monotonic()
        elapsed = (max(0.0, end_time - self._started_monotonic)
                   if self._started_monotonic is not None else 0.0)
        return {
            "elapsed_seconds": elapsed,
            "average_bps": self._network_bytes / elapsed if elapsed else 0.0,
            "network_bytes": self._network_bytes,
            "server_wait_seconds": self._server_wait_seconds,
            "disk_write_seconds": self._disk_write_seconds,
            "active_segments": len(self._active_segments),
            "segment_count": len(self.segments) or 1,
        }

    # ---------- préparation ----------
    def _prepare_segments(self, size, ranges_ok, etag="", last_modified=""):
        self._ranged = bool(ranges_ok and size and not self._force_single)
        if not self._ranged:
            remove_parts(self.dest)
            clear_state(self.dest)
            return [{"i": 0, "start": 0, "end": None}]

        state = load_state(self.dest)
        validators_match = True
        if state:
            saved_etag = state.get("etag") or ""
            saved_modified = state.get("last_modified") or ""
            if saved_etag and etag and saved_etag != etag:
                validators_match = False
            if saved_modified and last_modified and saved_modified != last_modified:
                validators_match = False
            if (saved_etag or saved_modified) and not (etag or last_modified):
                validators_match = False
            if (etag or last_modified) and not (saved_etag or saved_modified):
                validators_match = False
        if (state and state.get("url") == self.url and state.get("total") == size
                and state.get("segments") and validators_match):
            self._if_range = etag or last_modified
            return state["segments"]          # reprise

        remove_parts(self.dest)
        n = self.n_threads if size >= MIN_SIZE_FOR_SPLIT else 1
        part = size // n
        segs = []
        for i in range(n):
            s = i * part
            e = size - 1 if i == n - 1 else s + part - 1
            segs.append({"i": i, "start": s, "end": e})
        self._if_range = etag or last_modified
        save_state(self.dest, {"url": self.url, "total": size, "segments": segs,
                               "etag": etag, "last_modified": last_modified})
        return segs

    def _existing_bytes(self, segments):
        if not self._ranged:
            return 0
        total = 0
        for s in segments:
            p = part_path(self.dest, s["i"])
            if os.path.exists(p):
                total += min(os.path.getsize(p), s["end"] - s["start"] + 1)
        return total

    def _is_complete(self, segments):
        if not self._ranged:
            return not self._stop.is_set()
        for s in segments:
            p = part_path(self.dest, s["i"])
            if (not os.path.exists(p)
                    or os.path.getsize(p) < s["end"] - s["start"] + 1):
                return False
        return True

    # ---------- un segment ----------
    def _worker(self, seg):
        try:
            self._worker_loop(seg)
        except Exception as e:
            self._errors.append(e)
            self._stop.set()    # inutile de continuer les autres segments
        finally:
            self._active_segments.discard(seg["i"])

    def _worker_loop(self, seg):
        i, start, end = seg["i"], seg["start"], seg["end"]
        part = part_path(self.dest, i)
        attempts = 0

        session = requests.Session()
        self._worker_sessions.append(session)
        while not self._stop.is_set():
            done = os.path.getsize(part) if (self._ranged and os.path.exists(part)) else 0
            if self._ranged and start + done > end:
                return   # segment terminé
            self._active_segments.add(i)
            headers = dict(self.headers)
            if self._ranged:
                headers["Range"] = f"bytes={start + done}-{end}"
                if self._if_range:
                    headers["If-Range"] = self._if_range
            got = 0
            try:
                with session.get(self.url, headers=headers,
                                 stream=True, timeout=TIMEOUT,
                                 allow_redirects=True) as r:
                    if r.status_code in (429, 503):
                        raise _Retry(_retry_after(r))
                    expected = 206 if self._ranged else 200
                    if r.status_code != expected:
                        if self._ranged and r.status_code in (200, 416):
                            raise _RestartSingle(
                                "Le serveur a refusé une plage; redémarrage en flux unique.")
                        raise DownloadError(
                            f"Segment {i} : réponse HTTP {r.status_code}")
                    if self._ranged:
                        response_etag = r.headers.get("ETag", "")
                        response_modified = r.headers.get("Last-Modified", "")
                        if ((self.etag and response_etag and self.etag != response_etag) or
                                (self.last_modified and response_modified and
                                 self.last_modified != response_modified)):
                            raise _RestartSingle(
                                "La ressource a changé pendant la reprise; "
                                "les segments partiels seront écartés.")
                        match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+|\*)",
                                             r.headers.get("Content-Range", "").strip(), re.I)
                        if (not match or int(match.group(1)) != start + done or
                                int(match.group(2)) > end or
                                self.total and match.group(3) != "*" and
                                int(match.group(3)) != self.total):
                            raise DownloadError(f"Segment {i} : plage reçue incohérente")
                    with open(part, "ab" if self._ranged else "wb") as f:
                        chunks = iter(r.iter_content(CHUNK))
                        while True:
                            wait_started = time.monotonic()
                            try:
                                chunk = next(chunks)
                            except StopIteration:
                                break
                            with self._lock:
                                self._server_wait_seconds += time.monotonic() - wait_started
                            if self._stop.is_set():
                                return
                            if self.limiter:
                                self.limiter.wait(len(chunk), self._stop)
                                if self._stop.is_set():
                                    return
                            write_started = time.monotonic()
                            f.write(chunk)
                            write_elapsed = time.monotonic() - write_started
                            got += len(chunk)
                            with self._lock:
                                self.downloaded += len(chunk)
                                self._network_bytes += len(chunk)
                                self._disk_write_seconds += write_elapsed
                        flush_started = time.monotonic()
                        f.flush()
                        with self._lock:
                            self._disk_write_seconds += time.monotonic() - flush_started
                self._active_segments.discard(i)
                if not self._ranged:
                    return
                if os.path.getsize(part) <= done:
                    raise OSError("connexion fermée sans données")
            except (_Retry, OSError) + RETRYABLE_ERRORS as e:
                self._active_segments.discard(i)
                self.attempts += 1
                if not self._ranged:
                    with self._lock:
                        self.downloaded -= got
                else:
                    cur = os.path.getsize(part) if os.path.exists(part) else 0
                    if cur > done:
                        attempts = 0   # on a avancé : on repart à zéro
                attempts += 1
                if isinstance(e, _Retry) and self._ranged and self.n_threads > 1:
                    raise
                if attempts > MAX_RETRIES:
                    raise DownloadError(
                        f"Segment {i} : abandon après {MAX_RETRIES} essais ({e})")
                wait = getattr(e, "wait", None) or min(2 ** attempts, 30)
                if self._stop.wait(wait):   # attend, mais réagit à la pause
                    return

    # ---------- fusion + vérification ----------
    def _merge(self, segments):
        self.status = "assembling"
        disk_started = time.monotonic()
        h = hashlib.sha256()
        tmp = self.dest + ".tmp"
        with open(tmp, "wb") as out:
            for s in segments:
                with open(part_path(self.dest, s["i"]), "rb") as p:
                    while True:
                        block = p.read(1024 * 1024)
                        if not block:
                            break
                        out.write(block)
                        h.update(block)

        final_size = os.path.getsize(tmp)
        digest = h.hexdigest()
        self.status = "verifying"

        problem = None
        if self.total and final_size != self.total:
            problem = (f"taille incorrecte : {final_size} octets "
                       f"au lieu de {self.total}")
        elif self.expected_sha256 and digest != self.expected_sha256.lower():
            problem = "le hash SHA-256 ne correspond pas"
        if problem:
            os.remove(tmp)
            remove_parts(self.dest)
            clear_state(self.dest)
            raise DownloadError("Fichier corrompu, " + problem)

        self.status = "finalizing"
        os.replace(tmp, self.dest)
        with self._lock:
            self._disk_write_seconds += time.monotonic() - disk_started
        remove_parts(self.dest)
        clear_state(self.dest)
        self.sha256 = digest

    # ---------- point d'entrée ----------
    def run(self):
        # The event starts unset; do not clear it here. A pause can arrive
        # just after start() and before this worker thread enters run().
        self._errors = []
        self.error = None
        self.status = "downloading"
        self._started_monotonic = time.monotonic()
        self._finished_monotonic = None
        try:
            self._run()
        except Exception as e:
            self.status = "error"
            self.error = str(e)
            raise
        finally:
            self._finished_monotonic = time.monotonic()

    def _run(self):
        size, ranges_ok, response_headers = probe(
            self.url, headers=self.headers, return_headers=True)
        current_etag = response_headers.get("ETag", "")
        current_modified = response_headers.get("Last-Modified", "")
        old_validator = self.etag or self.last_modified
        new_validator = current_etag or current_modified
        if old_validator and new_validator and old_validator != new_validator:
            from .resume import clear_state, remove_parts
            clear_state(self.dest)
            remove_parts(self.dest)
            self.downloaded = 0
            raise DownloadError(
                "Le fichier distant a changé depuis la dernière reprise. "
                "Les données partielles ont été écartées; redémarre le transfert.")
        self.etag = current_etag or self.etag
        self.last_modified = current_modified or self.last_modified
        self.total = size or 0
        segments = self._prepare_segments(size, ranges_ok, current_etag,
                                          current_modified)
        self.segments = segments
        self.downloaded = self._existing_bytes(segments)
        if self._started_monotonic is None:
            self._started_monotonic = time.monotonic()

        threads = [threading.Thread(target=self._worker, args=(s,), daemon=True)
                   for s in segments]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        for session in self._worker_sessions:
            session.close()
        self._worker_sessions.clear()

        if self._errors and self._ranged and any(
                isinstance(error, _RestartSingle) for error in self._errors):
            from .resume import clear_state, remove_parts
            clear_state(self.dest)
            remove_parts(self.dest)
            self._stop.clear()
            self._errors.clear()
            self._force_single = True
            self._ranged = False
            self.downloaded = 0
            return self._run()
        if self._errors and self._ranged and self.n_threads > 1 and any(
                isinstance(error, _Retry) for error in self._errors):
            from .resume import clear_state, remove_parts
            clear_state(self.dest)
            remove_parts(self.dest)
            self._stop.clear()
            self.downloaded = 0
            retry_delays = [error.wait for error in self._errors
                            if isinstance(error, _Retry) and error.wait is not None]
            retry_delay = min(30, max(retry_delays, default=1))
            if self._stop.wait(retry_delay):
                self.status = "paused"
                return
            self._errors.clear()
            self.n_threads = max(1, self.n_threads // 2)
            self.downloaded = 0
            return self._run()
        if self._errors:
            raise DownloadError(str(self._errors[0]))

        self.status = "verifying"
        if not self._is_complete(segments):
            self.status = "paused"
            return

        if not self._ranged and not self.total:
            self.total = self.downloaded
        self._merge(segments)
        self.status = "done"
