"""Isolated regression tests for the real download preparation/persistence path."""
import os
import sys
import tempfile
import time
import unittest
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from PyQt5.QtWidgets import QApplication

from core.database import Database
from core.downloader import Downloader
from core.hls import HlsDownloader
from core.metadata import browser_filename_info, clean_page_title, probe_download
from core.queue_manager import QueueManager
from gui.dialogs import DownloadInfoDialog
from gui.main_window import MainWindow


class MetadataTests(unittest.TestCase):
    def test_content_disposition_wins_and_query_is_not_in_filename(self):
        import requests

        response = requests.Response()
        response.status_code = 200
        response.raw = io.BytesIO()
        response.url = "https://cdn.example/fallback.mp4?token=secret"
        response.headers.update({
            "Content-Type": "application/pdf",
            "Content-Length": "1234",
            "Accept-Ranges": "bytes",
            "Content-Disposition": "attachment; filename*=UTF-8''R%C3%A9sum%C3%A9.pdf",
        })
        with patch("core.metadata.requests.head", return_value=response):
            info = probe_download("https://example.test/download?id=123")
        self.assertEqual(info.name, "Résumé.pdf")
        self.assertEqual(info.size, 1234)
        self.assertEqual(info.mime_type, "application/pdf")
        self.assertTrue(info.supports_range)
        response.close()


class DownloaderFallbackTests(unittest.TestCase):
    def test_server_that_ignores_segment_ranges_restarts_as_one_stream(self):
        payload = bytes(range(256)) * (4 * 1024 * 4)  # 4 MiB, forces segmentation

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requested_range = self.headers.get("Range", "")
                if requested_range == "bytes=0-0":
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes 0-0/{len(payload)}")
                    self.send_header("Content-Length", "1")
                    self.end_headers()
                    self.wfile.write(payload[:1])
                    return
                # Simulate a CDN that claimed range support during the probe,
                # then ignores every worker range request and returns the full file.
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "fallback.bin")
            downloader = Downloader(
                f"http://127.0.0.1:{server.server_port}/file", target, n_threads=4)
            try:
                downloader.run()
                self.assertEqual(downloader.status, "done")
                self.assertTrue(downloader._force_single)
                with open(target, "rb") as stream:
                    self.assertEqual(stream.read(), payload)
                self.assertEqual(downloader.metrics_snapshot()["network_bytes"], len(payload))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class MetadataFilenameTests(unittest.TestCase):
    def test_animesama_page_suffix_is_removed_from_hls_title(self):
        page_title = ("Gensou Suikoden - Saison 1 _ Anime-Sama - Streaming et "
                      "catalogage d'animes et scans")
        self.assertEqual(clean_page_title(page_title, "anime-sama.si"),
                         "Gensou Suikoden - Saison 1")

    def test_hls_manifest_name_becomes_page_title_mp4(self):
        info = browser_filename_info({
            "url": "https://cdn.example/master.m3u8?token=abc",
            "original_filename": "master.m3u8",
            "page_title": ("Gensou Suikoden - Saison 1 _ Anime-Sama - Streaming et "
                           "catalogage d'animes et scans"),
            "source_site": "anime-sama.si",
            "stream": True,
        })
        self.assertEqual(info["name"], "Gensou Suikoden - Saison 1.mp4")

    def test_hls_name_uses_explicit_video_title_over_manifest_name(self):
        info = browser_filename_info({
            "url": "https://cdn.example/index.m3u8",
            "original_filename": "index.m3u8",
            "suggested_filename": "BLEACH Rebirth of Souls - PS5 4K 60FPS Gameplay",
            "format": "mp4",
        })
        self.assertEqual(info["name"],
                         "BLEACH Rebirth of Souls - PS5 4K 60FPS Gameplay.mp4")

    def test_generic_server_name_does_not_hide_available_page_title(self):
        info = browser_filename_info({
            "url": "https://cdn.example/master.m3u8",
            "suggested_filename": "master.mp4",
            "video_title": "index-v1-a1.m3u8",
            "page_title": "Nom de la série - Saison 1 - Anime-Sama",
            "source_site": "anime-sama.si",
        })
        self.assertEqual(info["name"], "Nom de la série - Saison 1.mp4")

    def test_browser_confirmation_receives_animesama_title_and_hls_estimate(self):
        captured = {}

        class BridgeHarness:
            _handle_browser_request = MainWindow._handle_browser_request

            def _confirm_download(self, prepared):
                captured.update(prepared)
                return 17

        completed = __import__("threading").Event()
        result = {}
        BridgeHarness()._handle_browser_request(({
            "url": "https://cdn.example/master.m3u8?token=x",
            "filename": "master.m3u8",
            "original_filename": "master.m3u8",
            "page_url": "https://anime-sama.example/catalogue/episode",
            "page_title": "Gensou Suikoden - Saison 1 _ Anime-Sama - Streaming et catalogage",
            "source_site": "anime-sama.example",
            "format": "mp4", "stream": True,
            "bitrate_kbps": 1800, "duration_seconds": 2400,
            "media_size": 540_000_000, "size_estimated": True,
        }, completed, result))

        self.assertTrue(completed.is_set())
        self.assertEqual(result, {"ok": True, "id": 17})
        self.assertEqual(captured["name"], "Gensou Suikoden - Saison 1.mp4")
        self.assertEqual(captured["estimated_total"], 540_000_000)
        self.assertEqual(captured["page_url"], "https://anime-sama.example/catalogue/episode")

    def test_frenchstream_title_reaches_confirmation_before_transfer(self):
        captured = {}

        class BridgeHarness:
            _handle_browser_request = MainWindow._handle_browser_request

            def _confirm_download(self, prepared):
                captured.update(prepared)
                return 18

        completed = __import__("threading").Event()
        result = {}
        BridgeHarness()._handle_browser_request(({
            "url": "https://cdn.example/index.m3u8",
            "filename": "index.m3u8",
            "page_url": "https://frenchstream.example/film",
            "page_title": "Nom du film (2025) - French Stream",
            "source_site": "frenchstream.example",
            "stream": True, "format": "mp4",
            "bitrate_kbps": 1500, "duration_seconds": 7200,
        }, completed, result))

        self.assertTrue(completed.is_set())
        self.assertEqual(result, {"ok": True, "id": 18})
        self.assertEqual(captured["name"], "Nom du film (2025).mp4")
        self.assertEqual(captured["estimated_total"], 1_350_000_000)


class HlsProgressTests(unittest.TestCase):
    def test_master_bandwidth_and_extinf_produce_explicit_estimate(self):
        from unittest.mock import patch

        class PlaylistResponse:
            def __init__(self, text):
                self.text = text

        master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2000000,AVERAGE-BANDWIDTH=1800000
variant.m3u8
"""
        media = """#EXTM3U
#EXTINF:10.0,
segment-1.ts
#EXTINF:20.0,
segment-2.ts
#EXT-X-ENDLIST
"""
        with tempfile.TemporaryDirectory() as directory:
            engine = HlsDownloader("https://cdn.example/master.m3u8",
                                   os.path.join(directory, "video.mp4"), n_threads=1)
            with patch("core.hls._get", side_effect=[PlaylistResponse(master),
                                                       PlaylistResponse(media)]):
                items, estimate = engine._resolve()
            self.assertEqual(len(items), 2)
            self.assertEqual(estimate, int(1_800_000 * 30 / 8))
            engine._n = len(items)
            engine.estimated_total = estimate
            engine._count = 0
            engine._update_total()
            self.assertEqual(engine.total, estimate)
            self.assertTrue(engine.total_is_estimated)

    def test_hls_total_is_exact_only_after_every_segment_is_present(self):
        with tempfile.TemporaryDirectory() as directory:
            dl = HlsDownloader("https://example.test/list.m3u8",
                               os.path.join(directory, "video.mp4"), n_threads=1)
            os.makedirs(dl.dest + ".hls")
            dl._n = 2
            dl._count = 1
            with open(dl._seg_path(0), "wb") as stream:
                stream.write(b"first")
            dl._update_total()
            self.assertEqual(dl.total, 0)
            with open(dl._seg_path(1), "wb") as stream:
                stream.write(b"second")
            dl._count = 2
            dl.estimated_total = 1000
            dl.total = 1000
            dl.total_is_estimated = True
            dl._update_total()
            self.assertEqual(dl.total, len(b"firstsecond"))
            self.assertFalse(dl.total_is_estimated)

    def test_downloaded_bytes_update_for_each_received_chunk(self):
        import threading
        from unittest.mock import patch

        first_chunk_read = threading.Event()
        release_stream = threading.Event()

        class Response:
            status_code = 200

            def iter_content(self, chunk_size):
                yield b"first"
                first_chunk_read.set()
                release_stream.wait(2)
                yield b"second"

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            dl = HlsDownloader("https://example.test/list.m3u8",
                               os.path.join(directory, "video.mp4"), n_threads=1)
            os.makedirs(dl.dest + ".hls")
            with patch("core.hls.http_get", return_value=Response()):
                worker = threading.Thread(target=dl._fetch, args=(0, "https://cdn.test/0.ts"))
                worker.start()
                self.assertTrue(first_chunk_read.wait(2))
                self.assertEqual(dl.downloaded, len(b"first"))
                release_stream.set()
                worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(dl.downloaded, len(b"firstsecond"))
    def test_url_filename_drops_query_and_fragment(self):
        import requests

        response = requests.Response()
        response.status_code = 200
        response.raw = io.BytesIO()
        response.url = "https://cdn.example/demo%20video.mp4?token=secret#part"
        response.headers.update({"Content-Length": "5", "Accept-Ranges": "bytes"})
        with patch("core.metadata.requests.head", return_value=response):
            info = probe_download("https://example.test/start")
        self.assertEqual(info.name, "demo video.mp4")
        response.close()

    def test_mime_type_supplies_a_cautious_extension_when_url_has_none(self):
        import requests

        response = requests.Response()
        response.status_code = 200
        response.raw = io.BytesIO()
        response.url = "https://cdn.example/resource?key=abc"
        response.headers.update({"Content-Length": "12", "Accept-Ranges": "bytes",
                                 "Content-Type": "image/jpeg"})
        with patch("core.metadata.requests.head", return_value=response):
            info = probe_download("https://example.test/start")
        self.assertEqual(info.name, "resource.jpg")
        response.close()


class DownloadWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "downloads.db"))
        self.output = os.path.join(self.tmp.name, "out")
        os.makedirs(self.output)
        self.manager = QueueManager(self.db, self.output, max_parallel=1, n_threads=1)

    def tearDown(self):
        self.manager.shutdown()
        self.db.close()
        self.tmp.cleanup()

    def prepared(self, name="rapport.pdf"):
        return {
            "initial_url": "https://example.test/fichier?id=3",
            "url": "https://cdn.example/fichier?id=3",
            "name": name,
            "size": 20,
            "mime_type": "application/pdf",
            "extension": ".pdf",
            "headers": {},
            "supports_range": True,
            "etag": '"v1"',
            "last_modified": "",
            "format_id": None,
        }

    def test_confirmation_dialog_cancel_does_not_create_download(self):
        dialog = DownloadInfoDialog(self.prepared(), self.db, self.output)
        self.assertTrue(dialog.isModal())
        self.assertEqual(self.db.history(), [])
        dialog.reject()
        self.assertEqual(self.db.history(), [])

    def test_main_window_builds_and_refreshes_without_network_services(self):
        with patch("gui.main_window.Database",
                   side_effect=lambda: Database(os.path.join(self.tmp.name, "ui.db"))), \
                patch("gui.main_window.start_api", return_value=None):
            window = MainWindow()
        try:
            self.assertEqual(window.table.columnCount(), 10)
            self.assertEqual(window.table.horizontalHeaderItem(5).text(), "Vitesse")
            self.assertEqual(window.table.horizontalHeaderItem(7).text(), "État")
            window.refresh()
            self.assertEqual(window.table.rowCount(), 0)
        finally:
            window.close()
            window.db.close()
            window.deleteLater()
            QApplication.sendPostedEvents(None, 0)
            QApplication.processEvents()

    def test_custom_path_and_description_are_persisted_and_survive_restart(self):
        target = os.path.join(self.output, "custom", "mon rapport.pdf")
        item_id = self.manager.add_prepared(
            self.prepared(), filename="mon rapport.pdf", path=target,
            category="Documents", description="Version signée", deferred=True)
        row = self.db.get(item_id)
        self.assertEqual(row["name"], "mon rapport.pdf")
        self.assertEqual(row["path"], target)
        self.assertEqual(row["description"], "Version signée")
        self.assertEqual(row["status"], "deferred")
        self.assertTrue(os.path.isdir(os.path.dirname(target)))
        remembered = os.path.join(self.tmp.name, "category-folder")
        self.db.save_category("Compressé", remembered, remember_path=True)

        self.manager.shutdown()
        self.db.close()
        self.db = Database(os.path.join(self.tmp.name, "downloads.db"))
        self.manager = QueueManager(self.db, self.output, max_parallel=1, n_threads=1)
        self.manager.load_unfinished()
        self.assertEqual(self.db.get(item_id)["status"], "deferred")
        self.assertEqual(self.manager.snapshot()[0]["description"], "Version signée")
        category = next(c for c in self.db.categories() if c["name"] == "Compressé")
        self.assertEqual(category["folder"], remembered)
        self.assertTrue(category["remember_path"])

    def test_scheduled_download_keeps_its_start_time_after_restart(self):
        scheduled_at = time.time() + 3600
        item_id = self.manager.add_prepared(self.prepared(), start_at=scheduled_at)
        self.assertEqual(self.db.get(item_id)["status"], "scheduled")
        self.assertAlmostEqual(self.db.get(item_id)["scheduled_at"], scheduled_at, places=2)
        self.manager.shutdown()
        self.db.close()
        self.db = Database(os.path.join(self.tmp.name, "downloads.db"))
        self.manager = QueueManager(self.db, self.output, max_parallel=1, n_threads=1)
        self.manager.load_unfinished()
        self.assertEqual(self.manager.items[item_id].state, "scheduled")
        self.assertAlmostEqual(self.manager.items[item_id].start_at, scheduled_at, places=2)

    def test_existing_destination_is_never_overwritten_silently(self):
        target = os.path.join(self.output, "rapport.pdf")
        with open(target, "wb") as stream:
            stream.write(b"keep")
        with self.assertRaises(FileExistsError):
            self.manager.add_prepared(self.prepared(), path=target)
        with open(target, "rb") as stream:
            self.assertEqual(stream.read(), b"keep")
        self.assertEqual(self.db.history(), [])

    def test_remove_history_entry_keeps_the_downloaded_file(self):
        item_id = self.manager.add_prepared(self.prepared())
        item = self.manager.items[item_id]
        with open(item.dest, "wb") as stream:
            stream.write(b"file remains")
        self.manager.remove_entry(item_id)
        self.assertTrue(os.path.isfile(item.dest))
        self.assertIsNone(self.db.get(item_id))

    def test_explicit_partial_delete_removes_hls_and_http_sidecars(self):
        item_id = self.manager.add_prepared(self.prepared(), deferred=True)
        item = self.manager.items[item_id]
        with open(item.dest + ".flashdl", "w", encoding="utf-8") as stream:
            stream.write("state")
        with open(item.dest + ".part.mp4", "wb") as stream:
            stream.write(b"partial")
        os.makedirs(item.dest + ".hls")
        with open(os.path.join(item.dest + ".hls", "chunk.seg"), "wb") as stream:
            stream.write(b"chunk")
        self.manager.remove_entry(item_id, delete_partial=True)
        self.assertFalse(os.path.exists(item.dest + ".flashdl"))
        self.assertFalse(os.path.exists(item.dest + ".part.mp4"))
        self.assertFalse(os.path.exists(item.dest + ".hls"))

    def test_partial_cleanup_succeeds_if_destination_folder_was_removed(self):
        import shutil

        item_id = self.manager.add_prepared(self.prepared(), deferred=True)
        item = self.manager.items[item_id]
        missing_folder = os.path.dirname(item.dest)
        shutil.rmtree(missing_folder)
        self.manager.remove_entry(item_id, delete_partial=True)
        self.assertIsNone(self.db.get(item_id))

    def test_move_rename_updates_disk_and_database(self):
        item_id = self.manager.add_prepared(self.prepared(), deferred=True)
        item = self.manager.items[item_id]
        with open(item.dest, "wb") as stream:
            stream.write(b"complete")
        target = os.path.join(self.output, "renamed.pdf")
        self.manager.move_rename(item_id, target)
        self.assertFalse(os.path.exists(os.path.join(self.output, "Documents", "rapport.pdf")))
        self.assertTrue(os.path.isfile(target))
        self.assertEqual(self.db.get(item_id)["path"], target)
        self.assertEqual(item.dest, target)

    def test_changed_url_clears_partial_state_before_resume(self):
        item_id = self.manager.add_prepared(self.prepared(), deferred=True)
        item = self.manager.items[item_id]
        with open(item.dest + ".flashdl", "w", encoding="utf-8") as stream:
            stream.write("partial state")
        updated = self.prepared()
        updated.update(url="https://cdn.example/new", etag='"v2"', size=30)
        result = self.manager.update_url(item_id, updated["url"], updated)
        self.assertTrue(result["changed"])
        self.assertFalse(os.path.exists(item.dest + ".flashdl"))
        self.assertEqual(self.db.get(item_id)["status"], "paused")

    def test_http_resume_state_is_discarded_when_validator_changes(self):
        from core.resume import part_path, save_state

        target = os.path.join(self.output, "resume.bin")
        save_state(target, {"url": "https://example.test/file", "total": 32,
                            "segments": [{"i": 0, "start": 0, "end": 31}],
                            "etag": '"old"', "last_modified": ""})
        with open(part_path(target, 0), "wb") as stream:
            stream.write(b"old partial")
        engine = Downloader("https://example.test/file", target, n_threads=1)
        segments = engine._prepare_segments(32, True, etag='"new"')
        self.assertFalse(os.path.exists(part_path(target, 0)))
        self.assertEqual(segments[0]["start"], 0)

    def test_hls_unknown_total_stays_indeterminate(self):
        engine = HlsDownloader("https://example.test/list.m3u8",
                               os.path.join(self.output, "video.mp4"))
        engine.downloaded = 8000
        engine._count = 2
        engine._n = 10
        engine._update_total()
        self.assertEqual(engine.total, 0)

    def test_done_state_requires_an_existing_complete_file(self):
        item_id = self.manager.add_prepared(self.prepared())
        item = self.manager.items[item_id]
        item.state = "active"
        item.dl = _FinishedDownloader("done", None, 20)
        self.manager._refresh()
        self.assertEqual(self.db.get(item_id)["status"], "error")

    def test_done_state_records_actual_file_only_when_size_matches(self):
        item_id = self.manager.add_prepared(self.prepared())
        item = self.manager.items[item_id]
        with open(item.dest, "wb") as stream:
            stream.write(b"x" * 20)
        item.state = "active"
        item.dl = _FinishedDownloader("done", item.dest, 20)
        self.manager._refresh()
        row = self.db.get(item_id)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["size"], 20)
        self.assertEqual(row["downloaded"], 20)
        os.remove(item.dest)
        snapshot = self.manager.snapshot()[0]
        self.assertEqual(snapshot["state"], "error")
        self.assertIn("supprimé", snapshot["error"])
        self.assertEqual(self.db.get(item_id)["status"], "error")

    def test_local_http_range_transfer_really_completes(self):
        payload = bytes(range(256)) * 2048

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"test-v1"')
                self.end_headers()

            def do_GET(self):
                value = self.headers.get("Range", "")
                if value.startswith("bytes="):
                    start, end = value[6:].split("-", 1)
                    start = int(start or 0)
                    end = int(end or len(payload) - 1)
                    part = payload[start:end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range",
                                     f"bytes {start}-{end}/{len(payload)}")
                else:
                    part = payload
                    self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(part)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                self.wfile.write(part)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/payload.bin"
            prepared = {
                "initial_url": url, "url": url, "name": "payload.bin",
                "size": len(payload), "mime_type": "application/octet-stream",
                "headers": {}, "supports_range": True, "etag": '"test-v1"',
            }
            item_id = self.manager.add_prepared(prepared,
                                                path=os.path.join(self.output, "payload.bin"))
            deadline = time.time() + 8
            while time.time() < deadline and self.db.get(item_id)["status"] not in ("done", "error"):
                time.sleep(0.05)
            row = self.db.get(item_id)
            self.assertEqual(row["status"], "done", row.get("error"))
            with open(row["path"], "rb") as stream:
                self.assertEqual(stream.read(), payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_http_transfer_can_pause_and_resume_from_its_partial_data(self):
        payload = bytes(range(256)) * 8192

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"resume-v1"')
                self.end_headers()

            def do_GET(self):
                value = self.headers.get("Range", "")
                start, end = (value[6:].split("-", 1) if value.startswith("bytes=")
                              else ("0", str(len(payload) - 1)))
                start, end = int(start or 0), int(end or len(payload) - 1)
                part = payload[start:end + 1]
                self.send_response(206 if value.startswith("bytes=") else 200)
                if value.startswith("bytes="):
                    self.send_header("Content-Range",
                                     f"bytes {start}-{end}/{len(payload)}")
                self.send_header("Content-Length", str(len(part)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("ETag", '"resume-v1"')
                self.end_headers()
                for offset in range(0, len(part), 64 * 1024):
                    try:
                        self.wfile.write(part[offset:offset + 64 * 1024])
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                        return
                    time.sleep(0.02)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/resume.bin"
            target = os.path.join(self.output, "resume.bin")
            prepared = {"initial_url": url, "url": url, "name": "resume.bin",
                        "size": len(payload), "headers": {}, "supports_range": True,
                        "etag": '"resume-v1"'}
            item_id = self.manager.add_prepared(prepared, path=target)
            deadline = time.time() + 4
            while time.time() < deadline and self.manager.items[item_id].state != "active":
                time.sleep(0.02)
            self.assertEqual(self.manager.items[item_id].state, "active")
            time.sleep(0.14)
            self.manager.pause(item_id)
            deadline = time.time() + 4
            while time.time() < deadline and self.db.get(item_id)["status"] != "paused":
                time.sleep(0.03)
            self.assertEqual(self.db.get(item_id)["status"], "paused")
            self.assertTrue(os.path.isfile(target + ".part0"))
            self.manager.resume(item_id)
            deadline = time.time() + 10
            while time.time() < deadline and self.db.get(item_id)["status"] not in ("done", "error"):
                time.sleep(0.05)
            row = self.db.get(item_id)
            self.assertEqual(row["status"], "done", row.get("error"))
            with open(target, "rb") as stream:
                self.assertEqual(stream.read(), payload)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


class _FinishedDownloader:
    def __init__(self, status, dest, total):
        self.status = status
        self.dest = dest
        self.total = total
        self.downloaded = total
        self.error = None
        self.sha256 = "digest"

    def is_alive(self):
        return False

    def join(self):
        pass


if __name__ == "__main__":
    unittest.main()
