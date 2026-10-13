"""Controlled local comparison of one versus four HTTP range workers.

This is a diagnostic benchmark, not a prediction of public Internet throughput.
The local server throttles each connection independently at about 2 MiB/s.
"""
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from core.downloader import Downloader


SIZE = 8 * 1024 * 1024
CHUNK = 256 * 1024
PAYLOAD = b"R" * SIZE


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        value = self.headers.get("Range", "")
        if not value.startswith("bytes="):
            self.send_error(400)
            return
        first, last = value[6:].split("-", 1)
        start, end = int(first), min(int(last), SIZE - 1)
        if start < 0 or end < start:
            self.send_error(416)
            return
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{SIZE}")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        while start <= end:
            stop = min(start + CHUNK, end + 1)
            try:
                self.wfile.write(PAYLOAD[start:stop])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            start = stop
            time.sleep(0.125)  # 2 MiB/s per connection; parallelism is measurable.


def run_case(url, directory, workers):
    target = os.path.join(directory, f"download-{workers}.bin")
    engine = Downloader(url, target, n_threads=workers)
    started = time.perf_counter()
    engine.run()
    elapsed = time.perf_counter() - started
    if engine.status != "done" or os.path.getsize(target) != SIZE:
        raise RuntimeError(f"transfer with {workers} worker(s) did not complete")
    return elapsed, engine.metrics_snapshot()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/payload"
    try:
        with tempfile.TemporaryDirectory() as directory:
            for workers in (1, 4):
                elapsed, metrics = run_case(url, directory, workers)
                print(f"workers={workers} bytes={metrics['network_bytes']} "
                      f"elapsed={elapsed:.3f}s "
                      f"average={metrics['network_bytes'] / elapsed / 1024 / 1024:.2f} MiB/s "
                      f"server_wait={metrics['server_wait_seconds']:.3f}s "
                      f"disk_write={metrics['disk_write_seconds']:.3f}s")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
