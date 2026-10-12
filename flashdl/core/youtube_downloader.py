"""yt-dlp-backed downloader for YouTube adaptive formats."""
import glob
import hashlib
import os
import threading


class YoutubeDownloader:
    def __init__(self, url, dest, format_id, has_audio=True, ext="mp4",
                 headers=None, limiter=None, n_threads=8):
        self.url = url
        self.dest = dest
        self.format_id = str(format_id)
        self.has_audio = bool(has_audio)
        self.ext = ext or "mp4"
        self.headers = dict(headers or {})
        self.limiter = limiter
        self.n_threads = max(1, min(int(n_threads or 1), 8))
        self.status = "waiting"
        self.error = None
        self.sha256 = None
        self.total = 0
        self.downloaded = 0
        self._progress_by_stream = {}
        self._total_by_stream = {}
        self._stop = threading.Event()
        self._thread = None

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

    def _progress(self, data):
        if data.get("status") == "downloading":
            info = data.get("info_dict") or {}
            key = str(info.get("format_id") or data.get("filename") or "video")
            self._progress_by_stream[key] = int(data.get("downloaded_bytes") or 0)
            total = int(data.get("total_bytes") or data.get("total_bytes_estimate") or 0)
            if total:
                self._total_by_stream[key] = total
            self.downloaded = sum(self._progress_by_stream.values())
            self.total = sum(self._total_by_stream.values())
        if self._stop.is_set():
            import yt_dlp
            raise yt_dlp.utils.DownloadError("Téléchargement mis en pause")

    def run(self):
        self._stop.clear()
        self.status = "downloading"
        self.error = None
        self._progress_by_stream.clear()
        self._total_by_stream.clear()
        try:
            import yt_dlp
            from .hls import find_ffmpeg

            base = os.path.splitext(self.dest)[0]
            ffmpeg = find_ffmpeg()
            selector = self.format_id
            if not self.has_audio:
                audio_ext = "m4a" if self.ext == "mp4" else "webm"
                selector += f"+bestaudio[ext={audio_ext}]/bestaudio"
                if not ffmpeg:
                    raise RuntimeError(
                        "FFmpeg est nécessaire pour réunir l’image et le son de cette qualité.")
            options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "js_runtimes": {"node": {}},
                "extractor_args": {
                    "youtube": {"player_client": ["default", "web_embedded"]},
                    "youtubetab": {"skip": ["authcheck"]},
                },
                "format": selector,
                "outtmpl": base + ".%(ext)s",
                "merge_output_format": self.ext,
                "continuedl": True,
                "nopart": False,
                "concurrent_fragment_downloads": self.n_threads,
                "http_chunk_size": 10 * 1024 * 1024,
                "progress_hooks": [self._progress],
            }
            if self.headers:
                # YouTube gets UA/referer, but never a raw browser Cookie
                # header. yt-dlp deprecates this and cookies can trigger the
                # broken tv_downgraded player client.
                youtube_headers = {
                    key: value for key, value in self.headers.items()
                    if key.lower() in ("user-agent", "referer")
                }
                if youtube_headers:
                    options["http_headers"] = youtube_headers
            if ffmpeg:
                options["ffmpeg_location"] = ffmpeg

            with yt_dlp.YoutubeDL(options) as ydl:
                result = ydl.download([self.url])
                if result:
                    raise RuntimeError("yt-dlp n’a pas terminé le téléchargement correctement")

            if self._stop.is_set():
                self.status = "paused"
                return

            candidates = [p for p in glob.glob(base + ".*")
                          if not p.endswith((".part", ".ytdl", ".temp"))]
            if not candidates:
                raise RuntimeError("yt-dlp n’a pas produit de fichier vidéo")
            output = max(candidates, key=os.path.getmtime)
            if os.path.abspath(output) != os.path.abspath(self.dest):
                os.replace(output, self.dest)

            digest = hashlib.sha256()
            with open(self.dest, "rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            self.sha256 = digest.hexdigest()
            self.total = os.path.getsize(self.dest)
            self.downloaded = self.total
            self.status = "done"
        except Exception as exc:
            if self._stop.is_set():
                self.status = "paused"
            else:
                self.status = "error"
                self.error = str(exc)
                raise
