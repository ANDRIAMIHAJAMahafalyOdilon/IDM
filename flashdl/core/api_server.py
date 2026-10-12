import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

from .config import get_headers_for_url
from .extractors import (VideoFormat, _expand_hls_master, extract,
                         is_extractable_url)

PORT = 9614


def _clean_name(name):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")
    return name[:150] or None


def start_api(qm, port=PORT):
    """Démarre le serveur local pour l'extension. Renvoie le serveur (ou None)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/ping":
                self._json(200, {"app": "FlashDL"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            # seules les extensions de navigateur sont acceptées, pas les sites web
            if not self.headers.get("Origin", "").startswith("chrome-extension://"):
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

                if self.path == "/extract":
                    info = extract(url, extra_headers=headers)
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
        return None     # port déjà pris (FlashDL déjà ouvert ?)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
