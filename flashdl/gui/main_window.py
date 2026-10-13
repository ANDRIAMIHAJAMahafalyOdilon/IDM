import os
import sys
import time
import math
import threading
import subprocess
import shutil

from PyQt5.QtCore import QObject, QTimer, Qt, QUrl, pyqtSignal, QThread
from PyQt5.QtWidgets import (QAction, QApplication, QDoubleSpinBox, QFileDialog,
                             QComboBox, QHeaderView, QInputDialog, QLabel,
                             QLineEdit, QMainWindow, QMenu, QMessageBox,
                             QPlainTextEdit, QProgressBar, QShortcut, QSpinBox, QTableWidget,
                             QTableWidgetItem, QTabWidget, QToolBar)
from PyQt5.QtGui import QKeySequence
from PyQt5.QtGui import QDesktopServices

from core.api_server import start_api
from core.database import Database
from core.extractors import extract, is_extractable_url
from core.queue_manager import QueueManager
from core.rate_limiter import RateLimiter
from .dialogs import (AddDialog, DownloadInfoDialog, DownloadProgressDialog, MoveRenameDialog,
                      PropertiesDialog, UrlUpdateDialog, VideoFormatDialog)
from core.metadata import (browser_filename_info, clean_filename, clean_page_title,
                           filename_with_extension, is_generic_filename)
from core.downloader import filename_from_url

STATES = {"scheduled": "Programmé", "queued": "En attente", "deferred": "Différé",
          "active": "Téléchargement", "downloading": "Téléchargement",
          "paused": "En pause", "interrupted": "Interrompu", "done": "Terminé",
          "error": "Échec", "replaced": "Remplacé", "cancelled": "Annulé"}
DB_STATES = dict(STATES, cancelled="Annulé")


class RequestBridge(QObject):
    request = pyqtSignal(object)


class MetadataWorker(QThread):
    result = pyqtSignal(object)

    def __init__(self, manager, url, prepared, parent=None):
        super().__init__(parent)
        self.manager, self.url, self.prepared = manager, url, prepared

    def run(self):
        try:
            result = self.manager.prepare(
                self.url, headers=self.prepared.get("headers"),
                filename=self.prepared.get("name"),
                stream=self.prepared.get("stream", False),
                source_url=self.prepared.get("source_url"),
                format_index=self.prepared.get("_format_index"),
                extra_headers=self.prepared.get("headers"))
        except Exception as exc:
            result = {"error": str(exc)}
        self.result.emit(result)


def fmt_size(n):
    if n is None:
        return "Taille inconnue"
    if n == 0:
        return "0 o"
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_speed(bps):
    return f"{bps / 1024 / 1024:.2f} Mo/s" if bps > 0 else ""


def fmt_eta(seconds):
    if seconds is None or seconds < 0 or not math.isfinite(seconds):
        return "—"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600} h {(seconds % 3600) // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Roch Xd")
        self.resize(1100, 600)

        self.db = Database()
        try:
            saved_limit = max(0.0, min(1000.0,
                float(self.db.preference("speed_limit_mib", "0"))))
        except (TypeError, ValueError):
            saved_limit = 0.0
        self._saved_limit_mib = saved_limit
        try:
            saved_parallel = max(1, min(10, int(self.db.preference("max_parallel", "3"))))
        except (TypeError, ValueError):
            saved_parallel = 3
        self._saved_parallel = saved_parallel
        try:
            saved_threads = int(self.db.preference("threads_per_file", "4"))
        except (TypeError, ValueError):
            saved_threads = 4
        if saved_threads not in (1, 2, 4, 8):
            saved_threads = 4
        self._saved_threads = saved_threads
        self.limiter = RateLimiter(int(saved_limit * 1024 * 1024))
        base = os.path.join(os.path.expanduser("~"), "Downloads", "Roch Xd")
        self.qm = QueueManager(self.db, base, saved_parallel, saved_threads,
                               limiter=self.limiter)
        self.qm.load_unfinished()
        self._bridge = RequestBridge(self)
        self._bridge.request.connect(self._handle_browser_request)
        self.api = start_api(self.qm, on_download_request=self._browser_request)

        self._rows = []        # ids dans l'ordre des lignes
        self._bars = {}
        self._prev = {}
        self._speed = {}
        self._tick = 0
        self._last_snapshot = []
        self._progress_dialogs = {}
        self._metadata_workers = []

        self._build_toolbar()
        self._build_tabs()
        self._install_shortcuts()

        self.folder_label = QLabel(f"Dossier : {base}")
        self.speed_label = QLabel("")
        self.statusBar().addWidget(self.folder_label, 1)
        self.statusBar().addPermanentWidget(self.speed_label)
        if self.api is None:
            self.statusBar().showMessage(
                "API navigateur indisponible (port 9615 déjà utilisé). "
                "Ferme les anciennes instances de Roch Xd puis relance.")

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    # ---------- construction ----------
    def _build_toolbar(self):
        tb = QToolBar("Actions")
        tb.setMovable(False)
        self.addToolBar(tb)

        for text, slot in (("➕ Ajouter", self.on_add),
                           ("⏸ Pause", self.on_pause),
                           ("▶ Reprendre", self.on_resume),
                           ("🗑 Supprimer", self.on_remove),
                           ("📁 Dossier", self.on_folder)):
            act = QAction(text, self)
            act.triggered.connect(slot)
            tb.addAction(act)

        tb.addSeparator()
        tb.addWidget(QLabel(" Limite : "))
        self.limit_spin = QDoubleSpinBox()
        self.limit_spin.setRange(0, 1000)
        self.limit_spin.setDecimals(1)
        self.limit_spin.setSingleStep(0.5)
        self.limit_spin.setSuffix(" Mo/s")
        self.limit_spin.setSpecialValueText("Illimité")
        self.limit_spin.setValue(self._saved_limit_mib)
        self.limit_spin.valueChanged.connect(self._set_speed_limit)
        tb.addWidget(self.limit_spin)

        tb.addWidget(QLabel("  Simultanés : "))
        self.par_spin = QSpinBox()
        self.par_spin.setRange(1, 10)
        self.par_spin.setValue(self._saved_parallel)
        self.par_spin.valueChanged.connect(self._set_max_parallel)
        tb.addWidget(self.par_spin)

        tb.addWidget(QLabel("  Connexions/fichier : "))
        self.thr_spin = QComboBox()
        for count in (1, 2, 4, 8):
            self.thr_spin.addItem(str(count), count)
        self.thr_spin.setCurrentIndex(self.thr_spin.findData(self._saved_threads))
        self.thr_spin.currentIndexChanged.connect(
            lambda index: self._set_threads_per_file(self.thr_spin.itemData(index)))
        tb.addWidget(self.thr_spin)

        tb.addSeparator()
        tb.addWidget(QLabel("  État : "))
        self.state_filter = QComboBox()
        self.state_filter.addItem("Tous", "all")
        for label, key in (("En cours", "in_progress"), ("Terminés", "done"),
                           ("En pause", "paused"), ("Échecs", "error")):
            self.state_filter.addItem(label, key)
        self.state_filter.currentIndexChanged.connect(self.refresh)
        tb.addWidget(self.state_filter)
        tb.addWidget(QLabel("  Catégorie : "))
        self.category_filter = QComboBox()
        self.category_filter.addItem("Toutes", "all")
        for row in self.db.categories():
            self.category_filter.addItem(row["name"], row["name"])
        self.category_filter.currentIndexChanged.connect(self.refresh)
        tb.addWidget(self.category_filter)

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels(
            ["Fichier", "Catégorie", "Taille", "Téléchargé", "Progression",
             "Vitesse", "Temps restant", "État", "Destination", "Ajouté le"])
        self._style_table(self.table, stretch_col=0)
        self.table.setColumnWidth(4, 170)
        self.table.setColumnWidth(6, 110)
        self.table.setColumnWidth(7, 115)
        self.table.setColumnWidth(8, 220)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.doubleClicked.connect(self._double_click_action)
        self.tabs.addTab(self.table, "Téléchargements")

        self.hist = QTableWidget(0, 5)
        self.hist.setHorizontalHeaderLabels(
            ["Nom", "Catégorie", "Taille", "État", "Date"])
        self._style_table(self.hist, stretch_col=0)
        self.tabs.addTab(self.hist, "Historique")

    def _install_shortcuts(self):
        self.shortcut_move = QShortcut(QKeySequence("Ctrl+M"), self)
        self.shortcut_move.setContext(Qt.ApplicationShortcut)
        self.shortcut_move.activated.connect(self._shortcut_move)
        self.shortcut_delete = QShortcut(QKeySequence("Delete"), self)
        self.shortcut_delete.setContext(Qt.ApplicationShortcut)
        self.shortcut_delete.activated.connect(self._shortcut_delete)
        self.shortcut_enter = QShortcut(QKeySequence("Return"), self.table)
        self.shortcut_enter.activated.connect(self._double_click_action)
        self.shortcut_select_all = QShortcut(QKeySequence("Ctrl+A"), self.table)
        self.shortcut_select_all.setContext(Qt.WidgetWithChildrenShortcut)
        self.shortcut_select_all.activated.connect(self.table.selectAll)
        self.history_select_all = QShortcut(QKeySequence("Ctrl+A"), self.hist)
        self.history_select_all.setContext(Qt.WidgetWithChildrenShortcut)
        self.history_select_all.activated.connect(self.hist.selectAll)

    @staticmethod
    def _style_table(t, stretch_col):
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setSelectionMode(QTableWidget.ExtendedSelection)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setAlternatingRowColors(True)
        t.setShowGrid(False)
        t.verticalHeader().setDefaultSectionSize(30)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)
        t.setStyleSheet(
            "QTableWidget{background:#fff;alternate-background-color:#f5f7fa;"
            "selection-background-color:#d9eaff;selection-color:#17212b;border:0}"
            "QHeaderView::section{background:#edf1f5;color:#34404c;font-weight:600;"
            "padding:7px;border:0;border-bottom:1px solid #d6dce3}"
        )

    def _set_speed_limit(self, value):
        self.limiter.set_rate(int(value * 1024 * 1024))
        self.db.set_preference("speed_limit_mib", value)

    def _set_max_parallel(self, value):
        self.qm.max_parallel = int(value)
        self.db.set_preference("max_parallel", value)

    def _set_threads_per_file(self, value):
        self.qm.n_threads = max(1, min(int(value), 8))
        self.db.set_preference("threads_per_file", value)

    # ---------- actions ----------
    def _selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._rows[rows[0].row()]

    def on_add(self):
        dlg = AddDialog(self)
        if dlg.exec_():
            start_at = dlg.start_at()
            for url in dlg.urls():
                # Do not perform DNS/HTTP/extractor work before the confirmation UI.
                provisional_name = clean_filename(filename_from_url(url)) or "download"
                prepared = {"initial_url": url, "url": url, "name": provisional_name,
                            "size": None, "mime_type": "", "extension": os.path.splitext(provisional_name)[1],
                            "headers": {}, "stream": url.lower().split("?", 1)[0].endswith(".m3u8"),
                            "supports_range": None, "_needs_metadata": True,
                            "_require_metadata": is_extractable_url(url)}
                self._confirm_download(prepared, start_at=start_at)

    def _start_metadata_lookup(self, prepared, dialog):
        worker = MetadataWorker(self.qm, prepared["initial_url"], prepared, self)
        self._metadata_workers.append(worker)
        worker.result.connect(lambda result, p=prepared, d=dialog:
                              self._metadata_result(p, d, result))
        worker.finished.connect(lambda w=worker: self._metadata_workers.remove(w)
                                if w in self._metadata_workers else None)
        worker.start()

    @staticmethod
    def _metadata_result(prepared, dialog, result):
        if result.get("error"):
            prepared["_needs_metadata"] = False
            prepared["metadata_error"] = result["error"]
            if dialog and dialog.isVisible():
                dialog.details.setText(dialog.details.text() +
                                       "\nDétection indisponible : " + result["error"])
            return
        # A metadata preflight may replace the provisional transfer dictionary.
        # Keep the extension's page/stream context and HLS estimate across it.
        for key in ("estimated_total", "page_url", "page_title", "video_title",
                    "source_site", "media_format", "resolution", "bitrate_kbps",
                    "duration_seconds", "original_filename", "stream", "headers",
                    "source_url", "_preferred_title", "_preferred_extension"):
            if key in prepared:
                result.setdefault(key, prepared[key])
        preferred_title = prepared.get("_preferred_title")
        extracted_info = result.get("video_info")
        if extracted_info and extracted_info.title:
            extracted_title = clean_filename(clean_page_title(
                extracted_info.title, prepared.get("source_site")))
            if extracted_title and not is_generic_filename(extracted_title):
                preferred_title = extracted_title
        if preferred_title:
            ext = (prepared.get("_preferred_extension") or
                   os.path.splitext(result.get("name") or "")[1] or "")
            result["name"] = filename_with_extension(preferred_title, ext)
        if dialog:
            dialog.apply_metadata(result)
        else:
            prepared.update(result)
        prepared["_needs_metadata"] = False

    def _browser_request(self, data):
        """Bridge the extension's worker thread to the Qt GUI thread."""
        completed = threading.Event()
        result = {}
        self._bridge.request.emit((data, completed, result))
        # The browser request waits for the user's confirmation dialog, while
        # metadata work itself runs in a background QThread.
        # The API thread must stay alive until the user confirms or cancels.
        # A short timeout used to report failure while the confirmation
        # dialog was still open, even though the GUI later accepted the item.
        if not completed.wait(600):
            return {"ok": False, "unavailable": True,
                    "error": "La demande a expiré avant confirmation."}
        return result

    def _handle_browser_request(self, envelope):
        data, completed, result = envelope
        try:
            def field(name, limit=500):
                value = data.get(name)
                return value.strip()[:limit] if isinstance(value, str) else ""

            filename_info = browser_filename_info(data)
            original_name = filename_info["original_name"]
            source_site = field("source_site", 200)
            preferred = filename_info["preferred_title"]
            format_ext = filename_info["format_ext"]
            extension = filename_info["extension"]
            name = filename_info["name"]
            stream = bool(data.get("stream")) or data["url"].lower().split("?", 1)[0].endswith(".m3u8")
            def positive_number(key):
                try:
                    value = float(data.get(key))
                except (TypeError, ValueError):
                    return 0.0
                return value if 0 < value < 1e15 else 0.0

            media_size = positive_number("media_size")
            bitrate_kbps = positive_number("bitrate_kbps")
            duration_seconds = positive_number("duration_seconds")
            estimated_total = 0
            if stream:
                if media_size and data.get("size_estimated"):
                    estimated_total = int(media_size)
                elif bitrate_kbps and duration_seconds:
                    estimated_total = int(bitrate_kbps * 1000 * duration_seconds / 8)
            prepared = {"initial_url": data["url"], "url": data["url"],
                        "name": name, "size": None, "mime_type": "",
                        "extension": os.path.splitext(name)[1],
                        "headers": data.get("headers") or {},
                        "stream": stream,
                        "estimated_total": estimated_total,
                        "source_url": data.get("referer"),
                        "page_url": field("page_url", 2000),
                        "page_title": field("page_title"),
                        "video_title": field("video_title"),
                        "source_site": source_site,
                        "media_format": format_ext,
                        "resolution": field("resolution", 24),
                        "bitrate_kbps": bitrate_kbps or None,
                        "duration_seconds": duration_seconds or None,
                        "original_filename": original_name,
                        "_preferred_title": preferred,
                        "_preferred_extension": extension,
                        "_format_index": data.get("formatIndex"),
                        "_needs_metadata": True,
                        "_require_metadata": is_extractable_url(data["url"])}
            cached_info = data.get("_video_info")
            format_index = data.get("formatIndex")
            if cached_info is not None and isinstance(format_index, int):
                # /extract already produced the selected formats. Reuse that
                # result so clicking a row does not run yt-dlp a second time.
                selected = self.qm.prepare(
                    data["url"], headers=prepared["headers"],
                    source_url=prepared["source_url"], video_info=cached_info,
                    format_index=format_index)
                if selected.get("error"):
                    raise ValueError(selected["error"])
                prepared.update(selected)
                prepared.update({
                    "page_url": field("page_url", 2000),
                    "page_title": field("page_title"),
                    "video_title": field("video_title"),
                    "source_site": source_site,
                    "resolution": field("resolution", 24),
                    "bitrate_kbps": bitrate_kbps or None,
                    "duration_seconds": duration_seconds or None,
                    "estimated_total": estimated_total,
                    "original_filename": original_name,
                    "video_info": cached_info,
                    "_needs_metadata": False,
                    "_require_metadata": False,
                    "_format_index": format_index,
                })
                if preferred:
                    extension = os.path.splitext(prepared.get("name") or "")[1]
                    prepared["name"] = filename_with_extension(preferred, extension)
                    prepared["_preferred_title"] = preferred
                    prepared["_preferred_extension"] = extension
            item_id = self._confirm_download(prepared)
            if item_id is None:
                result.update(ok=False, cancelled=True)
            else:
                result.update(ok=True, id=item_id)
        except Exception as exc:
            result.update(ok=False, error=str(exc))
        finally:
            completed.set()

    def _confirm_download(self, prepared, start_at=None):
        # Unowned top-level dialog: minimizing the main window does not minimize it.
        dialog = DownloadInfoDialog(prepared, self.db, self.qm.base_dir, None)
        if prepared.get("_needs_metadata"):
            self._start_metadata_lookup(prepared, dialog)
        if not dialog.exec_():
            return None
        values = dialog.values()
        if (values.get("format_index") is not None and
                prepared.get("video_info")):
            prepared.update(self.qm.prepare(
                values["url"], video_info=prepared["video_info"],
                format_index=values["format_index"],
                extra_headers=prepared.get("headers")))
        if values["url"] != (prepared.get("initial_url") or prepared.get("url")):
            # Keep URL edits non-blocking too. Downloader.probe validates the
            # actual response when the transfer starts.
            changed_name = clean_filename(filename_from_url(values["url"])) or "download"
            refreshed = {"initial_url": values["url"], "url": values["url"],
                         "name": changed_name, "size": None, "mime_type": "",
                         "extension": os.path.splitext(changed_name)[1],
                         "headers": prepared.get("headers") or {},
                         "source_url": prepared.get("source_url"),
                         "stream": values["url"].lower().split("?", 1)[0].endswith(".m3u8"),
                         "supports_range": None}
            if values["name"] == prepared.get("name"):
                values["name"] = refreshed["name"]
                values["path"] = os.path.join(os.path.dirname(values["path"]),
                                               values["name"])
            prepared = refreshed
        prepared["initial_url"] = values["url"]
        prepared["url"] = prepared.get("url") or values["url"]
        prepared["name"] = values["name"]
        prepared["extension"] = os.path.splitext(values["name"])[1]
        category = values["category"]
        if values["remember_path"]:
            existing = next((row for row in self.db.categories()
                             if row["name"] == category), None)
            self.db.save_category(category, values["folder"], True,
                                  bool(existing and existing["is_custom"]))
        if self.category_filter.findData(category) < 0:
            self.category_filter.addItem(category, category)
        if any(os.path.normcase(item.dest) == os.path.normcase(values["path"])
               and item.state in ("queued", "scheduled", "deferred", "active",
                                  "paused", "interrupted", "error")
               for item in self.qm.items.values()):
            stem, ext = os.path.splitext(values["path"])
            idx = 1
            candidate = f"{stem} ({idx}){ext}"
            while os.path.exists(candidate) or any(
                    os.path.normcase(item.dest) == os.path.normcase(candidate)
                    for item in self.qm.items.values()
                    if item.state in ("queued", "scheduled", "deferred", "active",
                                      "paused", "interrupted", "error")):
                idx += 1
                candidate = f"{stem} ({idx}){ext}"
            answer = QMessageBox.question(
                self, "Chemin déjà utilisé",
                f"Un autre téléchargement utilise ce chemin. Utiliser {os.path.basename(candidate)} ?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if answer != QMessageBox.Yes:
                return None
            values["path"] = candidate
            values["name"] = os.path.basename(candidate)
        try:
            item_id = self.qm.add_prepared(
                prepared, filename=values["name"], path=values["path"],
                category=category, description=values["description"],
                deferred=values["deferred"], start_at=start_at,
                replace_existing=values["replace_existing"])
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Ajout impossible", str(exc))
            return None
        self._show_progress(item_id)
        self.refresh()
        return item_id

    def _show_progress(self, item_id):
        """Reuse the sole top-level progress/final window for this download ID."""
        dialog = self._progress_dialogs.get(item_id)
        if dialog is None:
            dialog = DownloadProgressDialog(item_id, self.qm, self.db, parent=None,
                                            finished_callback=self._progress_finished)
            self._progress_dialogs[item_id] = dialog
        if dialog.isMinimized():
            dialog.showNormal()
        else:
            dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def _progress_finished(self, item, action):
        dialog = self._progress_dialogs.get(item.id)
        path = item.dest
        # The same progress window is already the completion notification.
        if action == "open" and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif action == "folder" and os.path.isdir(os.path.dirname(path)):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        elif action == "close" and dialog:
            dialog.hide()
        self.refresh()

    def on_pause(self):
        for item_id in self._selected_ids():
            self.qm.pause(item_id)

    def on_resume(self):
        for item_id in self._selected_ids():
            self._resume_item(item_id)

    def _selected_ids(self):
        model = self.table.selectionModel()
        rows = sorted(model.selectedRows(), key=lambda index: index.row())
        return [self._rows[index.row()] for index in rows
                if 0 <= index.row() < len(self._rows)]

    def _select_all_visible(self):
        widget = self.tabs.currentWidget()
        if isinstance(widget, QTableWidget):
            widget.selectAll()

    def _cancel_ids(self, item_ids):
        for item_id in item_ids:
            self.qm.cancel(item_id)

    def _resume_ids(self, item_ids):
        for item_id in item_ids:
            self._resume_item(item_id)

    def _open_selected_folders(self, item_ids):
        folders = {os.path.dirname((self.db.get(item_id) or {}).get("path") or "")
                   for item_id in item_ids}
        for folder in folders:
            if folder and os.path.isdir(folder):
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_selected_info(self, item_ids):
        lines = []
        for item_id in item_ids:
            row = self.db.get(item_id) or {}
            lines.append("\t".join(str(row.get(key) or "")
                                   for key in ("name", "url", "path", "status")))
        QApplication.clipboard().setText("\n".join(lines))

    def _resume_item(self, item_id):
        row = self.db.get(item_id)
        if not row:
            return
        path = row.get("path") or ""
        item = self.qm.items.get(item_id)
        partial_bytes = max(row.get("downloaded") or 0,
                            getattr(getattr(item, "dl", None), "downloaded", 0))
        resumable = (row.get("supports_range") or row.get("format_id") or
                     (row.get("url") or "").lower().split("?", 1)[0].endswith(".m3u8") or
                     os.path.exists(path + ".flashdl") or os.path.isdir(path + ".hls"))
        if partial_bytes and not resumable:
            answer = QMessageBox.question(
                self, "Reprise non disponible",
                "Le serveur ne confirme pas la reprise de ce fichier. Recommencer depuis le début ?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self.qm.resume(item_id)

    def on_remove(self):
        self._remove_selected()

    def on_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier de téléchargement",
                                             self.qm.base_dir)
        if d:
            self.qm.base_dir = d
            self.folder_label.setText(f"Dossier : {d}")

    def _current_row(self, item_id):
        return next((row for row in self._last_snapshot if row["id"] == item_id), None)

    def _show_context_menu(self, point):
        item = self.table.itemAt(point)
        if item is None:
            return
        clicked_id = self._rows[item.row()]
        if clicked_id not in self._selected_ids():
            self.table.clearSelection()
            self.table.selectRow(item.row())
        selected_ids = self._selected_ids()
        if len(selected_ids) > 1:
            rows = {item_id: (self._current_row(item_id) or {}).get("state")
                    for item_id in selected_ids}
            active = [item_id for item_id, state in rows.items()
                      if state in ("active", "downloading", "queued", "scheduled")]
            paused = [item_id for item_id, state in rows.items()
                      if state in ("paused", "interrupted", "error", "deferred")]
            cancellable = [item_id for item_id, state in rows.items()
                           if state in ("active", "downloading", "queued", "scheduled", "paused")]
            menu = QMenu(self)
            pause = menu.addAction(f"Mettre en pause ({len(active)})")
            pause.setEnabled(bool(active))
            pause.triggered.connect(lambda: [self.qm.pause(i) for i in active])
            resume = menu.addAction(f"Reprendre ({len(paused)})")
            resume.setEnabled(bool(paused))
            resume.triggered.connect(lambda: self._resume_ids(paused))
            cancel = menu.addAction(f"Annuler les transferts ({len(cancellable)})")
            cancel.setEnabled(bool(cancellable))
            cancel.triggered.connect(lambda: self._cancel_ids(cancellable))
            menu.addSeparator()
            remove = menu.addAction(f"Retirer {len(selected_ids)} entrées de la liste…")
            remove.triggered.connect(self._remove_selected)
            folders = menu.addAction("Ouvrir le ou les dossiers")
            folders.triggered.connect(lambda: self._open_selected_folders(selected_ids))
            copy = menu.addAction("Copier les informations")
            copy.triggered.connect(lambda: self._copy_selected_info(selected_ids))
            menu.exec_(self.table.viewport().mapToGlobal(point))
            return
        item_id = self._rows[item.row()]
        row = self._current_row(item_id) or {}
        path = row.get("path") or ""
        state = row.get("state")
        exists = bool(path and os.path.isfile(path))
        folder_exists = bool(path and os.path.isdir(os.path.dirname(path)))
        complete = state == "done" and exists
        active = state in ("active", "downloading")
        resumable = state in ("paused", "interrupted", "error")

        menu = QMenu(self)
        show_progress = menu.addAction("Afficher les détails du téléchargement")
        show_progress.setEnabled(bool(item_id in self._progress_dialogs or
                                      item_id in self.qm.items))
        show_progress.triggered.connect(lambda: self._show_progress(item_id))
        menu.addSeparator()
        open_action = menu.addAction("Ouvrir")
        open_action.setEnabled(complete)
        open_action.triggered.connect(lambda: self._open_file(item_id))
        open_with = menu.addAction("Ouvrir avec…")
        open_with.setEnabled(complete)
        open_with.triggered.connect(lambda: self._open_with(item_id))
        open_folder = menu.addAction("Ouvrir le dossier")
        open_folder.setEnabled(folder_exists)
        open_folder.triggered.connect(lambda: self._open_folder(item_id))
        menu.addSeparator()
        move = menu.addAction("Déplacer/Renommer\tCtrl+M")
        move.setEnabled(not active and state not in ("queued", "scheduled") and bool(path))
        move.triggered.connect(lambda: self._move_rename(item_id))
        menu.addSeparator()
        redownload = menu.addAction("Retélécharger")
        redownload.setEnabled(bool(row.get("url") or row.get("initial_url")))
        redownload.triggered.connect(lambda: self._redownload(item_id))
        resume = menu.addAction("Reprendre le téléchargement")
        resume.setEnabled(resumable or state == "deferred")
        resume.triggered.connect(lambda: self._resume_item(item_id))
        stop = menu.addAction("Arrêter le téléchargement")
        stop.setEnabled(active or state in ("queued", "scheduled"))
        stop.triggered.connect(lambda: self.qm.stop(item_id))
        update_url = menu.addAction("Actualiser adresse téléchargement")
        update_url.setEnabled(state not in ("done", "cancelled", "active", "downloading"))
        update_url.triggered.connect(lambda: self._update_url(item_id))
        menu.addSeparator()
        remove = menu.addAction("Supprimer")
        remove.triggered.connect(lambda: self._remove_entry(item_id))
        menu.addSeparator()
        queues = menu.addMenu("Ajouter dans la file d’attente")
        for queue in self.db.queues():
            action = queues.addAction(queue["name"])
            action.triggered.connect(
                lambda checked=False, q=queue["name"]: self.qm.change_queue(item_id, q))
        queues.addSeparator()
        new_queue = queues.addAction("Créer une file…")
        new_queue.triggered.connect(lambda: self._create_queue(item_id))
        queues.addSeparator()
        queue_enabled = self.qm.queue_enabled(row.get("queue_name") or "Principale")
        queue_toggle = queues.addAction(
            "Suspendre cette file" if queue_enabled else "Démarrer cette file")
        current_queue = row.get("queue_name") or "Principale"
        queue_toggle.triggered.connect(
            lambda: self.db.set_queue_enabled(current_queue, not queue_enabled))
        remove_queue = menu.addAction("Supprimer de la file d’attente")
        remove_queue.setEnabled(row.get("queue_name") not in (None, "Principale"))
        remove_queue.triggered.connect(
            lambda: self.qm.change_queue(item_id, "Principale"))
        menu.addSeparator()
        double_click = menu.addMenu("Double-cliquez sur")
        choices = (("open", "Ouvrir le fichier"), ("folder", "Ouvrir le dossier"),
                   ("properties", "Afficher les propriétés"),
                   ("resume", "Reprendre le téléchargement"), ("none", "Ne rien faire"))
        current = self.db.preference("double_click_action", "open")
        for value, label in choices:
            action = double_click.addAction(label)
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(
                lambda checked=False, v=value: self.db.set_preference("double_click_action", v))
        menu.addSeparator()
        properties = menu.addAction("Propriétés")
        properties.triggered.connect(lambda: self._show_properties(item_id))
        menu.exec_(self.table.viewport().mapToGlobal(point))

    def _open_file(self, item_id):
        row = self.db.get(item_id)
        if not row or row.get("status") != "done" or not os.path.isfile(row.get("path") or ""):
            QMessageBox.warning(self, "Fichier indisponible",
                                "Le téléchargement n’est pas terminé ou le fichier est absent.")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(row["path"])):
            QMessageBox.warning(self, "Ouverture impossible", "Aucune application n’a pu ouvrir ce fichier.")

    def _open_with(self, item_id):
        row = self.db.get(item_id)
        if not row or row.get("status") != "done" or not os.path.isfile(row.get("path") or ""):
            QMessageBox.warning(self, "Fichier indisponible", "Le fichier terminé est introuvable.")
            return
        try:
            if os.name == "nt":
                subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", row["path"]])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(row["path"]))
        except OSError as exc:
            QMessageBox.warning(self, "Ouverture impossible", str(exc))

    def _open_folder(self, item_id):
        row = self.db.get(item_id)
        if not row:
            return
        path = row.get("path") or ""
        folder = os.path.dirname(path)
        if not os.path.isdir(folder):
            QMessageBox.warning(self, "Dossier introuvable", folder)
            return
        try:
            if os.name == "nt" and os.path.isfile(path):
                subprocess.Popen(["explorer.exe", f"/select,{path}"])
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        except OSError as exc:
            QMessageBox.warning(self, "Ouverture impossible", str(exc))

    def _move_rename(self, item_id):
        row = self.db.get(item_id)
        if not row:
            return
        dialog = MoveRenameDialog(row["path"], self)
        if not dialog.exec_():
            return
        target = dialog.path()
        old_ext = os.path.splitext(row["path"])[1]
        if old_ext and not os.path.splitext(target)[1]:
            target += old_ext
        try:
            self.qm.move_rename(item_id, target)
        except (OSError, ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "Déplacement impossible", str(exc))
        self.refresh()

    def _redownload(self, item_id):
        row = self.db.get(item_id)
        if not row:
            return
        prepared = self.qm.prepare(row.get("initial_url") or row.get("url"))
        if prepared.get("error"):
            QMessageBox.warning(self, "Préparation impossible", prepared["error"])
            return
        self._confirm_download(prepared)

    def _update_url(self, item_id):
        row = self.db.get(item_id)
        if not row:
            return
        dialog = UrlUpdateDialog(row.get("url"), self)
        if not dialog.exec_():
            return
        try:
            prepared = self.qm.prepare(dialog.url())
            if prepared.get("error"):
                raise ValueError(prepared["error"])
            old_validators = (row.get("etag"), row.get("last_modified"), row.get("size"))
            new_validators = (prepared.get("etag"), prepared.get("last_modified"), prepared.get("size"))
            if old_validators != new_validators or row.get("url") != prepared.get("url"):
                answer = QMessageBox.question(
                    self, "Le fichier distant a changé",
                    "Les validateurs ou la taille diffèrent. Les données partielles seront supprimées. Continuer ?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer != QMessageBox.Yes:
                    return
            self.qm.update_url(item_id, prepared.get("url") or dialog.url(), prepared)
        except Exception as exc:
            QMessageBox.warning(self, "Adresse non actualisée", str(exc))
        self.refresh()

    def _remove_entry(self, item_id):
        row = self.db.get(item_id)
        if not row:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Supprimer le téléchargement")
        box.setText("Que veux-tu supprimer ?")
        remove_entry = box.addButton("Retirer de la liste", QMessageBox.AcceptRole)
        delete_file = box.addButton("Supprimer aussi le fichier…", QMessageBox.DestructiveRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        partial = None
        if row.get("status") not in ("done", "cancelled"):
            from PyQt5.QtWidgets import QCheckBox
            partial = QCheckBox("Supprimer aussi les données partielles")
            box.setCheckBox(partial)
        box.exec_()
        if box.clickedButton() not in (remove_entry, delete_file):
            return
        remove_local = box.clickedButton() == delete_file
        if remove_local:
            confirm = QMessageBox.question(
                self, "Confirmer la suppression du fichier",
                f"Supprimer définitivement ce fichier local ?\n{row.get('path')}",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if confirm != QMessageBox.Yes:
                return
        try:
            self.qm.remove_entry(item_id, delete_file=remove_local,
                                 delete_partial=bool(partial and partial.isChecked()))
        except OSError as exc:
            QMessageBox.warning(self, "Suppression impossible", str(exc))
            return
        self.refresh()

    def _create_queue(self, item_id):
        name, accepted = QInputDialog.getText(self, "Nouvelle file", "Nom de la file :")
        name = (name or "").strip()
        if accepted and name:
            self.db.add_queue(name)
            self.qm.change_queue(item_id, name)

    def _show_properties(self, item_id):
        row = self.db.get(item_id)
        if row:
            snap = self._current_row(item_id) or {}
            row["status"] = snap.get("state", row.get("status"))
            row["downloaded"] = snap.get("downloaded", row.get("downloaded"))
            row["size"] = snap.get("total", row.get("size"))
            row["current_speed"] = self._speed.get(item_id, 0)
            row["transfer_metrics"] = snap.get("metrics") or {}
            if row.get("started_at") and not row.get("finished_at"):
                try:
                    started = time.mktime(time.strptime(row["started_at"],
                                                        "%Y-%m-%d %H:%M:%S"))
                    row["duration"] = max(0, time.time() - started)
                except (ValueError, TypeError, OverflowError):
                    pass
            if row.get("duration") and row.get("size"):
                row["average_speed"] = row["size"] / row["duration"]
            elif row.get("status") == "done" and row.get("started_at") and row.get("finished_at"):
                try:
                    start = time.strptime(row["started_at"], "%Y-%m-%d %H:%M:%S")
                    finish = time.strptime(row["finished_at"], "%Y-%m-%d %H:%M:%S")
                    elapsed = time.mktime(finish) - time.mktime(start)
                    row["average_speed"] = row["size"] / elapsed if elapsed > 0 else None
                except (ValueError, TypeError):
                    row["average_speed"] = None
            row["resume_info"] = ("HTTP Range et validateurs" if row.get("supports_range")
                                  else "Continuité yt-dlp" if row.get("format_id")
                                  else "Segments HLS" if (row.get("url") or "").lower().endswith(".m3u8")
                                  else "Redémarrage complet si Range absent")
            dialog = PropertiesDialog(row, self)
            dialog.exec_()

    def _double_click_action(self, *_):
        item_id = self._selected_id()
        if item_id is None:
            return
        action = self.db.preference("double_click_action", "open")
        if action == "none":
            return
        row = self.db.get(item_id) or {}
        state = row.get("status")
        if state in ("downloading", "active", "queued", "paused", "interrupted",
                     "error", "cancelled") and item_id in self.qm.items:
            self._show_progress(item_id)
            return
        file_ready = state == "done" and os.path.isfile(row.get("path") or "")
        if action == "open" and not file_ready:
            action = ("resume" if state in ("paused", "interrupted", "error", "deferred")
                      else "properties")
        if action == "folder":
            self._open_folder(item_id)
        elif action == "properties":
            self._show_properties(item_id)
        elif action == "resume":
            self._resume_item(item_id)
        else:
            self._open_file(item_id)

    def _shortcut_move(self):
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
            return
        item_id = self._selected_id()
        if item_id is not None and len(self._selected_ids()) == 1:
            self._move_rename(item_id)

    def _shortcut_delete(self):
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox)):
            return
        self._remove_selected()

    def _remove_selected(self):
        item_ids = self._selected_ids()
        if not item_ids:
            return
        if len(item_ids) == 1:
            self._remove_entry(item_ids[0])
            return
        answer = QMessageBox.question(
            self, "Retirer plusieurs téléchargements",
            f"Retirer {len(item_ids)} entrées ? Les fichiers terminés resteront "
            "sur le disque. Les transferts actifs seront arrêtés et les données "
            "partielles supprimées.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        # Joining active network workers can take seconds; keep that work away
        # from Qt's event thread. The periodic refresh reflects completed removals.
        threading.Thread(
            target=lambda: [self.qm.remove_entry(item_id, delete_partial=True)
                            for item_id in item_ids],
            daemon=True).start()

    # ---------- rafraîchissement ----------
    def _rebuild(self, snap):
        selected_ids = set(self._selected_ids()) if self.table.rowCount() else set()
        self._rows = [s["id"] for s in snap]
        self._bars = {}
        self.table.setRowCount(len(snap))
        for row, s in enumerate(snap):
            for col in range(10):
                self.table.setItem(row, col, QTableWidgetItem(""))
            name_item = self.table.item(row, 0)
            name_item.setText(self._file_icon(s) + "  " + s["name"])
            name_item.setData(Qt.UserRole, s["id"])
            self.table.item(row, 1).setText(s["category"])
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setStyleSheet(
                "QProgressBar{background:#e5e7eb;border:0;border-radius:4px;"
                "text-align:center;color:#17212b}"
                "QProgressBar::chunk{background:#24a148;border-radius:4px}")
            self._bars[s["id"]] = bar
            self.table.setCellWidget(row, 4, bar)
            if s["id"] in selected_ids:
                self.table.selectRow(row)

    @staticmethod
    def _file_icon(item):
        mime = (item.get("mime_type") or "").lower()
        ext = os.path.splitext(item.get("name") or "")[1].lower()
        if mime.startswith("video/") or ext in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}:
            return "🎬"
        if mime.startswith("audio/") or ext in {".mp3", ".wav", ".m4a", ".flac", ".ogg"}:
            return "♫"
        if mime.startswith("image/"):
            return "▧"
        if ext in {".zip", ".7z", ".rar", ".gz"}:
            return "▣"
        return "📄"

    def refresh(self):
        all_snap = self.qm.snapshot()
        self._last_snapshot = all_snap
        state = self.state_filter.currentData() if hasattr(self, "state_filter") else "all"
        category = self.category_filter.currentData() if hasattr(self, "category_filter") else "all"
        in_progress_states = {"queued", "scheduled", "deferred", "active",
                              "downloading", "preparing"}
        snap = [s for s in all_snap
                if (state == "all" or
                    state == "in_progress" and s["state"] in in_progress_states or
                    state == "done" and s["state"] == "done" or
                    state == "paused" and s["state"] == "paused" or
                    state == "error" and s["state"] in {"error", "interrupted"})
                and (category == "all" or s["category"] == category)]
        if [s["id"] for s in snap] != self._rows:
            self._rebuild(snap)

        now = time.time()
        total_speed = 0
        for row, s in enumerate(snap):
            prev = self._prev.get(s["id"])
            instant_speed = 0
            elapsed = 0
            if prev and now > prev[0] and s["state"] == "active":
                elapsed = now - prev[0]
                delta = s["downloaded"] - prev[1]
                if delta >= 0:
                    instant_speed = delta / elapsed
            old_speed = self._speed.get(s["id"], 0)
            if s["state"] != "active":
                speed = 0
            elif not prev or elapsed <= 0 or s["downloaded"] < prev[1]:
                speed = instant_speed
            else:
                # Lisser les rafales sans masquer une interruption durable.
                alpha = 1 - math.exp(-elapsed / 1.0)
                speed = old_speed + alpha * (instant_speed - old_speed)
            self._speed[s["id"]] = speed
            self._prev[s["id"]] = (now, s["downloaded"])
            total_speed += speed

            size_text = fmt_size(s["total"])
            if s.get("size_estimated") and size_text:
                size_text = "≈ " + size_text + " (estimée)"
            self.table.item(row, 2).setText(size_text)
            self.table.item(row, 3).setText(fmt_size(s["downloaded"] or 0))

            bar = self._bars[s["id"]]
            if not s["total"] and s["state"] not in ("done", "error", "cancelled"):
                bar.setRange(0, 0)          # taille inconnue : barre animée
            else:
                bar.setRange(0, 100)
                # The engine may have received all bytes but still be verifying
                # or assembling. Reserve 100% for the committed final file.
                progress = int(s["percent"])
                bar.setValue(100 if s["state"] == "done" else min(progress, 99))

            self.table.item(row, 5).setText(fmt_speed(speed))
            remaining = ((s["total"] - s["downloaded"]) / speed
                         if s["total"] and speed > 0 else None)
            self.table.item(row, 6).setText(fmt_eta(remaining))

            label = STATES.get(s["state"], s["state"])
            engine_state = s.get("engine_status")
            if s["state"] == "active" and engine_state in {
                    "verifying", "assembling", "finalizing"}:
                label = {"verifying": "Vérification", "assembling": "Assemblage",
                         "finalizing": "Finalisation"}[engine_state]
            if s["state"] == "scheduled" and s["start_at"]:
                label += time.strftime(" (%H:%M)", time.localtime(s["start_at"]))
            state_item = self.table.item(row, 7)
            state_item.setText(label)
            state_item.setToolTip(s["error"] or "")

            self.table.item(row, 8).setText(s.get("path") or "")
            self.table.item(row, 8).setToolTip(s.get("path") or "")
            self.table.item(row, 9).setText(s.get("added_at") or "")

        self.speed_label.setText(
            f"Vitesse totale : {fmt_speed(total_speed) or '0.00 Mo/s'}")

        self._tick += 1
        if self.tabs.currentIndex() == 1 and self._tick % 4 == 0:
            self._refresh_history()

    def _refresh_history(self):
        rows = self.db.history(100)
        selected_ids = {
            self.hist.item(index.row(), 0).data(Qt.UserRole)
            for index in self.hist.selectionModel().selectedRows()
            if self.hist.item(index.row(), 0)
        }
        self.hist.setRowCount(len(rows))
        for r, h in enumerate(rows):
            values = [h["name"], h["category"], fmt_size(h["size"]),
                      DB_STATES.get(h["status"], h["status"]),
                      h["finished_at"] or h["added_at"]]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.UserRole, h["id"])
                self.hist.setItem(r, c, item)
            if h["id"] in selected_ids:
                self.hist.selectRow(r)

    # ---------- fermeture ----------
    def closeEvent(self, event):
        self.timer.stop()
        if self.api:
            self.api.shutdown()
        for worker in list(self._metadata_workers):
            worker.wait(9000)
        self.qm.shutdown()      # met tout en pause : la reprise reste possible
        event.accept()


def run_gui():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec_()
