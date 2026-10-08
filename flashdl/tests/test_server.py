import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000
RATE = 500 * 1024              # débit max PAR connexion (octets/s)
FILE_NAME = "test.bin"
ALIASES = {"test.bin", "test2.bin", "film.mp4", "cours.pdf"}
FILE_SIZE = 50 * 1024 * 1024   # 50 Mo
BLOCK = 16 * 1024
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FILE_PATH = os.path.join(DATA_DIR, FILE_NAME)


def ensure_file():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(FILE_PATH) or os.path.getsize(FILE_PATH) != FILE_SIZE:
        print("Création du fichier de test (50 Mo)...")
        with open(FILE_PATH, "wb") as f:
            for _ in range(FILE_SIZE // (1024 * 1024)):
                f.write(os.urandom(1024 * 1024))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.lstrip("/") not in ALIASES:
            self.send_error(404)
            return

        size = os.path.getsize(FILE_PATH)
        start, end = 0, size - 1
        rng = self.headers.get("Range")

        if rng and rng.startswith("bytes="):
            s, _, e = rng[6:].partition("-")
            start = int(s) if s else 0
            end = min(int(e), size - 1) if e else size - 1
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)

        length = end - start + 1
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Type", "application/octet-stream")
        self.end_headers()

        with open(FILE_PATH, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                data = f.read(min(BLOCK, remaining))
                if not data:
                    break
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return
                remaining -= len(data)
                time.sleep(len(data) / RATE)   # c'est ça qui bride


if __name__ == "__main__":
    ensure_file()
    print(f"Serveur prêt : http://127.0.0.1:{PORT}/{FILE_NAME}")
    print("Fichiers disponibles : " + ", ".join(sorted(ALIASES)))
    print(f"Débit limité à {RATE // 1024} Ko/s par connexion. Ctrl+C pour arrêter.")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()