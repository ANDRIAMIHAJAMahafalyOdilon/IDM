"""Focused regressions for progress UI and non-blocking metadata preflight."""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtCore import QItemSelection, QItemSelectionModel
from PyQt5.QtWidgets import (QApplication, QShortcut, QTableWidget,
                             QTableWidgetItem)
from PyQt5.QtGui import QKeySequence

from core.database import Database
from core.downloader import Downloader
from core.queue_manager import QueueManager
from gui.dialogs import DownloadInfoDialog, DownloadProgressDialog
from gui.main_window import MainWindow


class ProgressUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "downloads.db"))
        self.manager = QueueManager(self.db, self.tmp.name, max_parallel=1, n_threads=2)

    def tearDown(self):
        self.manager.shutdown()
        self.db.close()
        self.tmp.cleanup()

    def test_metadata_result_preserves_manual_filename(self):
        prepared = {"initial_url": "https://example.test/download?id=1",
                    "url": "https://example.test/download?id=1",
                    "name": "download", "size": None, "mime_type": ""}
        dialog = DownloadInfoDialog(prepared, self.db, self.tmp.name)
        dialog._name_touched = True
        dialog.save_as.setText(os.path.join(self.tmp.name, "my-name.zip"))
        dialog.apply_metadata({"name": "server-name.7z", "size": 1024,
                               "mime_type": "application/x-7z-compressed"})
        self.assertEqual(os.path.basename(dialog.save_as.text()), "my-name.zip")
        self.assertEqual(prepared["name"], "server-name.7z")

    def test_closing_progress_window_hides_without_stopping_transfer(self):
        prepared = {"initial_url": "https://example.test/archive.zip",
                    "url": "https://example.test/archive.zip", "name": "archive.zip",
                    "headers": {}, "size": None}
        item_id = self.manager.add_prepared(prepared, path=os.path.join(self.tmp.name, "archive.zip"),
                                            deferred=True)
        dialog = DownloadProgressDialog(item_id, self.manager, self.db)
        dialog.show()
        dialog.close()
        self.assertFalse(dialog.isVisible())
        self.assertEqual(self.manager.items[item_id].state, "deferred")
        dialog.timer.stop()

    def test_completion_reuses_the_same_window_instance_and_shows_actions(self):
        target = os.path.join(self.tmp.name, "ready.zip")
        prepared = {"initial_url": "https://example.test/ready.zip",
                    "url": "https://example.test/ready.zip", "name": "ready.zip",
                    "headers": {}, "size": 5}
        item_id = self.manager.add_prepared(prepared, path=target, deferred=True,
                                            category="Archives")
        item = self.manager.items[item_id]
        dialog = DownloadProgressDialog(item_id, self.manager, self.db)
        self.assertIsNone(dialog.parent())
        self.assertTrue(dialog.windowFlags() & Qt.Window)
        with open(target, "wb") as stream:
            stream.write(b"ready")
        item.dl.total = item.dl.downloaded = 5
        item.dl.status = "done"
        item.state = "done"
        self.db.update(item_id, status="done", size=5, downloaded=5)
        dialog.refresh()
        self.assertIs(dialog.stack.currentWidget(), dialog.completed_page)
        self.assertEqual(dialog._view_state, "completed")
        self.assertEqual(dialog.final_name.text(), "ready.zip")
        self.assertEqual(dialog.final_category.text(), "Archives")
        self.assertTrue(dialog.open_file_btn.isEnabled())
        with patch("gui.dialogs.QDesktopServices.openUrl", return_value=True) as opener:
            dialog.open_file_btn.click()
            self.assertTrue(opener.called)
            dialog.open_folder_btn.click()
            self.assertEqual(opener.call_count, 2)
        dialog.timer.stop()
        self.assertIs(dialog.stack.currentWidget(), dialog.completed_page)

    def test_cancelled_item_never_enters_success_view(self):
        target = os.path.join(self.tmp.name, "cancelled.bin")
        prepared = {"initial_url": "https://example.test/cancelled.bin",
                    "url": "https://example.test/cancelled.bin", "name": "cancelled.bin",
                    "headers": {}}
        item_id = self.manager.add_prepared(prepared, path=target, deferred=True)
        item = self.manager.items[item_id]
        item.state = "cancelled"
        self.db.update(item_id, status="cancelled")
        dialog = DownloadProgressDialog(item_id, self.manager, self.db)
        self.assertIs(dialog.stack.currentWidget(), dialog.failed_page)
        self.assertEqual(dialog._view_state, "cancelled")
        dialog.timer.stop()

    def test_each_download_id_has_its_own_top_level_window(self):
        dialogs = []
        targets = []
        for name in ("one.bin", "two.bin"):
            url = "https://example.test/" + name
            target = os.path.join(self.tmp.name, name)
            targets.append(target)
            item_id = self.manager.add_prepared(
                {"initial_url": url, "url": url, "name": name, "headers": {}},
                path=target, deferred=True)
            dialogs.append(DownloadProgressDialog(item_id, self.manager, self.db))
        self.assertNotEqual(dialogs[0].item_id, dialogs[1].item_id)
        self.assertIsNot(dialogs[0], dialogs[1])
        self.assertIsNone(dialogs[0].parent())
        self.assertIsNone(dialogs[1].parent())
        first_item = self.manager.items[dialogs[0].item_id]
        with open(targets[0], "wb") as stream:
            stream.write(b"first")
        first_item.dl.total = first_item.dl.downloaded = 5
        first_item.dl.status = "done"
        first_item.state = "done"
        self.db.update(first_item.id, status="done", size=5, downloaded=5)
        dialogs[0].refresh()
        self.assertIs(dialogs[0].stack.currentWidget(), dialogs[0].completed_page)
        self.assertIs(dialogs[1].stack.currentWidget(), dialogs[1].progress_page)
        for dialog in dialogs:
            dialog.timer.stop()

    def test_range_segment_snapshot_reads_real_partial_bytes(self):
        target = os.path.join(self.tmp.name, "large.bin")
        downloader = Downloader("https://example.test/large.bin", target, n_threads=2)
        downloader.status = "downloading"
        downloader.total = 100
        downloader._ranged = True
        downloader.segments = [{"i": 0, "start": 0, "end": 49},
                               {"i": 1, "start": 50, "end": 99}]
        with open(target + ".part0", "wb") as part:
            part.write(b"x" * 17)
        segments = downloader.segment_snapshot()
        self.assertEqual([s["downloaded"] for s in segments], [17, 0])
        self.assertEqual([s["state"] for s in segments], ["En attente", "En attente"])

    def test_single_stream_has_one_real_connection_row(self):
        downloader = Downloader("https://example.test/stream", "single.bin", n_threads=4)
        downloader.total = 90
        downloader.downloaded = 22
        downloader._ranged = False
        rows = downloader.segment_snapshot()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["downloaded"], 22)
        self.assertEqual(rows[0]["total"], 90)

    def test_known_size_progress_is_determinate_and_caps_before_finalization(self):
        target = os.path.join(self.tmp.name, "progress.bin")
        prepared = {"initial_url": "https://example.test/progress.bin",
                    "url": "https://example.test/progress.bin", "name": "progress.bin",
                    "headers": {}, "size": 100}
        item_id = self.manager.add_prepared(prepared, path=target, deferred=True)
        item = self.manager.items[item_id]
        item.state = "active"
        item.dl.total = 100
        item.dl.downloaded = 40
        item.dl.status = "downloading"
        dialog = DownloadProgressDialog(item_id, self.manager, self.db)
        try:
            dialog.refresh()
            self.assertEqual(dialog.progress.minimum(), 0)
            self.assertEqual(dialog.progress.maximum(), 100)
            self.assertEqual(dialog.progress.value(), 40)
            self.assertEqual(dialog.progress_percent.text(), "40.00 %")
            item.dl.downloaded = 100
            item.dl.status = "verifying"
            dialog.refresh()
            self.assertEqual(dialog.progress.value(), 99)
            self.assertEqual(dialog.progress_percent.text(), "99.99 %")
        finally:
            dialog.timer.stop()

    def test_unknown_size_shows_indeterminate_label_without_percentage(self):
        prepared = {"initial_url": "https://cdn.example/master.m3u8",
                    "url": "https://cdn.example/master.m3u8", "name": "video.mp4",
                    "headers": {}, "size": None, "stream": True}
        item_id = self.manager.add_prepared(prepared, deferred=True)
        item = self.manager.items[item_id]
        item.state = "active"
        item.dl.downloaded = 304 * 1024 * 1024
        item.dl.total = 0
        dialog = DownloadProgressDialog(item_id, self.manager, self.db)
        try:
            dialog.refresh()
            self.assertEqual(dialog.progress.maximum(), 0)
            self.assertEqual(dialog.progress_percent.text(), "Progression indéterminée")
            self.assertNotIn("%", dialog.got.text())
            self.assertIn("304.00 Mo", dialog.got.text())
        finally:
            dialog.timer.stop()

    def test_estimated_hls_size_is_labeled_and_percentage_is_approximate(self):
        prepared = {"initial_url": "https://cdn.example/master.m3u8",
                    "url": "https://cdn.example/master.m3u8", "name": "video.mp4",
                    "headers": {}, "size": None, "stream": True,
                    "estimated_total": 100}
        item_id = self.manager.add_prepared(prepared, deferred=True)
        item = self.manager.items[item_id]
        item.state = "active"
        item.dl.downloaded = 40
        dialog = DownloadProgressDialog(item_id, self.manager, self.db)
        try:
            dialog.refresh()
            self.assertEqual(item.dl.estimated_total, 100)
            self.assertEqual(item.dl.total, 100)
            self.assertIn("Estimée", dialog.size.text())
            self.assertEqual(dialog.progress_percent.text(), "≈ 40.00 % estimés")
        finally:
            dialog.timer.stop()

    def test_confirmation_returns_while_metadata_worker_is_slow(self):
        db_path = os.path.join(self.tmp.name, "window.db")
        with patch("gui.main_window.Database", side_effect=lambda: Database(db_path)), \
                patch("gui.main_window.start_api", return_value=None):
            window = MainWindow()
        window.qm.prepare = lambda *args, **kwargs: (time.sleep(0.8) or {
            "initial_url": args[0], "url": args[0], "name": "ready.zip",
            "size": 10, "mime_type": "application/zip", "headers": {}})
        prepared = {"initial_url": "https://example.test/ready.zip",
                    "url": "https://example.test/ready.zip", "name": "ready.zip",
                    "headers": {}, "_needs_metadata": True}
        confirmation_parent = []
        def reject_confirmation():
            active = QApplication.activeModalWidget()
            confirmation_parent.append(active.parent())
            active.reject()
        QTimer.singleShot(30, reject_confirmation)
        started = time.monotonic()
        try:
            self.assertIsNone(window._confirm_download(prepared))
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(confirmation_parent, [None])
            for worker in list(window._metadata_workers):
                worker.wait(1500)
        finally:
            window.close()
            window.db.close()
            window.deleteLater()
            QApplication.sendPostedEvents(None, 0)
            QApplication.processEvents()

    def test_extension_format_opens_prefilled_confirmation_without_reextract(self):
        import threading
        from core.extractors import VideoFormat, VideoInfo

        db_path = os.path.join(self.tmp.name, "cached-confirm.db")
        with patch("gui.main_window.Database", side_effect=lambda: Database(db_path)), \
                patch("gui.main_window.start_api", return_value=None):
            window = MainWindow()
        info = VideoInfo(success=True, title="Titre fiable", source_url=
                         "https://www.youtube.com/watch?v=sample", extractor="youtube",
                         formats=[VideoFormat(label="720p", ext="mp4",
                             url="https://cdn.example/720.mp4", format_id="720")])
        received = []
        window._confirm_download = lambda prepared: received.append(prepared) or 17
        completed, result = threading.Event(), {}
        payload = {
            "url": info.source_url, "referer": info.source_url,
            "formatIndex": 0, "_video_info": info,
            "video_title": "Titre fiable", "page_title": "Titre fiable",
            "page_url": info.source_url, "source_site": "youtube.com",
            "format": "mp4", "headers": {"User-Agent": "test"},
        }
        try:
            window._handle_browser_request((payload, completed, result))
            self.assertTrue(completed.is_set())
            self.assertEqual(result, {"ok": True, "id": 17})
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0]["name"], "Titre fiable.mp4")
            self.assertFalse(received[0]["_needs_metadata"])
            self.assertEqual(window._metadata_workers, [])
        finally:
            window.close()
            window.db.close()
            window.deleteLater()
            QApplication.sendPostedEvents(None, 0)
            QApplication.processEvents()

    def test_main_window_reopens_the_existing_progress_instance(self):
        db_path = os.path.join(self.tmp.name, "window-map.db")
        with patch("gui.main_window.Database", side_effect=lambda: Database(db_path)), \
                patch("gui.main_window.start_api", return_value=None):
            window = MainWindow()
        try:
            url = "https://example.test/map.bin"
            item_id = window.qm.add_prepared(
                {"initial_url": url, "url": url, "name": "map.bin", "headers": {}},
                path=os.path.join(self.tmp.name, "map.bin"), deferred=True)
            first = window._show_progress(item_id)
            first.hide()
            second = window._show_progress(item_id)
            self.assertIs(first, second)
            self.assertEqual(len(window._progress_dialogs), 1)
            first.timer.stop()
        finally:
            window.close()
            window.db.close()
            window.deleteLater()
            QApplication.sendPostedEvents(None, 0)
            QApplication.processEvents()

    def test_extended_selection_supports_ctrl_shift_and_select_all(self):
        table = QTableWidget(3, 1)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        for row, value in enumerate(("one", "two", "three")):
            table.setItem(row, 0, QTableWidgetItem(value))
        select_all = QShortcut(QKeySequence("Ctrl+A"), table)
        select_all.setContext(Qt.WidgetWithChildrenShortcut)
        select_all.activated.connect(table.selectAll)
        try:
            model = table.selectionModel()
            row0 = table.model().index(0, 0)
            row1 = table.model().index(1, 0)
            row2 = table.model().index(2, 0)
            selected = lambda: {index.row() for index in model.selectedRows()}
            model.select(row0, QItemSelectionModel.ClearAndSelect |
                         QItemSelectionModel.Rows)
            # Shift-click uses an anchored contiguous selection.
            model.select(QItemSelection(row0, row2),
                         QItemSelectionModel.Select | QItemSelectionModel.Rows)
            self.assertEqual(selected(), {0, 1, 2})
            # Ctrl-click toggles just the clicked row.
            model.select(row1, QItemSelectionModel.Toggle | QItemSelectionModel.Rows)
            self.assertEqual(selected(), {0, 2})
            select_all.activated.emit()
            QApplication.processEvents()
            self.assertEqual(selected(), {0, 1, 2})
        finally:
            table.close()
            table.deleteLater()
            QApplication.sendPostedEvents(None, 0)
            QApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
