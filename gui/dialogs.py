from PyQt5.QtCore import QTime
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QLabel, QMessageBox, QPlainTextEdit, QTimeEdit,
                             QVBoxLayout)

from core.scheduler import parse_start


class AddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajouter des téléchargements")
        self.resize(520, 300)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Colle un ou plusieurs liens (un par ligne) :"))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("http://127.0.0.1:8000/test.bin")
        lay.addWidget(self.edit)

        row = QHBoxLayout()
        self.chk = QCheckBox("Programmer le démarrage à :")
        self.time = QTimeEdit(QTime.currentTime().addSecs(3600))
        self.time.setDisplayFormat("HH:mm")
        self.time.setEnabled(False)
        self.chk.toggled.connect(self.time.setEnabled)
        row.addWidget(self.chk)
        row.addWidget(self.time)
        row.addStretch()
        lay.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _check(self):
        if not self.urls():
            QMessageBox.warning(self, "FlashDL",
                                "Aucun lien valide (il doit commencer par http).")
            return
        self.accept()

    def urls(self):
        lines = [l.strip() for l in self.edit.toPlainText().splitlines()]
        return [l for l in lines if l.lower().startswith(("http://", "https://"))]

    def start_at(self):
        if not self.chk.isChecked():
            return None
        return parse_start(self.time.time().toString("HH:mm"))