import json
import hashlib
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

from .config import get_headers_for_url
from .extractors import (VideoFormat, _expand_hls_master, extract,
                         is_extractable_url)

PORT = 9615
APP_VERSION = "1.2.0"


def _clean_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")
    return name[:150] or None


def start_api(qm, port=PORT, on_download_request=None):
    """Démarre le serveur local pour l'extension. Renvoie le serveur (ou None)."""
    extracted_cache = {}
    extracted_cache_lock = threading.Lock()

    def cache_key(url, headers):
        material = "\n".join((url, headers.get("Referer", ""),
                              headers.get("Cookie", ""), headers.get("User-Agent", "")))
        return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()

    def cached_info(key):
        now = time.monotonic()
        with extracted_cache_lock:
            for old_key, (expires, _) in list(extracted_cache.items()):
                if expires <= now:
                    extracted_cache.pop(old_key, None)
            entry = extracted_cache.get(key)
            return entry[1] if entry and entry[0] > now else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            origin = self.headers.get("Origin", "")
            if re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/ping":
                self._json(200, {"app": "Roch Xd", "version": APP_VERSION})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            # seules les extensions de navigateur sont acceptées, pas les sites web
            origin = self.headers.get("Origin", "")
            if not re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
                self._json(403, {"error": "origine refusée"})
                return
            if self.path not in ("/add", "/inspect", "/extract"):
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length))
                url = data["url"]
                if not url.lower().startswith(("http://", "https://")):
                    raise ValueError("lien invalide")

                # Headers de base selon le site, puis on écrase avec ceux du navigateur
                headers = get_headers_for_url(url)
                if data.get("referer"):
                    headers["Referer"] = data.get("referer")
                if data.get("cookies"):
                    headers["Cookie"] = data.get("cookies")
                elif urlparse(url).hostname and (
                        urlparse(url).hostname.lower().endswith("youtube.com") or
                        urlparse(url).hostname.lower() == "youtu.be"):
                    headers.pop("Cookie", None)
                if data.get("userAgent"):
                    headers["User-Agent"] = data.get("userAgent")
                if self.path == "/add" and on_download_request:
                    request_data = {**data, "headers": headers}
                    if data.get("formatIndex") is not None:
                        request_data["_video_info"] = cached_info(
                            cache_key(url, headers))
                    result = on_download_request(request_data)
                    self._json(200, result)
                    return

                if self.path == "/extract":
                    info = extract(url, extra_headers=headers)
                    if info.success and info.formats:
                        with extracted_cache_lock:
                            extracted_cache[cache_key(url, headers)] = (
                                time.monotonic() + 30.0, info)
                            while len(extracted_cache) > 32:
                                extracted_cache.pop(next(iter(extracted_cache)))
                    self._json(200, {
                        "ok": info.success,
                        "title": info.title,
                        "thumbnail": info.thumbnail,
                        "extractor": info.extractor,
                        "error": info.error,
                        "formats": [{
                            "index": index,
                            "label": fmt.label,
                            "ext": fmt.ext,
                            "size": fmt.size,
                            "hasAudio": fmt.has_audio,
                            "bitrateKbps": fmt.bitrate_kbps,
                        } for index, fmt in enumerate(info.formats)],
                    })
                    return

                if self.path == "/inspect":
                    if not urlparse(url).path.lower().endswith(".m3u8"):
                        size = None
                        try:
                            response = requests.head(
                                url, headers=headers, timeout=8,
                                allow_redirects=True)
                            try:
                                if not response.headers.get("Content-Type", "").lower().startswith("text/html"):
                                    size = int(response.headers.get("Content-Length") or 0) or None
                                    content_range = response.headers.get("Content-Range", "")
                                    if "/" in content_range:
                                        size = int(content_range.rsplit("/", 1)[1]) or size
                            finally:
                                response.close()
                            if not size:
                                response = requests.get(
                                    url, headers={**headers, "Range": "bytes=0-0"},
                                    stream=True, timeout=8, allow_redirects=True)
                                try:
                                    content_range = response.headers.get("Content-Range", "")
                                    if "/" in content_range:
                                        size = int(content_range.rsplit("/", 1)[1]) or None
                                    elif response.status_code == 200:
                                        size = int(response.headers.get("Content-Length") or 0) or None
                                finally:
                                    response.close()
                        except (requests.RequestException, ValueError):
                            size = None
                        self._json(200, {"ok": True, "size": size})
                        return
                    source = VideoFormat(
                        label="HLS", ext="mp4", url=url, is_hls=True,
                        extra_headers=headers,
                    )
                    formats = _expand_hls_master([source])
                    self._json(200, {
                        "ok": True,
                        "variants_found": any(fmt.url != url for fmt in formats),
                        "formats": [{
                            "label": fmt.label,
                            "url": fmt.url,
                            "size": fmt.size,
                            "bitrateKbps": fmt.bitrate_kbps,
                        } for fmt in formats],
                    })
                    return

                if data.get("formatIndex") is not None or is_extractable_url(url):
                    ids, info = qm.add_video(
                        url, format_index=data.get("formatIndex"),
                        extra_headers=headers)
                    if not ids:
                        raise ValueError(info.error or "Aucun format vidéo disponible")
                    item_id = ids[0]
                else:
                    result = qm.add(url, headers=headers,
                                    filename=_clean_name(data.get("filename")),
                                    stream=bool(data.get("stream")),
                                    source_url=data.get("referer"))
                    item_id = result[0] if isinstance(result, tuple) else result
                self._json(200, {"ok": True, "id": item_id})
            except Exception as e:
                self._json(400, {"error": str(e)})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return None     # port déjà pris (Roch Xd déjà ouvert ?)
    server.daemon_threads = True
    server.block_on_close = False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
