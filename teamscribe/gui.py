"""Desktop widget for TeamScribe.

A small, frameless, always-on-top window: start/stop recording with one
click, see a live "listening" indicator, browse recent sessions, and
re-run summarize / push-tasks without touching a terminal. Launch with
``teamscribe gui``.

Long-running work (record/transcribe/summarize/planner) runs in a
QThread so the UI never blocks; only Qt signals cross back to the main
thread to update widgets.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, Qt, QThread, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizeGrip,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import config, shortcuts


# --------------------------------------------------------------------------
# Background workers
# --------------------------------------------------------------------------

class RecordWorker(QThread):
    tick = Signal(float)
    info = Signal(str, str)
    finished_ok = Signal(Path)
    failed = Signal(str)

    def __init__(self, do_summarize: bool):
        super().__init__()
        self.do_summarize = do_summarize
        self.stop_event = threading.Event()
        self._shown_info = False

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        from . import capture, summarize as summarize_mod, transcribe

        try:
            session = config.new_session_dir()
            audio_path = session / "audio.wav"

            def on_tick(elapsed, mic_name, speaker_name):
                if not self._shown_info:
                    self._shown_info = True
                    self.info.emit(mic_name, speaker_name)
                self.tick.emit(elapsed)

            capture.record(
                audio_path,
                config.max_minutes(),
                on_tick=on_tick,
                stop_event=self.stop_event,
            )
            result = transcribe.transcribe(audio_path, session, log=lambda *_: None)
            if self.do_summarize:
                try:
                    summarize_mod.summarize_session(session, log=lambda *_: None)
                except Exception:
                    pass  # summary is best-effort; recording still succeeded

            from . import naming

            session = naming.finalize_session(session, result["text"], log=lambda *_: None)
            self.finished_ok.emit(session)
        except Exception as exc:
            self.failed.emit(str(exc))


class TaskWorker(QThread):
    """Runs a single no-arg callable off the UI thread (summarize/push)."""

    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self) -> None:
        try:
            result = self.fn()
            self.finished_ok.emit(result or "")
        except Exception as exc:
            self.failed.emit(str(exc))


# --------------------------------------------------------------------------
# Settings dialog
# --------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paramètres — TeamScribe")
        self.settings = config.load_gui_settings()

        layout = QVBoxLayout(self)

        self.startup_box = QCheckBox("Lancer au démarrage de Windows")
        self.startup_box.setChecked(shortcuts.is_startup_enabled())
        layout.addWidget(self.startup_box)

        self.summarize_box = QCheckBox("Résumer automatiquement après l'enregistrement")
        self.summarize_box.setChecked(self.settings.get("auto_summarize", True))
        layout.addWidget(self.summarize_box)

        self.ontop_box = QCheckBox("Toujours par-dessus les autres fenêtres")
        self.ontop_box.setChecked(self.settings.get("always_on_top", True))
        layout.addWidget(self.ontop_box)

        layout.addWidget(QLabel("Thème :"))
        theme_row = QHBoxLayout()
        self.dark_radio = QRadioButton("Sombre")
        self.light_radio = QRadioButton("Clair")
        theme_group = QButtonGroup(self)
        theme_group.addButton(self.dark_radio)
        theme_group.addButton(self.light_radio)
        if self.settings.get("theme", "dark") == "light":
            self.light_radio.setChecked(True)
        else:
            self.dark_radio.setChecked(True)
        theme_row.addWidget(self.dark_radio)
        theme_row.addWidget(self.light_radio)
        layout.addLayout(theme_row)

        self.glass_label = QLabel()
        layout.addWidget(self.glass_label)
        self.glass_slider = QSlider(Qt.Horizontal)
        self.glass_slider.setRange(20, 100)
        self.glass_slider.setValue(self.settings.get("glass_opacity", 100))
        self.glass_slider.valueChanged.connect(self._update_glass_label)
        self._update_glass_label(self.glass_slider.value())
        layout.addWidget(self.glass_slider)

        desktop_btn = QPushButton("Créer un raccourci sur le bureau")
        desktop_btn.clicked.connect(self._create_desktop_shortcut)
        layout.addWidget(desktop_btn)

        devices_btn = QPushButton("Vérifier l'audio")
        devices_btn.clicked.connect(self._run_selftest)
        layout.addWidget(devices_btn)

        buttons_row = QHBoxLayout()
        save_btn = QPushButton("Enregistrer")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Annuler")
        cancel_btn.clicked.connect(self.reject)
        buttons_row.addWidget(save_btn)
        buttons_row.addWidget(cancel_btn)
        layout.addLayout(buttons_row)

    def _update_glass_label(self, value: int) -> None:
        self.glass_label.setText(f"Effet verre liquide (transparence) : {value}%")

    def _create_desktop_shortcut(self) -> None:
        try:
            path = shortcuts.create_desktop_shortcut()
        except Exception as exc:
            QMessageBox.warning(self, "TeamScribe", f"Échec de la création :\n{exc}")
            return
        QMessageBox.information(self, "TeamScribe", f"Raccourci créé :\n{path}")

    def _run_selftest(self) -> None:
        from . import capture

        try:
            info = capture.list_devices()
        except RuntimeError as exc:
            QMessageBox.warning(self, "TeamScribe", str(exc))
            return
        msg = (
            f"Haut-parleurs : {info['default_speakers']['name']}\n"
            f"Micro : {info['default_mic']['name']}\n"
            f"Périphériques loopback détectés : {len(info['loopback_devices'])}"
        )
        QMessageBox.information(self, "Audio", msg)

    def accept(self) -> None:
        try:
            shortcuts.set_startup_enabled(self.startup_box.isChecked())
        except Exception as exc:
            QMessageBox.warning(
                self, "TeamScribe", f"Échec de la mise à jour du démarrage automatique :\n{exc}"
            )
            return

        self.settings["auto_summarize"] = self.summarize_box.isChecked()
        self.settings["always_on_top"] = self.ontop_box.isChecked()
        self.settings["theme"] = "light" if self.light_radio.isChecked() else "dark"
        self.settings["glass_opacity"] = self.glass_slider.value()
        config.save_gui_settings(self.settings)
        super().accept()


# --------------------------------------------------------------------------
# Widget
# --------------------------------------------------------------------------

class TeamScribeWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TeamScribe")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(300, 420)

        self._drag_offset = None
        self._pinned = False
        self._record_worker: RecordWorker | None = None
        self._task_worker: TaskWorker | None = None
        self.settings = config.load_gui_settings()

        self._build_ui()
        self._apply_always_on_top(self.settings.get("always_on_top", True))
        self._apply_theme()
        self.pin_btn.setChecked(self.settings.get("pinned", False))
        self._restore_position()
        self.refresh_sessions()

    # -- position memory --------------------------------------------------

    def _position_is_visible(self, x: int, y: int) -> bool:
        point = QPoint(x, y)
        for screen in QGuiApplication.screens():
            # A small margin so a widget mostly off-screen (but with its
            # corner still reachable) still counts as visible.
            if screen.availableGeometry().adjusted(-50, -50, 50, 50).contains(point):
                return True
        return False

    def _move_to_corner(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        margin = 20
        self.move(geo.right() - self.width() - margin, geo.top() + margin)

    def _restore_position(self) -> None:
        x = self.settings.get("pos_x")
        y = self.settings.get("pos_y")
        if x is not None and y is not None and self._position_is_visible(x, y):
            self.move(x, y)
        else:
            self._move_to_corner()

    def _save_position(self) -> None:
        self.settings["pos_x"] = self.x()
        self.settings["pos_y"] = self.y()
        config.save_gui_settings(self.settings)

    def closeEvent(self, event) -> None:
        self._save_position()
        super().closeEvent(event)

    # -- chrome / dragging --------------------------------------------------

    @staticmethod
    def _stylesheet(theme: str, glass_opacity: int = 100) -> str:
        # "Liquid glass": the outer panel background gets an alpha channel so
        # the desktop shows through behind it, while buttons/list/text stay
        # fully opaque so the UI remains readable.
        alpha = max(0, min(100, glass_opacity)) / 100 * 255
        if theme == "light":
            root_bg = f"rgba(242, 242, 242, {alpha:.0f})"
            return (
                f"#root {{ background: {root_bg}; border: 1px solid #c8c8c8; border-radius: 8px; }}"
                "QLabel { color: #1e1e1e; background: transparent; }"
                "QPushButton { background: #ffffff; color: #1e1e1e; border: 1px solid #bbb;"
                " border-radius: 4px; padding: 6px; }"
                "QPushButton:hover { background: #e6e6e6; }"
                "QPushButton:disabled { color: #999; }"
                "QListWidget { background: #ffffff; color: #1e1e1e; border: 1px solid #ccc; }"
            )
        root_bg = f"rgba(30, 30, 30, {alpha:.0f})"
        return (
            f"#root {{ background: {root_bg}; border: 1px solid #3a3a3a; border-radius: 8px; }}"
            "QLabel { color: #e0e0e0; background: transparent; }"
            "QPushButton { background: #2d2d2d; color: #e0e0e0; border: 1px solid #444;"
            " border-radius: 4px; padding: 6px; }"
            "QPushButton:hover { background: #3a3a3a; }"
            "QPushButton:disabled { color: #777; }"
            "QListWidget { background: #181818; color: #d0d0d0; border: 1px solid #333; }"
        )

    def _apply_theme(self) -> None:
        theme = self.settings.get("theme", "dark")
        glass_opacity = self.settings.get("glass_opacity", 100)
        self.root_frame.setStyleSheet(self._stylesheet(theme, glass_opacity))

    def _build_ui(self) -> None:
        root = QFrame(self)
        root.setObjectName("root")
        self.root_frame = root
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 10)

        # Title bar (drag handle + close)
        title_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setStyleSheet("color: #666; font-size: 14px;")
        title = QLabel("TeamScribe")
        title.setStyleSheet("font-weight: bold;")
        self.pin_btn = QPushButton("📌")
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setStyleSheet("font-size: 13px;")
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("Épingler (bloquer le déplacement)")
        self.pin_btn.toggled.connect(self.set_pinned)
        settings_btn = QPushButton("⚙️")
        settings_btn.setFixedSize(24, 24)
        settings_btn.setStyleSheet("font-size: 15px;")
        settings_btn.setToolTip("Paramètres")
        settings_btn.clicked.connect(self.open_settings)
        restart_btn = QPushButton("⟳")
        restart_btn.setFixedSize(22, 22)
        restart_btn.setToolTip("Redémarrer l'application")
        restart_btn.clicked.connect(self.restart_app)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.close)
        title_row.addWidget(self.pin_btn)
        title_row.addWidget(settings_btn)
        title_row.addWidget(self.status_dot)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(restart_btn)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        self.status_label = QLabel("Prêt")
        self.status_label.setStyleSheet("color: #999;")
        layout.addWidget(self.status_label)

        # Record button
        self.record_btn = QPushButton("● Démarrer l'enregistrement")
        self.record_btn.clicked.connect(self.toggle_recording)
        self._set_record_btn_color(recording=False)
        layout.addWidget(self.record_btn)

        layout.addWidget(QLabel("Sessions récentes :"))
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.session_list.itemDoubleClicked.connect(self._open_session_folder)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._show_session_menu)
        self.session_list.installEventFilter(self)
        layout.addWidget(self.session_list)

        actions_row = QHBoxLayout()
        self.summarize_btn = QPushButton("Résumer")
        self.summarize_btn.clicked.connect(self.run_summarize)
        self.push_btn = QPushButton("Pousser → Planner")
        self.push_btn.clicked.connect(self.run_push_tasks)
        actions_row.addWidget(self.summarize_btn)
        actions_row.addWidget(self.push_btn)
        layout.addLayout(actions_row)

        bottom_row = QHBoxLayout()
        refresh_btn = QPushButton("Rafraîchir la liste")
        refresh_btn.clicked.connect(self.refresh_sessions)
        open_folder_btn = QPushButton("Ouvrir dossier")
        open_folder_btn.clicked.connect(self.open_sessions_folder)
        bottom_row.addWidget(refresh_btn)
        bottom_row.addWidget(open_folder_btn)
        layout.addLayout(bottom_row)

        # Frameless windows have no native resize border, so a visible grip
        # in the corner is what lets the user actually resize the widget.
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)
        layout.addLayout(grip_row)

        self.setMinimumSize(240, 320)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._pinned and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_btn.setStyleSheet(
            "font-size: 13px; background: #2e8b3d; border: 1px solid #3fae52;"
            if pinned else "font-size: 13px;"
        )
        self.pin_btn.setToolTip(
            "Désépingler (autoriser le déplacement)" if pinned else "Épingler (bloquer le déplacement)"
        )
        self.settings["pinned"] = pinned
        config.save_gui_settings(self.settings)

    # -- settings --------------------------------------------------

    def _apply_always_on_top(self, enabled: bool) -> None:
        flags = Qt.FramelessWindowHint | Qt.Tool
        if enabled:
            flags |= Qt.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.settings = config.load_gui_settings()
            self._apply_always_on_top(self.settings.get("always_on_top", True))
            self._apply_theme()

    def restart_app(self) -> None:
        if self._record_worker is not None:
            QMessageBox.information(
                self, "TeamScribe",
                "Arrête l'enregistrement en cours avant de redémarrer."
            )
            return
        self._save_position()
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        exe = str(pythonw) if pythonw.is_file() else str(python)
        subprocess.Popen([exe, "-m", "teamscribe.cli", "gui"], cwd=str(config.ROOT))
        QApplication.quit()

    # -- sessions list --------------------------------------------------

    def open_sessions_folder(self) -> None:
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(config.SESSIONS_DIR))

    def refresh_sessions(self) -> None:
        self.session_list.clear()
        if not config.SESSIONS_DIR.is_dir():
            return
        hidden = set(self.settings.get("hidden_sessions", []))
        sessions = sorted(
            (p for p in config.SESSIONS_DIR.glob("*") if p.is_dir() and p.name not in hidden),
            key=lambda p: p.name,
            reverse=True,
        )
        for path in sessions[:20]:
            # summary.json keeps a fixed name regardless of the contextual
            # rename in naming.finalize_session, so it's a stable signal.
            has_summary = (path / "summary.json").is_file()
            label = f"{'✓' if has_summary else '…'}  {path.name}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(path))
            self.session_list.addItem(item)

    def _selected_session(self) -> Path | None:
        item = self.session_list.currentItem()
        if item is None:
            return None
        return Path(item.data(Qt.UserRole))

    def _selected_sessions(self) -> list[Path]:
        return [Path(item.data(Qt.UserRole)) for item in self.session_list.selectedItems()]

    def _open_session_folder(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.UserRole))
        notes = list(path.glob("*_notes.md")) or list(path.glob("summary.md"))
        target = notes[0] if notes else path
        webbrowser.open(target.as_uri() if target.is_file() else str(target))

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.session_list
            and event.type() == QEvent.KeyPress
            and event.key() in (Qt.Key_Delete, Qt.Key_Backspace)
        ):
            self._delete_selected_sessions()
            return True
        return super().eventFilter(obj, event)

    def _show_session_menu(self, pos) -> None:
        item = self.session_list.itemAt(pos)
        if item is None:
            return
        # Keep the existing multi-selection (shift/ctrl-click) intact; only
        # fall back to selecting just this item if it isn't already part of
        # the current selection (e.g. a right-click outside any selection).
        if item not in self.session_list.selectedItems():
            self.session_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Supprimer…", self._delete_selected_sessions)
        menu.exec(self.session_list.mapToGlobal(pos))

    def _delete_selected_sessions(self) -> None:
        paths = self._selected_sessions()
        if not paths:
            return

        if len(paths) == 1:
            names = f"« {paths[0].name} »"
        else:
            names = f"ces {len(paths)} sessions"

        box = QMessageBox(self)
        box.setWindowTitle("Supprimer la session")
        box.setText(f"Que faire avec {names} ?")
        box.setIcon(QMessageBox.Question)
        widget_btn = box.addButton("Retirer du widget seulement", QMessageBox.ActionRole)
        delete_btn = box.addButton("Supprimer le(s) dossier(s) complet(s)", QMessageBox.DestructiveRole)
        box.addButton("Annuler", QMessageBox.RejectRole)
        box.setDefaultButton(widget_btn)
        box.exec()
        clicked = box.clickedButton()

        if clicked is widget_btn:
            hidden = self.settings.setdefault("hidden_sessions", [])
            for path in paths:
                if path.name not in hidden:
                    hidden.append(path.name)
            config.save_gui_settings(self.settings)
            self.refresh_sessions()
        elif clicked is delete_btn:
            confirm = QMessageBox.warning(
                self,
                "Confirmer la suppression",
                f"Supprimer définitivement {names} et tout leur contenu "
                "(audio, transcription, résumé) ? Cette action est irréversible.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
            hidden = self.settings.get("hidden_sessions", [])
            errors = []
            for path in paths:
                try:
                    shutil.rmtree(path)
                except OSError as exc:
                    errors.append(f"{path.name} : {exc}")
                    continue
                if path.name in hidden:
                    hidden.remove(path.name)
            config.save_gui_settings(self.settings)
            self.refresh_sessions()
            if errors:
                QMessageBox.warning(
                    self, "TeamScribe", "Échec de la suppression :\n" + "\n".join(errors)
                )

    # -- recording --------------------------------------------------

    def _set_record_btn_color(self, recording: bool) -> None:
        if recording:
            self.record_btn.setStyleSheet(
                "background: #c0392b; color: white; border: 1px solid #e74c3c;"
                " border-radius: 4px; padding: 6px;"
            )
        else:
            self.record_btn.setStyleSheet(
                "background: #2e8b3d; color: white; border: 1px solid #3fae52;"
                " border-radius: 4px; padding: 6px;"
            )

    def toggle_recording(self) -> None:
        if self._record_worker is not None:
            self.status_label.setText("Arrêt en cours…")
            self.record_btn.setEnabled(False)
            self._record_worker.stop()
            return

        self._record_worker = RecordWorker(
            do_summarize=self.settings.get("auto_summarize", True)
        )
        self._record_worker.tick.connect(self._on_tick)
        self._record_worker.info.connect(self._on_info)
        self._record_worker.finished_ok.connect(self._on_record_done)
        self._record_worker.failed.connect(self._on_record_failed)
        self._record_worker.start()

        self.record_btn.setText("■ Arrêter l'enregistrement")
        self._set_record_btn_color(recording=True)
        self.status_dot.setStyleSheet("color: #e53935; font-size: 14px;")
        self.status_label.setText("À l'écoute… 00:00:00")

    def _on_tick(self, elapsed: float) -> None:
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        self.status_label.setText(f"À l'écoute… {h:02d}:{m:02d}:{s:02d}")

    def _on_info(self, mic_name: str, speaker_name: str) -> None:
        self.status_label.setToolTip(f"mic: {mic_name}\nspeakers: {speaker_name}")

    def _on_record_done(self, session: Path) -> None:
        self._record_worker = None
        self.record_btn.setEnabled(True)
        self.record_btn.setText("● Démarrer l'enregistrement")
        self._set_record_btn_color(recording=False)
        self.status_dot.setStyleSheet("color: #666; font-size: 14px;")
        self.status_label.setText(f"Terminé : {session.name}")
        self.refresh_sessions()

    def _on_record_failed(self, message: str) -> None:
        self._record_worker = None
        self.record_btn.setEnabled(True)
        self.record_btn.setText("● Démarrer l'enregistrement")
        self._set_record_btn_color(recording=False)
        self.status_dot.setStyleSheet("color: #666; font-size: 14px;")
        self.status_label.setText("Erreur")
        QMessageBox.warning(self, "TeamScribe", f"Échec de l'enregistrement :\n{message}")

    # -- summarize / push-tasks --------------------------------------------------

    def _run_task(self, fn, busy_text: str, done_prefix: str) -> None:
        if self._task_worker is not None:
            return
        self.status_label.setText(busy_text)
        self._task_worker = TaskWorker(fn)
        self._task_worker.finished_ok.connect(
            lambda msg: self._on_task_done(done_prefix, msg)
        )
        self._task_worker.failed.connect(self._on_task_failed)
        self._task_worker.start()

    def _on_task_done(self, prefix: str, msg: str) -> None:
        self._task_worker = None
        self.status_label.setText(prefix)
        self.refresh_sessions()

    def _on_task_failed(self, message: str) -> None:
        self._task_worker = None
        self.status_label.setText("Erreur")
        QMessageBox.warning(self, "TeamScribe", message)

    def run_summarize(self) -> None:
        session = self._selected_session()
        if session is None:
            QMessageBox.information(self, "TeamScribe", "Sélectionne une session d'abord.")
            return

        from . import summarize as summarize_mod

        def fn():
            summarize_mod.summarize_session(session, log=lambda *_: None)
            return f"Résumé refait : {session.name}"

        self._run_task(fn, f"Résumé en cours pour {session.name}…", "Résumé terminé")

    def run_push_tasks(self) -> None:
        session = self._selected_session()
        if session is None:
            QMessageBox.information(self, "TeamScribe", "Sélectionne une session d'abord.")
            return

        summary_path = session / "summary.json"
        if not summary_path.is_file():
            QMessageBox.information(
                self, "TeamScribe", "Pas de summary.json — fais 'Résumer' d'abord."
            )
            return

        from . import planner

        def fn():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            actions = summary.get("actions", [])
            if not actions:
                return "Aucune action à pousser."
            plan_id = config.require_env("PLANNER_PLAN_ID")
            bucket_id = config.require_env("PLANNER_BUCKET_ID")
            token = planner.get_token(log=lambda *_: None)
            created = 0
            for a in actions:
                title = (a.get("description") or "").strip()
                if not title:
                    continue
                planner.create_task(
                    token, plan_id, bucket_id, title,
                    due_iso=None, notes=f"Créé par TeamScribe ({session.name}).",
                )
                created += 1
            return f"{created} tâche(s) créée(s) dans Planner."

        self._run_task(fn, "Envoi vers Planner…", "Tâches poussées")


def main() -> None:
    app = QApplication.instance() or QApplication([])
    widget = TeamScribeWidget()
    widget.show()
    app.exec()


if __name__ == "__main__":
    main()
