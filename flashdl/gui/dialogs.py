from PyQt5.QtCore import QTime, QTimer, QSettings, Qt
import math
import os

from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                             QFileDialog, QFormLayout, QHBoxLayout, QInputDialog,
                             QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                             QPushButton, QTimeEdit, QVBoxLayout, QTabWidget,
                             QProgressBar, QTableWidget, QTableWidgetItem,
                             QSpinBox, QRadioButton, QButtonGroup, QGroupBox, QWidget,
                             QStackedWidget)
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtCore import QUrl

from core.metadata import clean_filename
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
            QMessageBox.warning(self, "Roch Xd",
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


def format_size(size):
    if size is None:
        return "Taille inconnue"
    size = float(size)
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if size < 1024 or unit == "To":
            return f"{size:.0f} {unit}" if unit == "o" else f"{size:.2f} {unit}"
        size /= 1024


class DownloadInfoDialog(QDialog):
    """The pre-transfer dialog that owns the user's name/category/path choice."""
    def __init__(self, prepared, db, base_dir, parent=None):
        super().__init__(parent)
        self.prepared = prepared
        self._initial_name = prepared.get("name") or "download"
        self._name_touched = False
        self.db = db
        self.base_dir = base_dir
        self.choice = "cancel"
        self.replace_existing = False
        self.setWindowTitle("Informations sur le fichier à télécharger")
        self.setMinimumWidth(600)
        self.setModal(True)

        root = QVBoxLayout(self)
        header = QHBoxLayout()
        ext = os.path.splitext(prepared.get("name", ""))[1].lower()
        icon = "🎬" if (prepared.get("mime_type", "").startswith("video/") or
                         ext in {".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v"}) else "📄"
        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 26px; min-width: 42px;")
        header.addWidget(icon_label)
        self.name_label = QLabel(prepared.get("name") or "Fichier à télécharger")
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet("font-weight: 600; font-size: 14px;")
        header.addWidget(self.name_label, 1)
        root.addLayout(header)

        form = QFormLayout()
        self.url_edit = QLineEdit(prepared.get("initial_url") or prepared.get("url") or "")
        self.url_edit.setToolTip("Adresse de la ressource. Elle peut être corrigée avant de démarrer.")
        form.addRow("URL du fichier :", self.url_edit)

        category_row = QHBoxLayout()
        self.category = QComboBox()
        self.category_rows = db.categories()
        for row in self.category_rows:
            self.category.addItem(row["name"], row["name"])
        suggested = next((row["name"] for row in self.category_rows
                          if row["name"] == self._suggest_category()), "Général")
        index = self.category.findData(suggested)
        self.category.setCurrentIndex(max(0, index))
        self.add_category_btn = QPushButton("Ajouter…")
        self.add_category_btn.clicked.connect(self._add_category)
        category_row.addWidget(self.category, 1)
        category_row.addWidget(self.add_category_btn)
        form.addRow("Catégorie :", category_row)

        self.save_as = QLineEdit()
        self.save_as.textEdited.connect(lambda _text: setattr(self, "_name_touched", True))
        self.browse_btn = QPushButton("…")
        self.browse_btn.setFixedWidth(36)
        self.browse_btn.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.save_as, 1)
        path_row.addWidget(self.browse_btn)
        form.addRow("Enregistrer sous :", path_row)

        self.remember = QCheckBox()
        self.remember.setText("Rappeler ce chemin pour la catégorie")
        form.addRow("Mémorisation :", self.remember)
        self.description = QLineEdit()
        self.description.setPlaceholderText("Facultatif")
        form.addRow("Description :", self.description)
        self.format_choice = None
        root.addLayout(form)

        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setStyleSheet("color: palette(mid); padding: 4px 0;")
        root.addWidget(self.details)
        self._update_details()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.later_btn = QPushButton("Plus tard")
        self.start_btn = QPushButton("Démarrer")
        self.cancel_btn = QPushButton("Annuler")
        self.start_btn.setDefault(True)
        if prepared.get("_require_metadata"):
            self.start_btn.setEnabled(False)
            self.later_btn.setEnabled(False)
            self.start_btn.setToolTip("En attente des informations nécessaires au transfert vidéo.")
        if prepared.get("_require_metadata"):
            self.start_btn.setEnabled(False)
            self.start_btn.setToolTip("En attente des informations nécessaires au transfert vidéo.")
        self.later_btn.clicked.connect(self._later)
        self.start_btn.clicked.connect(self._start)
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.later_btn)
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.cancel_btn)
        root.addLayout(buttons)

        self._remembered = {row["name"]: row["folder"] for row in self.category_rows
                            if row["remember_path"]}
        self._current_default = ""
        self._set_default_path()
        self._update_remember_label()
        self.category.currentIndexChanged.connect(self._category_changed)
        self._category_touched = False
        self.category.currentIndexChanged.connect(self._category_user_selected)

    def _update_remember_label(self):
        category = self.category.currentData() or "Général"
        self.remember.setText(f"Rappeler ce chemin pour la catégorie « {category} »")

    def _suggest_category(self):
        from core.categories import category_for
        return category_for(self.prepared.get("name", ""),
                            self.prepared.get("mime_type", ""))

    def _default_folder(self, category):
        remembered = self._remembered.get(category)
        return remembered or os.path.join(self.base_dir, category)

    def _set_default_path(self):
        category = self.category.currentData() or "Général"
        self._current_default = self._default_folder(category)
        self.save_as.setText(os.path.join(self._current_default,
                                          self.prepared.get("name") or "download"))

    def _category_changed(self):
        old_folder = self._current_default
        category = self.category.currentData() or "Général"
        next_folder = self._default_folder(category)
        current_folder = os.path.dirname(os.path.normpath(self.save_as.text()))
        if not current_folder or os.path.normcase(current_folder) == os.path.normcase(old_folder):
            self.save_as.setText(os.path.join(next_folder,
                                              os.path.basename(self.save_as.text()) or
                                              self.prepared.get("name", "download")))
        self._current_default = next_folder
        self._update_remember_label()

    def _category_user_selected(self, *_):
        self._category_touched = True

    def _add_category(self):
        name, accepted = QInputDialog.getText(self, "Nouvelle catégorie",
                                               "Nom de la catégorie :")
        name = (name or "").strip()
        if not accepted or not name:
            return
        if name in [self.category.itemData(i) for i in range(self.category.count())]:
            QMessageBox.information(self, "Catégorie", "Cette catégorie existe déjà.")
            return
        self.db.save_category(name, name, is_custom=True)
        self.category.addItem(name, name)
        self.category.setCurrentIndex(self.category.count() - 1)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Enregistrer le fichier sous", self.save_as.text(),
            "Tous les fichiers (*.*)")
        if path:
            self.save_as.setText(os.path.abspath(path))

    def _update_details(self):
        size = format_size(self.prepared.get("size"))
        name = self.prepared.get("name") or "download"
        ext = os.path.splitext(name)[1] or "Aucune"
        mime = self.prepared.get("mime_type") or "Inconnu"
        original = self.prepared.get("original_filename")
        technical = (f"    Nom technique source : {original}"
                     if original and original != name else "")
        self.details.setText(f"Nom : {name}    Extension : {ext}    "
                             f"Taille estimée : {size}    Type MIME : {mime}{technical}")

    def _path_values(self):
        full_path = os.path.abspath(os.path.normpath(self.save_as.text().strip()))
        name = clean_filename(os.path.basename(full_path))
        if not name or not os.path.dirname(full_path):
            raise ValueError("Saisis un nom de fichier et un dossier valides.")
        return os.path.join(os.path.dirname(full_path), name), name

    def _start(self):
        if not self.url_edit.text().strip().lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "URL invalide", "Saisis une URL HTTP ou HTTPS valide.")
            return
        try:
            self.path, self.name = self._path_values()
        except ValueError as exc:
            QMessageBox.warning(self, "Chemin invalide", str(exc))
            return
        if os.path.exists(self.path):
            box = QMessageBox(self)
            box.setWindowTitle("Le fichier existe déjà")
            box.setText("Un fichier portant ce nom existe déjà.")
            replace = box.addButton("Remplacer", QMessageBox.DestructiveRole)
            rename = box.addButton("Renommer automatiquement", QMessageBox.AcceptRole)
            choose = box.addButton("Choisir un autre nom…", QMessageBox.ActionRole)
            box.addButton("Annuler", QMessageBox.RejectRole)
            box.exec_()
            clicked = box.clickedButton()
            if clicked == replace:
                self.replace_existing = True
            elif clicked == rename:
                self.path = self._unused_name(self.path)
                self.name = os.path.basename(self.path)
                self.save_as.setText(self.path)
            elif clicked == choose:
                self._browse()
                return
            else:
                return
        self.choice = "start"
        self.accept()

    def apply_metadata(self, metadata):
        """Update provisional fields without replacing a name edited by the user."""
        old_name = self.prepared.get("name") or self._initial_name
        current_name = os.path.basename(self.save_as.text())
        self.prepared.update(metadata)
        self.name_label.setText(metadata.get("name") or old_name)
        info = metadata.get("video_info")
        if metadata.get("url") and self.prepared.get("_require_metadata"):
            self.start_btn.setEnabled(True)
            self.start_btn.setToolTip("")
            self.later_btn.setEnabled(True)
        if info and len(info.formats) > 1 and self.format_choice is None:
            self.format_choice = QComboBox()
            for index, video_format in enumerate(info.formats):
                self.format_choice.addItem(video_format.label or "Format", index)
            form = self.layout().itemAt(1).layout()
            form.insertRow(2, "Qualité vidéo :", self.format_choice)
        if not self._category_touched and metadata.get("name"):
            from core.categories import category_for
            suggested = category_for(metadata.get("name"), metadata.get("mime_type", ""))
            index = self.category.findData(suggested)
            if index < 0:
                self.category.addItem(suggested, suggested)
                index = self.category.count() - 1
            blocked = self.category.blockSignals(True)
            self.category.setCurrentIndex(index)
            self.category.blockSignals(blocked)
            self._category_changed()
        if not self._name_touched and current_name == old_name:
            self.save_as.setText(os.path.join(os.path.dirname(self.save_as.text()),
                                               metadata.get("name") or old_name))
        self._update_details()

    def _unused_name(self, path):
        stem, ext = os.path.splitext(path)
        index = 1
        candidate = f"{stem} ({index}){ext}"
        while os.path.exists(candidate):
            index += 1
            candidate = f"{stem} ({index}){ext}"
        return candidate

    def _later(self):
        if not self.url_edit.text().strip().lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "URL invalide", "Saisis une URL HTTP ou HTTPS valide.")
            return
        try:
            self.path, self.name = self._path_values()
        except ValueError as exc:
            QMessageBox.warning(self, "Chemin invalide", str(exc))
            return
        self.choice = "deferred"
        self.accept()

    def values(self):
        return {
            "url": self.url_edit.text().strip(), "name": self.name,
            "path": self.path, "category": self.category.currentData() or "Général",
            "description": self.description.text().strip(),
            "remember_path": self.remember.isChecked(),
            "folder": os.path.dirname(self.path), "deferred": self.choice == "deferred",
            "replace_existing": self.replace_existing,
            "format_index": self.format_choice.currentData() if self.format_choice else None,
        }


class DownloadProgressDialog(QDialog):
    """Independent top-level window with progress and terminal views for one item."""
    def __init__(self, item_id, manager, db, parent=None, finished_callback=None):
        super().__init__(parent)
        self.item_id, self.manager, self.db = item_id, manager, db
        self.finished_callback = finished_callback
        self._last_bytes = 0
        self._last_at = __import__("time").monotonic()
        self._speed = 0.0
        self._terminal_seen = False
        self._view_state = "downloading"
        self.settings = QSettings("RochXd", "FlashDL")
        # No QWidget owner: Windows must not minimize this window with the main UI.
        self.setWindowFlags(Qt.Window | Qt.WindowSystemMenuHint |
                            Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint |
                            Qt.WindowCloseButtonHint)
        self.resize(720, 570)
        root = QVBoxLayout(self)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.progress_page = QWidget()
        progress_layout = QVBoxLayout(self.progress_page)
        self.stack.addWidget(self.progress_page)
        self.tabs = QTabWidget()
        progress_layout.addWidget(self.tabs)

        status = QWidget()
        form = QFormLayout(status)
        self.url = QLabel(); self.url.setWordWrap(True)
        self.state = QLabel("En attente")
        self.size = QLabel("Taille inconnue")
        self.got = QLabel("0 o")
        self.speed = QLabel("0.00 Ko/s")
        self.eta = QLabel("—")
        self.resume = QLabel("En cours de vérification")
        for label, widget in (("URL :", self.url), ("Statut :", self.state),
                              ("Taille fichier :", self.size), ("Téléchargé :", self.got),
                              ("Vitesse de télécharg. :", self.speed),
                              ("Temps restant :", self.eta), ("Reprise :", self.resume)):
            form.addRow(label, widget)
        self.tabs.addTab(status, "Statut téléchargement")

        limit = QWidget(); limit_form = QVBoxLayout(limit)
        self.limit_enabled = QCheckBox("Activer une limite personnalisée")
        self.limit_amount = QSpinBox(); self.limit_amount.setRange(1, 1000000)
        self.limit_unit = QComboBox(); self.limit_unit.addItems(["Ko/s", "Mo/s"])
        row = QHBoxLayout(); row.addWidget(self.limit_amount); row.addWidget(self.limit_unit)
        row.addStretch()
        limit_form.addWidget(QLabel("Aucune limite par défaut. La limite s’applique réellement au moteur."))
        limit_form.addWidget(self.limit_enabled); limit_form.addLayout(row); limit_form.addStretch()
        self.limit_enabled.toggled.connect(self._apply_limit)
        self.limit_amount.valueChanged.connect(self._apply_limit)
        self.limit_unit.currentIndexChanged.connect(self._apply_limit)
        self.tabs.addTab(limit, "Limiteur de vitesse")

        options = QWidget(); options_form = QVBoxLayout(options)
        self.on_complete = QComboBox()
        self.on_complete.addItem("Ne rien faire", "none")
        self.on_complete.addItem("Afficher une notification", "notify")
        self.on_complete.addItem("Ouvrir le fichier téléchargé", "open")
        self.on_complete.addItem("Ouvrir le dossier contenant le fichier", "folder")
        self.on_complete.addItem("Fermer la fenêtre de progression", "close")
        saved = self.settings.value("completion_action", "none")
        self.on_complete.setCurrentIndex(max(0, self.on_complete.findData(saved)))
        self.on_complete.currentIndexChanged.connect(
            lambda: self.settings.setValue("completion_action", self.on_complete.currentData()))
        options_form.addWidget(QLabel("Action après un transfert vérifié :"))
        options_form.addWidget(self.on_complete); options_form.addStretch()
        self.tabs.addTab(options, "Options à l'achèvement")

        self.progress = QProgressBar(); self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress_percent = QLabel("Progression indéterminée")
        self.progress_percent.setMinimumWidth(130)
        self.progress_percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.progress.setStyleSheet(
            "QProgressBar{background:#e5e7eb;border:0;border-radius:5px;"
            "text-align:center;color:#17212b}"
            "QProgressBar::chunk{background:#24a148;border-radius:5px}")
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress, 1)
        progress_row.addWidget(self.progress_percent)
        progress_layout.addLayout(progress_row)
        self.details_box = QGroupBox("Démarrage des positions et progression du téléchargement par les connexions")
        details_layout = QVBoxLayout(self.details_box)
        self.segment_visual = QHBoxLayout()
        details_layout.addLayout(self.segment_visual)
        self.segments = QTableWidget(0, 3)
        self.segments.setHorizontalHeaderLabels(["N.", "Téléchargé", "Info"])
        self.segments.horizontalHeader().setStretchLastSection(True)
        self.segments.verticalHeader().setVisible(False)
        details_layout.addWidget(self.segments)
        progress_layout.addWidget(self.details_box)
        self.toggle_details = QPushButton("Masquer les détails")
        self.toggle_details.setCheckable(True)
        self.toggle_details.toggled.connect(self._toggle_details)
        self.toggle_details.setChecked(bool(self.settings.value("details_hidden", False, type=bool)))
        controls = QHBoxLayout()
        self.pause_btn = QPushButton("Pause")
        self.cancel_btn = QPushButton("Annuler")
        controls.addWidget(self.toggle_details); controls.addStretch()
        controls.addWidget(self.pause_btn); controls.addWidget(self.cancel_btn)
        progress_layout.addLayout(controls)
        self.pause_btn.clicked.connect(self._pause_resume)
        self.cancel_btn.clicked.connect(self._cancel)

        self.completed_page = QWidget()
        completed_layout = QVBoxLayout(self.completed_page)
        self.completed_title = QLabel("Téléchargement terminé")
        self.completed_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        completed_layout.addWidget(self.completed_title)
        self.completed_info = QFormLayout()
        self.final_name = QLabel(); self.final_name.setWordWrap(True)
        self.final_size = QLabel(); self.final_category = QLabel()
        self.final_path = QLabel(); self.final_path.setWordWrap(True)
        self.final_status = QLabel("Fichier vérifié et finalisé")
        for label, widget in (("Nom final :", self.final_name),
                              ("Taille finale :", self.final_size),
                              ("Catégorie :", self.final_category),
                              ("Destination :", self.final_path),
                              ("Statut :", self.final_status)):
            self.completed_info.addRow(label, widget)
        completed_layout.addLayout(self.completed_info)
        completed_layout.addStretch(1)
        completed_buttons = QHBoxLayout()
        self.open_file_btn = QPushButton("Ouvrir")
        self.open_folder_btn = QPushButton("Ouvrir le dossier")
        self.close_completed_btn = QPushButton("Fermer")
        completed_buttons.addStretch(1)
        completed_buttons.addWidget(self.open_file_btn)
        completed_buttons.addWidget(self.open_folder_btn)
        completed_buttons.addWidget(self.close_completed_btn)
        completed_layout.addLayout(completed_buttons)
        self.stack.addWidget(self.completed_page)
        self.open_file_btn.clicked.connect(self._open_file)
        self.open_folder_btn.clicked.connect(self._open_folder)
        self.close_completed_btn.clicked.connect(self.close)

        self.failed_page = QWidget()
        failed_layout = QVBoxLayout(self.failed_page)
        self.failed_title = QLabel("Téléchargement échoué")
        self.failed_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.failed_message = QLabel(); self.failed_message.setWordWrap(True)
        failed_layout.addWidget(self.failed_title)
        failed_layout.addWidget(self.failed_message)
        failed_layout.addStretch(1)
        failed_buttons = QHBoxLayout()
        self.retry_btn = QPushButton("Reprendre")
        self.close_failed_btn = QPushButton("Fermer")
        failed_buttons.addStretch(1); failed_buttons.addWidget(self.retry_btn)
        failed_buttons.addWidget(self.close_failed_btn)
        failed_layout.addLayout(failed_buttons)
        self.stack.addWidget(self.failed_page)
        self.retry_btn.clicked.connect(self._retry)
        self.close_failed_btn.clicked.connect(self.close)

        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(250)
        self.refresh()

    @staticmethod
    def _size(value):
        if value is None: return "Inconnue"
        value = float(value)
        for unit in ("o", "Ko", "Mo", "Go", "To"):
            if value < 1024 or unit == "To":
                return f"{value:.2f} {unit}" if unit != "o" else f"{value:.0f} o"
            value /= 1024

    @staticmethod
    def _speed_text(value):
        # Keep speed labels readable during slow or bursty transfers; raw
        # bytes-per-second labels made the apparent unit jump too often.
        value = max(0.0, float(value or 0)) / 1024
        for unit in ("Ko/s", "Mo/s", "Go/s", "To/s"):
            if value < 1024 or unit == "To/s":
                return f"{value:.2f} {unit}"
            value /= 1024

    def _toggle_details(self, hidden):
        self.details_box.setVisible(not hidden)
        self.toggle_details.setText("Afficher les détails" if hidden else "Masquer les détails")
        self.settings.setValue("details_hidden", hidden)

    def _apply_limit(self, *_):
        if not hasattr(self.manager, "limiter") or self.manager.limiter is None: return
        rate = 0
        if self.limit_enabled.isChecked():
            rate = self.limit_amount.value() * (1024 if self.limit_unit.currentIndex() == 0 else 1024 * 1024)
        self.manager.limiter.set_rate(rate)

    def _pause_resume(self):
        item = self.manager.items.get(self.item_id)
        if not item: return
        if item.state == "paused": self.manager.resume(self.item_id)
        elif item.state == "active": self.manager.pause(self.item_id)

    def _cancel(self):
        answer = QMessageBox.question(self, "Annuler le téléchargement",
            "Arrêter ce transfert ? Les données partielles récupérables seront conservées.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.manager.cancel(self.item_id)

    def _open_file(self):
        item = self.manager.items.get(self.item_id)
        if item and os.path.isfile(item.dest):
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.dest))

    def _open_folder(self):
        item = self.manager.items.get(self.item_id)
        folder = os.path.dirname(item.dest) if item else ""
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _retry(self):
        self.manager.resume(self.item_id)
        self._view_state = "downloading"
        self._terminal_seen = False
        self._last_bytes = 0
        self._last_at = __import__("time").monotonic()
        self._speed = 0.0
        self.stack.setCurrentWidget(self.progress_page)
        self.timer.start(250)

    def _show_completed(self, item, total):
        row = self.db.get(self.item_id) or {}
        actual_size = os.path.getsize(item.dest)
        self.final_name.setText(os.path.basename(item.dest))
        self.final_size.setText(self._size(actual_size))
        self.final_category.setText(item.category or row.get("category") or "Général")
        self.final_path.setText(item.dest)
        self.open_file_btn.setEnabled(os.path.isfile(item.dest))
        self.open_folder_btn.setEnabled(os.path.isdir(os.path.dirname(item.dest)))
        self._view_state = "completed"
        self.setWindowTitle("Téléchargement terminé — " + os.path.basename(item.dest))
        self.stack.setCurrentWidget(self.completed_page)
        if self.finished_callback:
            self.finished_callback(item, self.on_complete.currentData())

    def _show_failed(self, item):
        self._view_state = "cancelled" if item.state == "cancelled" else "failed"
        self.failed_title.setText("Téléchargement annulé" if item.state == "cancelled"
                                  else "Téléchargement échoué")
        self.failed_message.setText(getattr(item.dl, "error", None) or
                                    ("Le fichier est incomplet; les données récupérables sont conservées."
                                     if item.state == "cancelled" else "Une erreur a interrompu le transfert."))
        self.retry_btn.setVisible(item.state == "error")
        self.setWindowTitle(self.failed_title.text() + " — " + item.name)
        self.stack.setCurrentWidget(self.failed_page)

    def refresh(self):
        item = self.manager.items.get(self.item_id)
        if not item: return
        row = self.db.get(self.item_id) or {}
        downloaded = getattr(item.dl, "downloaded", row.get("downloaded") or 0)
        total = getattr(item.dl, "total", 0) or row.get("size")
        total_estimated = bool(getattr(item.dl, "total_is_estimated", False))
        if item.state == "done" and os.path.isfile(item.dest):
            total = os.path.getsize(item.dest)
            total_estimated = False
        now = __import__("time").monotonic(); elapsed = now - self._last_at
        if elapsed > 0:
            instant = max(0, downloaded - self._last_bytes) / elapsed
            alpha = 1 - math.exp(-elapsed / 1.0)
            self._speed += alpha * (instant - self._speed)
        self._last_bytes, self._last_at = downloaded, now
        self.setWindowTitle(item.name)
        self.url.setText(item.initial_url or item.url)
        self.size.setText(("Estimée : " if total_estimated else "") + self._size(total))
        self.got.setText(self._size(downloaded))
        self.speed.setText(self._speed_text(self._speed))
        self.resume.setText("Oui" if getattr(item.dl, "_ranged", False) or item.format_id else
                            "À confirmer par le serveur")
        status = {"active": "Réception des données...", "paused": "En pause",
                  "done": "Téléchargement terminé", "error": "Échec du téléchargement",
                  "cancelling": "Annulation en cours", "cancelled": "Téléchargement incomplet"}.get(item.state, item.state)
        engine_state = getattr(item.dl, "status", "")
        if item.state == "active" and engine_state == "verifying":
            status = "Vérification des segments et de la taille..."
            self._view_state = "verifying"
        elif item.state == "active" and engine_state in ("assembling", "merging"):
            status = "Assemblage des segments..."
            self._view_state = "assembling"
        elif item.state == "active" and engine_state == "finalizing":
            status = "Finalisation du fichier..."
            self._view_state = "finalizing"
        elif item.state == "active":
            self._view_state = "downloading"
        elif item.state == "paused":
            self._view_state = "paused"
        elif item.state == "cancelling":
            self._view_state = "cancelling"
        elif item.state == "cancelled":
            self._view_state = "cancelled"
        if item.state == "error" and getattr(item.dl, "error", None):
            error = item.dl.error
            if any(term in error.lower() for term in ("incomplet", "taille", "hash", "plage", "corrompu")):
                status = "Vérification échouée"
            status += " — " + error
        self.state.setText(status)
        if total:
            self.progress.setRange(0, 100); self.progress.setValue(min(99, int(downloaded * 100 / total)) if item.state != "done" else 100)
            pct = downloaded * 100 / total
            shown_pct = 100.0 if item.state == "done" else min(99.99, max(0.0, pct))
            self.progress_percent.setText(("≈ " if total_estimated else "") +
                                           f"{shown_pct:.2f} %" +
                                           (" estimés" if total_estimated else ""))
            self.got.setText(f"{self._size(downloaded)} ({shown_pct:.2f} %"
                             f"{' estimés' if total_estimated else ''})"
                             if item.state != "done" else f"{self._size(downloaded)} (100 %)" )
            remaining = int((total-downloaded)/self._speed) if self._speed > 0 else None
            self.eta.setText((f"{remaining // 60} min {remaining % 60:02d} s"
                              if remaining is not None else "—"))
        else:
            self.progress.setRange(0, 0 if item.state in ("active", "queued") else 100)
            if item.state not in ("active", "queued"): self.progress.setValue(0)
            self.progress_percent.setText("Progression indéterminée")
            self.eta.setText("Indisponible (taille inconnue)" if item.state == "done" else "—")
        self.pause_btn.setText("Reprendre" if item.state == "paused" else "Pause")
        self.pause_btn.setEnabled(item.state in ("active", "paused", "queued"))
        segments = item.dl.segment_snapshot() if hasattr(item.dl, "segment_snapshot") else [
            {"i": 0, "downloaded": downloaded, "total": total,
             "state": "Réception des données..." if item.state == "active" else status}]
        while self.segment_visual.count():
            child = self.segment_visual.takeAt(0)
            if child.widget(): child.widget().deleteLater()
        for segment in segments:
            bar = QProgressBar(); bar.setTextVisible(False); bar.setFixedHeight(12)
            bar.setStyleSheet(
                "QProgressBar{background:#e5e7eb;border:0;border-radius:3px}"
                "QProgressBar::chunk{background:#24a148;border-radius:3px}")
            part_total = segment.get("total")
            if part_total:
                bar.setRange(0, 100); bar.setValue(min(100, int(segment["downloaded"] * 100 / part_total)))
            else:
                bar.setRange(0, 0 if item.state == "active" else 100)
            self.segment_visual.addWidget(bar, 1)
        self.segments.setRowCount(len(segments))
        for n, segment in enumerate(segments):
            vals = [str(n+1), self._size(segment.get("downloaded", 0)), segment.get("state", "")]
            for col, val in enumerate(vals): self.segments.setItem(n, col, QTableWidgetItem(val))
        if item.state in ("done", "error", "cancelled") and not self._terminal_seen:
            self._terminal_seen = True
            self.timer.stop()
            if item.state == "done":
                # QueueManager reaches done only after merge, size checks and DB update.
                actual_size = os.path.getsize(item.dest) if os.path.isfile(item.dest) else None
                expected_size = row.get("size") or total
                valid = (actual_size is not None and
                         (not expected_size or actual_size == expected_size))
                if valid:
                    self._show_completed(item, total)
                else:
                    self._show_failed(item)
            else:
                self._show_failed(item)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

class MoveRenameDialog(QDialog):
    def __init__(self, current_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Déplacer / Renommer")
        self.resize(560, 110)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        self.path_edit = QLineEdit(current_path)
        browse = QPushButton("…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path_edit, 1)
        row.addWidget(browse)
        layout.addWidget(QLabel("Nouveau nom et dossier :"))
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Déplacer / Renommer",
                                              self.path_edit.text(), "Tous les fichiers (*.*)")
        if path:
            self.path_edit.setText(path)

    def path(self):
        return os.path.abspath(os.path.normpath(self.path_edit.text().strip()))


class UrlUpdateDialog(QDialog):
    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Actualiser l’adresse du téléchargement")
        self.resize(600, 120)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Nouvelle URL temporaire ou signée :"))
        self.url_edit = QLineEdit(url or "")
        layout.addWidget(self.url_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def url(self):
        return self.url_edit.text().strip()


class PropertiesDialog(QDialog):
    def __init__(self, row, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Propriétés du téléchargement")
        self.resize(620, 480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        metrics = row.get("transfer_metrics") or {}
        fields = (
            ("Nom", row.get("name")),
            ("Extension", row.get("extension") or os.path.splitext(row.get("name") or "")[1]),
            ("URL d’origine", row.get("initial_url") or row.get("url")),
            ("URL actuelle", row.get("url")),
            ("Catégorie", row.get("category")),
            ("Type MIME", row.get("mime_type") or "Inconnu"),
            ("Taille totale", format_size(row.get("size"))),
            ("Taille téléchargée", format_size(row.get("downloaded"))),
            ("État", row.get("status")),
            ("Erreur", row.get("error") or "Aucune"),
            ("Chemin complet", row.get("path")),
            ("Ajouté le", row.get("added_at")),
            ("Début", row.get("started_at")),
            ("Fin", row.get("finished_at")),
            ("Durée", self._duration(row.get("duration"))),
            ("Vitesse moyenne", self._speed(row.get("average_speed"))),
            ("Vitesse actuelle", self._speed(row.get("current_speed"))),
            ("Débit moyen reçu (moteur)", self._speed(metrics.get("average_bps"))),
            ("Octets reçus pendant cette session", format_size(metrics.get("network_bytes"))),
            ("Durée de session mesurée", self._duration(metrics.get("elapsed_seconds"))),
            ("Attente des données réseau", self._duration(metrics.get("server_wait_seconds"))),
            ("Temps d’écriture/assemblage disque", self._duration(metrics.get("disk_write_seconds"))),
            ("Segments actifs / total",
             f"{metrics.get('active_segments', 0)} / {metrics.get('segment_count', 0)}"),
            ("Tentatives", row.get("attempts", 0)),
            ("Reprise", row.get("resume_info") or "Non vérifiée"),
        )
        for label, value in fields:
            line = QLineEdit(str(value or ""))
            line.setReadOnly(True)
            form.addRow(label + ":", line)
            if label.startswith("URL") or label == "Chemin complet":
                copy_btn = QPushButton("Copier")
                copy_btn.clicked.connect(lambda checked=False, v=str(value or ""):
                                         QApplication.clipboard().setText(v))
                line_row = QHBoxLayout()
                line_row.addWidget(line, 1)
                line_row.addWidget(copy_btn)
                form.removeRow(form.rowCount() - 1)
                form.addRow(label + ":", line_row)
        layout.addLayout(form)
        layout.addWidget(QLabel("Description :"))
        description = QPlainTextEdit(row.get("description") or "")
        description.setReadOnly(True)
        description.setMaximumHeight(70)
        layout.addWidget(description)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        close.accepted.connect(self.accept)
        layout.addWidget(close)

    @staticmethod
    def _duration(seconds):
        if seconds is None:
            return "Inconnue"
        seconds = max(0, int(seconds))
        return f"{seconds // 3600} h {(seconds % 3600) // 60} min {seconds % 60} s"

    @staticmethod
    def _speed(value):
        return "Inconnue" if value is None else f"{value / 1024 / 1024:.2f} Mo/s"
