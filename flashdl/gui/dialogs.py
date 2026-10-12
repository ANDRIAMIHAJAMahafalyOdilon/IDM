from PyQt5.QtCore import QTime
from PyQt5.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout,
                             QComboBox, QLabel, QMessageBox, QPlainTextEdit,
                             QTimeEdit, QVBoxLayout)

from core.scheduler import parse_start


class AddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ajouter des téléchargements")
        self.resize(520, 340)

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


class VideoFormatDialog(QDialog):
    """Permet de choisir la qualité vidéo et indique la taille estimée."""

    def __init__(self, video_info, parent=None):
        super().__init__(parent)
        self.video_info = video_info
        self.setWindowTitle("Choisir la qualité vidéo")
        self.resize(460, 190)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Vidéo : {video_info.title or 'Vidéo'}"))
        layout.addWidget(QLabel("Qualité de l’image / format :"))

        self.formats = QComboBox()
        for index, video_format in enumerate(video_info.formats):
            label = video_format.label or "Qualité inconnue"
            ext = (video_format.ext or "").upper()
            self.formats.addItem(f"{label} ({ext})" if ext else label, index)
        self.formats.currentIndexChanged.connect(self._update_details)
        layout.addWidget(self.formats)

        self.details = QLabel()
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self._update_details()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_details(self, *_):
        index = self.formats.currentData()
        if index is None:
            self.details.setText("Aucun format vidéo disponible.")
            return
        video_format = self.video_info.formats[index]
        size = video_format.size
        if size:
            size = float(size)
            for unit in ("o", "Ko", "Mo", "Go", "To"):
                if size < 1024 or unit == "To":
                    size_text = (f"{size:.0f} {unit}" if unit == "o"
                                 else f"{size:.2f} {unit}")
                    break
                size /= 1024
            size_text = f"environ {size_text}"
        else:
            size_text = "indisponible (le site ne fournit pas cette estimation)"
        quality = video_format.label or "non précisée par le site"
        details = f"Qualité : {quality}\nTaille estimée : {size_text}"
        if len(self.video_info.formats) == 1 and video_format.is_hls:
            details += ("\nAucune autre résolution n’a été détectée dans cette "
                        "playlist M3U8.")
        self.details.setText(details)

    def selected_format_index(self):
        return self.formats.currentData()
