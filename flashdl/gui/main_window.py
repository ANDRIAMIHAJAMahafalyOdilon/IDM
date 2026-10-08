import os
import sys
import time

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (QAction, QApplication, QDoubleSpinBox, QFileDialog,
                             QHeaderView, QLabel, QMainWindow, QMessageBox,
                             QProgressBar, QSpinBox, QTableWidget,
                             QTableWidgetItem, QTabWidget, QToolBar)

from core.api_server import start_api
from core.database import Database
from core.queue_manager import QueueManager
from core.rate_limiter import RateLimiter
from .dialogs import AddDialog

STATES = {"scheduled": "Programmé", "queued": "En attente", "active": "En cours",
          "paused": "En pause", "done": "Terminé", "error": "Erreur"}
DB_STATES = {"queued": "En attente", "downloading": "En cours", "paused": "En pause",
             "done": "Terminé", "error": "Erreur", "scheduled": "Programmé",
             "cancelled": "Annulé"}


def fmt_size(n):
    if not n:
        return "?"
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_speed(bps):
    return f"{bps / 1024 / 1024:.2f} Mo/s" if bps > 0 else ""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FlashDL")
        self.resize(980, 540)

        self.db = Database()
        self.limiter = RateLimiter(0)
        base = os.path.join(os.path.expanduser("~"), "Downloads", "FlashDL")
        self.qm = QueueManager(self.db, base, 3, 8, limiter=self.limiter)
        self.qm.load_unfinished()
        self.api = start_api(self.qm)

        self._rows = []        # ids dans l'ordre des lignes
        self._bars = {}
        self._prev = {}
        self._tick = 0

        self._build_toolbar()
        self._build_tabs()

        self.folder_label = QLabel(f"Dossier : {base}")
        self.speed_label = QLabel("")
        self.statusBar().addWidget(self.folder_label, 1)
        self.statusBar().addPermanentWidget(self.speed_label)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)

    # ---------- construction ----------
    def _build_toolbar(self):
        tb = QToolBar("Actions")
        tb.setMovable(False)
        self.addToolBar(tb)

        for text, slot in (("➕ Ajouter", self.on_add), ("⏸ Pause", self.on_pause),
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
        self.limit_spin.valueChanged.connect(
            lambda v: self.limiter.set_rate(int(v * 1024 * 1024)))
        tb.addWidget(self.limit_spin)

        tb.addWidget(QLabel("  Simultanés : "))
        self.par_spin = QSpinBox()
        self.par_spin.setRange(1, 10)
        self.par_spin.setValue(3)
        self.par_spin.valueChanged.connect(
            lambda v: setattr(self.qm, "max_parallel", v))
        tb.addWidget(self.par_spin)

        tb.addWidget(QLabel("  Threads/fichier : "))
        self.thr_spin = QSpinBox()
        self.thr_spin.setRange(1, 16)
        self.thr_spin.setValue(8)
        self.thr_spin.valueChanged.connect(
            lambda v: setattr(self.qm, "n_threads", v))
        tb.addWidget(self.thr_spin)

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Nom", "Catégorie", "Taille", "Progression", "Vitesse", "État"])
        self._style_table(self.table, stretch_col=0)
        self.table.setColumnWidth(3, 200)
        self.tabs.addTab(self.table, "Téléchargements")

        self.hist = QTableWidget(0, 6)
        self.hist.setHorizontalHeaderLabels(
            ["#", "Nom", "Catégorie", "Taille", "État", "Date"])
        self._style_table(self.hist, stretch_col=1)
        self.tabs.addTab(self.hist, "Historique")

    @staticmethod
    def _style_table(t, stretch_col):
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setSelectionMode(QTableWidget.SingleSelection)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.Stretch)

    # ---------- actions ----------
    def _selected_id(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            QMessageBox.information(self, "FlashDL",
                                    "Sélectionne d'abord un téléchargement.")
            return None
        return self._rows[rows[0].row()]

    def on_add(self):
        dlg = AddDialog(self)
        if dlg.exec_():
            start_at = dlg.start_at()
            for url in dlg.urls():
                self.qm.add(url, start_at)

    def on_pause(self):
        i = self._selected_id()
        if i is not None:
            self.qm.pause(i)

    def on_resume(self):
        i = self._selected_id()
        if i is not None:
            self.qm.resume(i)

    def on_remove(self):
        i = self._selected_id()
        if i is not None:
            self.qm.remove(i)

    def on_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Dossier de téléchargement",
                                             self.qm.base_dir)
        if d:
            self.qm.base_dir = d
            self.folder_label.setText(f"Dossier : {d}")

    # ---------- rafraîchissement ----------
    def _rebuild(self, snap):
        self._rows = [s["id"] for s in snap]
        self._bars = {}
        self.table.setRowCount(len(snap))
        for row, s in enumerate(snap):
            for col in range(6):
                self.table.setItem(row, col, QTableWidgetItem(""))
            self.table.item(row, 0).setText(s["name"])
            self.table.item(row, 1).setText(s["category"])
            bar = QProgressBar()
            bar.setRange(0, 100)
            self._bars[s["id"]] = bar
            self.table.setCellWidget(row, 3, bar)

    def refresh(self):
        snap = self.qm.snapshot()
        if [s["id"] for s in snap] != self._rows:
            self._rebuild(snap)

        now = time.time()
        total_speed = 0
        for row, s in enumerate(snap):
            speed = 0
            prev = self._prev.get(s["id"])
            if prev and s["state"] == "active" and now > prev[0]:
                speed = max(0, (s["downloaded"] - prev[1]) / (now - prev[0]))
            self._prev[s["id"]] = (now, s["downloaded"])
            total_speed += speed

            self.table.item(row, 2).setText(fmt_size(s["total"]))
            self._bars[s["id"]].setValue(int(s["percent"]))
            self.table.item(row, 4).setText(fmt_speed(speed))

            label = STATES.get(s["state"], s["state"])
            if s["state"] == "scheduled" and s["start_at"]:
                label += time.strftime(" (%H:%M)", time.localtime(s["start_at"]))
            state_item = self.table.item(row, 5)
            state_item.setText(label)
            state_item.setToolTip(s["error"] or "")

        self.speed_label.setText(
            f"Vitesse totale : {fmt_speed(total_speed) or '0.00 Mo/s'}")

        self._tick += 1
        if self.tabs.currentIndex() == 1 and self._tick % 4 == 0:
            self._refresh_history()

    def _refresh_history(self):
        rows = self.db.history(100)
        self.hist.setRowCount(len(rows))
        for r, h in enumerate(rows):
            values = [h["id"], h["name"], h["category"], fmt_size(h["size"]),
                      DB_STATES.get(h["status"], h["status"]),
                      h["finished_at"] or h["added_at"]]
            for c, v in enumerate(values):
                self.hist.setItem(r, c, QTableWidgetItem(str(v)))

    # ---------- fermeture ----------
    def closeEvent(self, event):
        self.timer.stop()
        if self.api:
            self.api.shutdown()
        self.qm.shutdown()      # met tout en pause : la reprise reste possible
        event.accept()


def run_gui():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec_()