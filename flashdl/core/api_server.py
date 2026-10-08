import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
            if self.path != "/add":
                self._json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length))
                url = data["url"]
                if not url.lower().startswith(("http://", "https://")):
                    raise ValueError("lien invalide")
                headers = {}
                if data.get("referer"):
                    headers["Referer"] = data["referer"]
                if data.get("cookies"):
                    headers["Cookie"] = data["cookies"]
                if data.get("userAgent"):
                    headers["User-Agent"] = data["userAgent"]
                item_id = qm.add(url, headers=headers,
                                 filename=_clean_name(data.get("filename")))
                self._json(200, {"ok": True, "id": item_id})
            except Exception as e:
                self._json(400, {"error": str(e)})

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        return None     # port déjà pris (FlashDL déjà ouvert ?)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server