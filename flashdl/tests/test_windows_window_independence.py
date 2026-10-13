"""Native Windows regression for independent Qt top-level progress windows."""
import ctypes
import os
import sys
import unittest
from ctypes import wintypes
from types import SimpleNamespace

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

if os.name == "nt":
    from PyQt5.QtWidgets import QApplication, QMainWindow
    from gui.dialogs import DownloadProgressDialog


@unittest.skipUnless(os.name == "nt" and os.environ.get("QT_QPA_PLATFORM") != "offscreen",
                     "requires a native Windows Qt desktop session")
class NativeWindowIndependenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_and_progress_minimize_restore_independently(self):
        class Manager:
            items = {}
            limiter = None

        class Db:
            @staticmethod
            def get(_item_id):
                return {"downloaded": 0, "size": None, "path": "C:/temp/example.bin"}

        manager = Manager()
        item = SimpleNamespace(
            id=11, name="example.bin", category="Général",
            url="https://example.test/example.bin",
            initial_url="https://example.test/example.bin", state="paused",
            dest="C:/temp/example.bin", format_id=None,
            dl=SimpleNamespace(status="paused", total=0, downloaded=0, error=None,
                               segment_snapshot=lambda: [{"i": 0, "downloaded": 0,
                                                         "total": None, "state": "En pause"}]))
        manager.items = {item.id: item}
        main = QMainWindow()
        progress = DownloadProgressDialog(item.id, manager, Db())
        user32 = ctypes.windll.user32

        try:
            main.show()
            progress.show()
            self.app.processEvents()
            main_hwnd = wintypes.HWND(int(main.winId()))
            progress_hwnd = wintypes.HWND(int(progress.winId()))
            self.assertFalse(user32.GetWindow(progress_hwnd, 4),
                             "progress window must have no Win32 owner")

            user32.ShowWindow(main_hwnd, 6)  # SW_MINIMIZE
            self.app.processEvents()
            self.assertTrue(user32.IsIconic(main_hwnd))
            self.assertFalse(user32.IsIconic(progress_hwnd))

            user32.ShowWindow(progress_hwnd, 6)
            self.app.processEvents()
            self.assertTrue(user32.IsIconic(main_hwnd))
            self.assertTrue(user32.IsIconic(progress_hwnd))

            user32.ShowWindow(main_hwnd, 9)  # SW_RESTORE
            self.app.processEvents()
            self.assertFalse(user32.IsIconic(main_hwnd))
            self.assertTrue(user32.IsIconic(progress_hwnd))
        finally:
            progress.timer.stop()
            progress.close()
            main.close()


if __name__ == "__main__":
    unittest.main()
