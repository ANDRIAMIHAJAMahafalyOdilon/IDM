import glob
import hashlib
import os
import re
import shutil
import subprocess
import threading
from urllib.parse import urljoin, urlparse

from .downloader import DownloadError, RETRYABLE_ERRORS, TIMEOUT
from .http_client import http_get


def is_hls_url(url):
    return urlparse(url).path.lower().endswith(".m3u8")


def find_ffmpeg():
    """Cherche ffmpeg : variable FFMPEG_PATH, PATH, puis dossier WinGet."""
    p = os.environ.get("FFMPEG_PATH")
    if p and os.path.isfile(p):
        return p
    p = shutil.which("ffmpeg")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [os.path.join(local, "Microsoft", "WinGet", "Links", "ffmpeg.exe")]
    candidates += glob.glob(os.path.join(local, "Microsoft", "WinGet", "Packages",
                                         "Gyan.FFmpeg*", "**", "ffmpeg.exe"),
                            recursive=True)
    return next((c for c in candidates if os.path.isfile(c)), None)


def _get(url, headers):
    try:
        r = http_get(url, headers=headers, timeout=TIMEOUT)
    except Exception as e:
        raise DownloadError(f"Connexion impossible : {e}")
    if r.status_code != 200:
        raise DownloadError(f"Le serveur a répondu HTTP {r.status_code}")
    return r


def _attrs(line):
    out = {}
    for m in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(":", 1)[1]):
        out[m.group(1)] = m.group(2).strip('"')
    return out


def parse_playlist(text, base):
    """Renvoie ('master', [(bande_passante, url)]) ou ('media', segments, init, fini)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines or not lines[0].startswith("#EXTM3U"):
        raise DownloadError("Ce n'est pas une playlist HLS valide")

    variants, segments, init, ended = [], [], None, False
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF"):
            uri = next((x for x in lines[i + 1:] if not x.startswith("#")), None)
            if uri:
                bw = int(_attrs(l).get("BANDWIDTH", 0) or 0)
                variants.append((bw, urljoin(base, uri)))
        elif l.startswith("#EXT-X-KEY"):
            if _attrs(l).get("METHOD", "NONE") != "NONE":
                raise DownloadError("Flux chiffré : non supporté")
        elif l.startswith("#EXT-X-BYTERANGE"):
            raise DownloadError("Flux HLS par plages d'octets : non supporté")
        elif l.startswith("#EXT-X-MAP"):
            init = urljoin(base, _attrs(l)["URI"])
        elif l.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif not l.startswith("#"):
            segments.append(urljoin(base, l))

    if variants:
        return "master", variants
    return "media", segments, init, ended


class HlsDownloader:
    """Même interface que Downloader, pour les flux .m3u8."""

    def __init__(self, url, dest, n_threads=8, expected_sha256=None, limiter=None,
                 headers=None):
        self.url = url
        self.dest = dest
        self.n_threads = n_threads
        self.limiter = limiter
        self.headers = dict(headers or {})
        self.status = "waiting"
        self.error = None
        self.sha256 = None
        self.total = 0
        self.downloaded = 0
        self._count = 0
        self._n = 0
        self._next = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._errors = []
        self._thread = None

    # ---------- contrôle ----------
    def start(self):
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()

    def _thread_main(self):
        try:
            self.run()
        except Exception:
            pass

    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def join(self):
        if self._thread:
            self._thread.join()

    def pause(self):
        self._stop.set()

    # ---------- téléchargement ----------
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

    def _resolve(self):
        url = self.url
        for _ in range(3):
            parsed = parse_playlist(_get(url, self.headers).text, url)
            if parsed[0] == "master":
                url = max(parsed[1])[1]          # meilleure qualité
                continue
            _, segments, init, ended = parsed
            if not ended:
                raise DownloadError("Flux en direct : non supporté")
            if not segments:
                raise DownloadError("Playlist vide")
            return ([init] if init else []) + segments
        raise DownloadError("Playlist HLS invalide")

    def _seg_path(self, i):
        return os.path.join(self.dest + ".hls", f"{i:06d}.seg")

    def _run(self):
        if not find_ffmpeg():
            raise DownloadError(
                "ffmpeg introuvable : installe-le (winget install Gyan.FFmpeg), "
                "puis relance FlashDL.")
        items = self._resolve()
        self._n = len(items)
        os.makedirs(self.dest + ".hls", exist_ok=True)

        self._count = 0
        self.downloaded = 0
        for i in range(self._n):
            p = self._seg_path(i)
            if os.path.exists(p):
                self._count += 1
                self.downloaded += os.path.getsize(p)
        self._update_total()
        self._next = 0

        threads = [threading.Thread(target=self._worker, args=(items,), daemon=True)
                   for _ in range(max(1, self.n_threads))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if self._errors:
            raise DownloadError(str(self._errors[0]))
        if self._stop.is_set() or not all(
                os.path.exists(self._seg_path(i)) for i in range(self._n)):
            self.status = "paused"
            return

        self._merge()
        self.status = "done"

    def _update_total(self):
        if self._count:
            self.total = int(self.downloaded / self._count * self._n)

    def _worker(self, items):
        try:
            while not self._stop.is_set():
                with self._lock:
                    i = self._next
                    self._next += 1
                if i >= self._n:
                    return
                if os.path.exists(self._seg_path(i)):
                    continue
                self._fetch(i, items[i])
        except Exception as e:
            self._errors.append(e)
            self._stop.set()

    def _fetch(self, i, url):
        final = self._seg_path(i)
        tmp = final + ".tmp"
        last = None
        for attempt in range(4):
            if self._stop.is_set():
                return
            got = 0
            try:
                r = http_get(url, headers=self.headers, stream=True,
                             timeout=TIMEOUT)
                try:
                    if r.status_code in (429, 503):
                        raise OSError(f"HTTP {r.status_code}")
                    if r.status_code != 200:
                        raise DownloadError(f"Le serveur a répondu HTTP {r.status_code}")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(64 * 1024):
                            if self._stop.is_set():
                                return
                            if self.limiter:
                                self.limiter.wait(len(chunk), self._stop)
                                if self._stop.is_set():
                                    return
                            f.write(chunk)
                            got += len(chunk)
                finally:
                    try:
                        r.close()
                    except Exception:
                        pass
                os.replace(tmp, final)
                with self._lock:
                    self.downloaded += got
                    self._count += 1
                    self._update_total()
                return
            except (OSError,) + RETRYABLE_ERRORS as e:
                last = e
                if self._stop.wait(min(2 ** attempt, 10)):
                    return
        raise DownloadError(f"Morceau {i} : abandon après 4 essais ({last})")

    # ---------- assemblage ----------
    def _merge(self):
        self.status = "merging"
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise DownloadError("ffmpeg introuvable : les morceaux sont conservés, "
                                "installe ffmpeg puis clique sur Reprendre.")
        folder = self.dest + ".hls"
        joined = self.dest + ".join.ts"
        with open(joined, "wb") as out:
            for i in range(self._n):
                with open(self._seg_path(i), "rb") as p:
                    shutil.copyfileobj(p, out)

        tmp_mp4 = self.dest + ".part.mp4"
        r = subprocess.run(
            [ffmpeg, "-y", "-loglevel", "error", "-i", joined, "-c", "copy", tmp_mp4],
            capture_output=True, text=True,
            creationflags=0x08000000 if os.name == "nt" else 0)
        if r.returncode != 0:
            if os.path.exists(tmp_mp4):
                os.remove(tmp_mp4)
            raise DownloadError("ffmpeg a échoué : " + (r.stderr or "")[-200:])
        os.replace(tmp_mp4, self.dest)
        os.remove(joined)

        h = hashlib.sha256()
        with open(self.dest, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                h.update(block)
        self.sha256 = h.hexdigest()
        self.total = os.path.getsize(self.dest)
        self.downloaded = self.total
        shutil.rmtree(folder, ignore_errors=True)
