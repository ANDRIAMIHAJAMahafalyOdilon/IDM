import hashlib
import os
import threading
from urllib.parse import urlparse, unquote

import requests

from .resume import (save_state, load_state, clear_state,
                     part_path, remove_parts)

CHUNK = 64 * 1024
MIN_SIZE_FOR_SPLIT = 1024 * 1024   # sous 1 Mo : une seule connexion
MAX_RETRIES = 6
TIMEOUT = 15


class DownloadError(Exception):
    pass


class _Retry(Exception):
    """Erreur temporaire (429, 503) : on réessaie après une pause."""
    def __init__(self, wait=None):
        super().__init__("serveur occupé (HTTP 429/503)")
        self.wait = wait


def _retry_after(r):
    try:
        return int(r.headers.get("Retry-After", ""))
    except ValueError:
        return None


def probe(url, timeout=TIMEOUT, headers=None):
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
                return int(r.headers["Content-Range"].split("/")[-1]), True
            except (KeyError, ValueError):
                return None, False
        size = r.headers.get("Content-Length")
        return (int(size) if size else None), False
    finally:
        r.close()


def filename_from_url(url):
    name = os.path.basename(unquote(urlparse(url).path))
    return name or "download"


class Downloader:
    def __init__(self, url, dest, n_threads=8, expected_sha256=None, limiter=None,
                 headers=None):
        self.headers = dict(headers or {})
        self.limiter = limiter
        self.url = url
        self.dest = dest
        self.n_threads = n_threads
        self.expected_sha256 = expected_sha256
        self.status = "waiting"   # waiting/downloading/merging/paused/done/error
        self.error = None
        self.sha256 = None
        self.total = 0
        self.downloaded = 0
        self._ranged = False
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._errors = []
        self._thread = None

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

    # ---------- préparation ----------
    def _prepare_segments(self, size, ranges_ok):
        self._ranged = bool(ranges_ok and size)
        if not self._ranged:
            remove_parts(self.dest)
            clear_state(self.dest)
            return [{"i": 0, "start": 0, "end": None}]

        state = load_state(self.dest)
        if (state and state.get("url") == self.url
                and state.get("total") == size and state.get("segments")):
            return state["segments"]          # reprise

        remove_parts(self.dest)
        n = self.n_threads if size >= MIN_SIZE_FOR_SPLIT else 1
        part = size // n
        segs = []
        for i in range(n):
            s = i * part
            e = size - 1 if i == n - 1 else s + part - 1
            segs.append({"i": i, "start": s, "end": e})
        save_state(self.dest, {"url": self.url, "total": size, "segments": segs})
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

    def _worker_loop(self, seg):
        i, start, end = seg["i"], seg["start"], seg["end"]
        part = part_path(self.dest, i)
        attempts = 0

        while not self._stop.is_set():
            done = os.path.getsize(part) if (self._ranged and os.path.exists(part)) else 0
            if self._ranged and start + done > end:
                return   # segment terminé
            headers = dict(self.headers)
            if self._ranged:
                headers["Range"] = f"bytes={start + done}-{end}"
            got = 0
            try:
                with requests.get(self.url, headers=headers,
                                  stream=True, timeout=TIMEOUT) as r:
                    if r.status_code in (429, 503):
                        raise _Retry(_retry_after(r))
                    expected = 206 if self._ranged else 200
                    if r.status_code != expected:
                        raise DownloadError(
                            f"Segment {i} : réponse HTTP {r.status_code}")
                    with open(part, "ab" if self._ranged else "wb") as f:
                        for chunk in r.iter_content(CHUNK):
                            if self._stop.is_set():
                                return
                            if self.limiter:
                                self.limiter.wait(len(chunk), self._stop)
                                if self._stop.is_set():
                                    return
                            f.write(chunk)
                            got += len(chunk)
                            with self._lock:
                                self.downloaded += len(chunk)
                if not self._ranged:
                    return
                if os.path.getsize(part) <= done:
                    raise OSError("connexion fermée sans données")
            except (_Retry, OSError) as e:
                if not self._ranged:
                    with self._lock:
                        self.downloaded -= got
                else:
                    cur = os.path.getsize(part) if os.path.exists(part) else 0
                    if cur > done:
                        attempts = 0   # on a avancé : on repart à zéro
                attempts += 1
                if attempts > MAX_RETRIES:
                    raise DownloadError(
                        f"Segment {i} : abandon après {MAX_RETRIES} essais ({e})")
                wait = getattr(e, "wait", None) or min(2 ** attempts, 30)
                if self._stop.wait(wait):   # attend, mais réagit à la pause
                    return

    # ---------- fusion + vérification ----------
    def _merge(self, segments):
        self.status = "merging"
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

        os.replace(tmp, self.dest)
        remove_parts(self.dest)
        clear_state(self.dest)
        self.sha256 = digest

    # ---------- point d'entrée ----------
    def run(self):
        self._stop.clear()
        self._errors = []
        self.error = None
        self.status = "downloading"
        try:
            self._run()
        except Exception as e:
            self.status = "error"
            self.error = str(e)
            raise

    def _run(self):
        size, ranges_ok = probe(self.url, headers=self.headers)
        self.total = size or 0
        segments = self._prepare_segments(size, ranges_ok)
        self.downloaded = self._existing_bytes(segments)

        threads = [threading.Thread(target=self._worker, args=(s,), daemon=True)
                   for s in segments]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if self._errors:
            raise DownloadError(str(self._errors[0]))

        if not self._is_complete(segments):
            self.status = "paused"
            return

        self._merge(segments)
        self.status = "done"