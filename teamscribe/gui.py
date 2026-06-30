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

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
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

from . import config, shortcuts, update
from .i18n import tr


# --------------------------------------------------------------------------
# Background workers
# --------------------------------------------------------------------------

class RecordWorker(QThread):
    tick = Signal(float)
    info = Signal(str, str)
    phase = Signal(str)   # "transcribing" | "summarizing" | "saving"
    finished_ok = Signal(Path)
    failed = Signal(str)

    def __init__(self, do_summarize: bool, keep_audio: bool = True):
        super().__init__()
        self.do_summarize = do_summarize
        self.keep_audio = keep_audio
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
            self.phase.emit("transcribing")
            result = transcribe.transcribe(audio_path, session, log=lambda *_: None)
            if not self.keep_audio:
                # Privacy mode: the transcript is already saved, so the raw
                # audio is no longer needed and is removed immediately
                # rather than ever touching naming.finalize_session's rename.
                try:
                    audio_path.unlink()
                except OSError:
                    pass
            if self.do_summarize:
                try:
                    self.phase.emit("summarizing")
                    summarize_mod.summarize_session(session, log=lambda *_: None)
                except Exception:
                    pass  # summary is best-effort; recording still succeeded

            from . import naming

            self.phase.emit("saving")
            session = naming.finalize_session(session, result["text"], log=lambda *_: None)
            self.finished_ok.emit(session)
        except Exception as exc:
            self.failed.emit(str(exc))


# --------------------------------------------------------------------------
# Hand-drawn icons
#
# Emoji glyphs (gear, nut-and-bolt...) render inconsistently across Windows
# font/emoji-font configurations — sometimes falling back to an unrelated
# "tofu" placeholder box. Drawing the few icons we need ourselves with
# QPainter sidesteps that entirely: identical, recognizable pixels on every
# machine regardless of installed fonts.
# --------------------------------------------------------------------------

def _gear_icon(color: str = "#9aa0a6", size: int = 18) -> QIcon:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))

    center = size / 2
    outer_r = size * 0.36
    inner_r = size * 0.16
    tooth_w = size * 0.16
    tooth_len = size * 0.14

    for i in range(8):
        painter.save()
        painter.translate(center, center)
        painter.rotate(i * 45)
        painter.drawRect(QRectF(-tooth_w / 2, -outer_r - tooth_len, tooth_w, tooth_len))
        painter.restore()
    painter.drawEllipse(QPointF(center, center), outer_r, outer_r)

    # Punch a transparent hole in the middle so it reads as a gear, not a
    # solid blob — works on any background since it's true transparency.
    painter.setCompositionMode(QPainter.CompositionMode_Clear)
    painter.drawEllipse(QPointF(center, center), inner_r, inner_r)
    painter.end()
    return QIcon(pixmap)


class _GripDots(QSizeGrip):
    """A QSizeGrip that paints a clear diagonal dot pattern.

    The base QSizeGrip's native paint is theme/style dependent and got
    fully hidden by our background-color stylesheet, leaving a plain flat
    square with no visual hint that it's a resize handle. Painting our own
    dots makes the affordance obvious in both themes.
    """

    def __init__(self, parent, color: str):
        super().__init__(parent)
        self._color = QColor(color)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        size = min(self.width(), self.height())
        dot = max(2, size // 7)
        gap = dot + 2
        for row in range(3):
            for col in range(3):
                if col + row < 2:
                    continue  # only the lower-right diagonal half
                cx = size - 3 - col * gap
                cy = size - 3 - row * gap
                painter.drawEllipse(QPointF(cx, cy), dot / 2, dot / 2)
        painter.end()


class UpdateCheckWorker(QThread):
    """Runs update.check_for_update() off the UI thread; never raises."""

    checked = Signal(dict)

    def run(self) -> None:
        self.checked.emit(update.check_for_update())


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
        self.settings = config.load_gui_settings()
        self.lang = self.settings.get("language", "en")
        self.setWindowTitle(tr("settings_title", self.lang))

        layout = QVBoxLayout(self)

        self.startup_box = QCheckBox(tr("launch_at_startup", self.lang))
        self.startup_box.setChecked(shortcuts.is_startup_enabled())
        layout.addWidget(self.startup_box)

        self.summarize_box = QCheckBox(tr("auto_summarize", self.lang))
        self.summarize_box.setChecked(self.settings.get("auto_summarize", True))
        layout.addWidget(self.summarize_box)

        self.ontop_box = QCheckBox(tr("always_on_top", self.lang))
        self.ontop_box.setChecked(self.settings.get("always_on_top", True))
        layout.addWidget(self.ontop_box)

        layout.addWidget(QLabel(tr("language_label", self.lang)))
        self.lang_combo = QComboBox()
        _LANGUAGES = [
            ("en", "English"),
            ("fr", "Français"),
            ("es", "Español"),
            ("pt", "Português"),
            ("de", "Deutsch"),
            ("it", "Italiano"),
            ("ru", "Русский"),
            ("ja", "日本語"),
            ("zh", "中文"),
            ("ko", "한국어"),
            ("hi", "हिन्दी"),
            ("ar", "العربية"),
            ("bn", "বাংলা"),
            ("ur", "اردو"),
        ]
        for code, label in _LANGUAGES:
            self.lang_combo.addItem(label, code)
        current_idx = next(
            (i for i, (code, _) in enumerate(_LANGUAGES) if code == self.lang), 0
        )
        self.lang_combo.setCurrentIndex(current_idx)
        layout.addWidget(self.lang_combo)
        lang_note = QLabel(tr("language_restart_note", self.lang))
        lang_note.setStyleSheet("color: #999; font-size: 11px;")
        layout.addWidget(lang_note)

        layout.addWidget(QLabel(tr("theme_label", self.lang)))
        theme_row = QHBoxLayout()
        self.dark_radio = QRadioButton(tr("theme_dark", self.lang))
        self.light_radio = QRadioButton(tr("theme_light", self.lang))
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

        desktop_btn = QPushButton(tr("create_desktop_shortcut", self.lang))
        desktop_btn.clicked.connect(self._create_desktop_shortcut)
        layout.addWidget(desktop_btn)

        devices_btn = QPushButton(tr("check_audio", self.lang))
        devices_btn.clicked.connect(self._run_selftest)
        layout.addWidget(devices_btn)

        layout.addWidget(QLabel(tr("update_label", self.lang)))
        self.update_status_label = QLabel("…")
        layout.addWidget(self.update_status_label)
        update_row = QHBoxLayout()
        check_update_btn = QPushButton(tr("check_now", self.lang))
        check_update_btn.clicked.connect(self._check_update_now)
        self.apply_update_btn = QPushButton(tr("update_now", self.lang))
        self.apply_update_btn.clicked.connect(self._apply_update_now)
        update_row.addWidget(check_update_btn)
        update_row.addWidget(self.apply_update_btn)
        layout.addLayout(update_row)
        self._refresh_update_status((parent._update_info if parent else None) or {})

        buttons_row = QHBoxLayout()
        save_btn = QPushButton(tr("save", self.lang))
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(tr("cancel", self.lang))
        cancel_btn.clicked.connect(self.reject)
        buttons_row.addWidget(save_btn)
        buttons_row.addWidget(cancel_btn)
        layout.addLayout(buttons_row)

        kofi_label = QLabel(tr("kofi_link", self.lang))
        kofi_label.setOpenExternalLinks(True)
        kofi_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(kofi_label)

        from . import __version__
        version_label = QLabel(tr("version_label", self.lang, version=__version__))
        version_label.setStyleSheet("color: #888; font-size: 11px;")
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)

    def _refresh_update_status(self, info: dict) -> None:
        if info.get("error") == "not-a-git-checkout":
            self.update_status_label.setText(tr("update_check_unavailable", self.lang))
            self.apply_update_btn.setEnabled(False)
        elif info.get("error"):
            self.update_status_label.setText(tr("update_check_failed", self.lang, error=info["error"]))
            self.apply_update_btn.setEnabled(False)
        elif info.get("available"):
            behind = info.get("behind", 0)
            self.update_status_label.setText(tr("update_available", self.lang, behind=behind))
            self.apply_update_btn.setEnabled(True)
        else:
            self.update_status_label.setText(tr("update_up_to_date", self.lang))
            self.apply_update_btn.setEnabled(False)

    def _check_update_now(self) -> None:
        self.update_status_label.setText(tr("update_checking", self.lang))
        QApplication.processEvents()
        info = update.check_for_update()
        parent = self.parent()
        if parent is not None:
            parent._update_info = info
            parent.update_badge.setVisible(bool(info.get("available")))
        self._refresh_update_status(info)

    def _apply_update_now(self) -> None:
        confirm = QMessageBox.question(
            self, tr("update_confirm_title", self.lang),
            tr("update_confirm_body", self.lang),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        self.apply_update_btn.setEnabled(False)
        self.update_status_label.setText(tr("update_in_progress", self.lang))
        QApplication.processEvents()
        try:
            update.apply_update(log=lambda msg: (
                self.update_status_label.setText(msg), QApplication.processEvents()
            ))
        except Exception as exc:
            QMessageBox.warning(self, "TeamScribe", tr("update_failed", self.lang, error=exc))
            self._refresh_update_status(update.check_for_update())
            return
        self.update_status_label.setText(tr("update_done", self.lang))
        if QMessageBox.question(
            self, tr("restart_confirm_title", self.lang),
            tr("restart_confirm_body", self.lang),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            parent = self.parent()
            self.reject()
            if parent is not None:
                parent.restart_app()

    def _update_glass_label(self, value: int) -> None:
        self.glass_label.setText(tr("glass_opacity_label", self.lang, value=value))

    def _create_desktop_shortcut(self) -> None:
        try:
            path = shortcuts.create_desktop_shortcut()
        except Exception as exc:
            QMessageBox.warning(self, "TeamScribe", tr("shortcut_create_failed", self.lang, error=exc))
            return
        QMessageBox.information(self, "TeamScribe", tr("shortcut_created", self.lang, path=path))

    def _run_selftest(self) -> None:
        from . import capture

        try:
            info = capture.list_devices()
        except RuntimeError as exc:
            QMessageBox.warning(self, "TeamScribe", str(exc))
            return
        msg = tr(
            "audio_check_result", self.lang,
            speakers=info["default_speakers"]["name"],
            mic=info["default_mic"]["name"],
            count=len(info["loopback_devices"]),
        )
        QMessageBox.information(self, tr("audio_title", self.lang), msg)

    def accept(self) -> None:
        try:
            shortcuts.set_startup_enabled(self.startup_box.isChecked())
        except Exception as exc:
            QMessageBox.warning(
                self, "TeamScribe", tr("startup_toggle_failed", self.lang, error=exc)
            )
            return

        self.settings["auto_summarize"] = self.summarize_box.isChecked()
        self.settings["always_on_top"] = self.ontop_box.isChecked()
        self.settings["theme"] = "light" if self.light_radio.isChecked() else "dark"
        self.settings["glass_opacity"] = self.glass_slider.value()
        self.settings["language"] = self.lang_combo.currentData()
        config.save_gui_settings(self.settings)
        super().accept()


# --------------------------------------------------------------------------
# Widget
# --------------------------------------------------------------------------

class TeamScribeWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TeamScribe")
        logo_path = config.ROOT / "assets" / "logo.ico"
        if logo_path.is_file():
            self.setWindowIcon(QIcon(str(logo_path)))
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(300, 420)

        self._drag_offset = None
        self._pinned = False
        self._minimized = False
        self._expanded_size: QSize | None = None
        self._record_worker: RecordWorker | None = None
        self._task_worker: TaskWorker | None = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_info: dict = {"available": False}
        self.settings = config.load_gui_settings()
        self.lang = self.settings.get("language", "en")

        self._build_ui()
        self._apply_always_on_top(self.settings.get("always_on_top", True))
        self._apply_theme()
        self.pin_btn.setChecked(self.settings.get("pinned", False))
        self._restore_position()
        self.refresh_sessions()
        self.check_for_update()
        self._prefetch_shortcuts()
        self._prefetch_whisper()
        if self.settings.get("minimized", False):
            self._set_minimized(True, save=False)

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
        self.size_grip.set_color("#888888" if theme == "light" else "#aaaaaa")

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
        self.pin_btn.setToolTip(tr("pin_tooltip_off", self.lang))
        self.pin_btn.toggled.connect(self.set_pinned)
        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(_gear_icon(size=18))
        self.settings_btn.setIconSize(QSize(18, 18))
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.setToolTip(tr("settings_tooltip", self.lang))
        self.settings_btn.clicked.connect(self.open_settings)
        self.update_badge = QLabel(self.settings_btn)
        self.update_badge.setFixedSize(8, 8)
        self.update_badge.setStyleSheet(
            "background: #e53935; border-radius: 4px; border: 1px solid #1e1e1e;"
        )
        self.update_badge.move(16, -1)
        self.update_badge.hide()
        restart_btn = QPushButton("⟳")
        restart_btn.setFixedSize(22, 22)
        restart_btn.setToolTip(tr("restart_tooltip", self.lang))
        restart_btn.clicked.connect(self.restart_app)
        self.minimize_btn = QPushButton("▼")
        self.minimize_btn.setFixedSize(22, 22)
        self.minimize_btn.setToolTip(tr("minimize_tooltip_collapse", self.lang))
        self.minimize_btn.clicked.connect(self.toggle_minimize)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(22, 22)
        close_btn.clicked.connect(self.close)
        self.mini_record_btn = QPushButton("●")
        self.mini_record_btn.setFixedSize(22, 22)
        self.mini_record_btn.setToolTip(tr("record_start", self.lang))
        self.mini_record_btn.clicked.connect(self.toggle_recording)
        self.mini_record_btn.hide()
        self._set_mini_record_btn_color(recording=False)

        title_row.addWidget(self.pin_btn)
        title_row.addWidget(self.settings_btn)
        title_row.addWidget(self.status_dot)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.mini_record_btn)
        title_row.addWidget(restart_btn)
        title_row.addWidget(self.minimize_btn)
        title_row.addWidget(close_btn)
        layout.addLayout(title_row)

        # Collapsible content — everything below the title bar is wrapped in
        # a single widget so toggle_minimize() can show/hide it in one call.
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)

        self.status_label = QLabel(tr("status_ready", self.lang))
        self.status_label.setStyleSheet("color: #999;")
        content_layout.addWidget(self.status_label)

        # Privacy mode switch: when ON the raw audio file is deleted after
        # transcription so only the text transcript is kept.
        privacy_row = QHBoxLayout()
        privacy_label = QLabel(tr("privacy_mode_label", self.lang))
        privacy_label.setStyleSheet("font-size: 12px;")
        self.privacy_switch = QCheckBox()
        # Privacy mode ON by default (keep_audio defaults to False now)
        privacy_on_default = not self.settings.get("keep_audio", False)
        self.privacy_switch.setChecked(privacy_on_default)
        self.privacy_switch.setToolTip(tr("privacy_mode_tooltip", self.lang))
        self.privacy_switch.toggled.connect(self.set_keep_audio)
        self.privacy_hint = QLabel(tr("privacy_mode_hint", self.lang))
        self.privacy_hint.setStyleSheet("color: #1e88e5; font-size: 10px;")
        self.privacy_hint.setVisible(False)
        privacy_row.addWidget(privacy_label)
        privacy_row.addStretch()
        privacy_row.addWidget(self.privacy_switch)
        content_layout.addLayout(privacy_row)
        content_layout.addWidget(self.privacy_hint)
        self.set_keep_audio(privacy_on_default)

        # Record button
        self.record_btn = QPushButton(tr("record_start", self.lang))
        self.record_btn.clicked.connect(self.toggle_recording)
        self._set_record_btn_color(recording=False)
        content_layout.addWidget(self.record_btn)

        content_layout.addWidget(QLabel(tr("recent_sessions", self.lang)))
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.session_list.itemDoubleClicked.connect(self._open_session_folder)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._show_session_menu)
        self.session_list.installEventFilter(self)
        content_layout.addWidget(self.session_list)

        actions_row = QHBoxLayout()
        self.push_btn = QPushButton(tr("push_planner_btn", self.lang))
        self.push_btn.clicked.connect(self.run_push_tasks)
        actions_row.addWidget(self.push_btn)
        content_layout.addLayout(actions_row)

        bottom_row = QHBoxLayout()
        refresh_btn = QPushButton(tr("refresh_list_btn", self.lang))
        refresh_btn.clicked.connect(self.refresh_sessions)
        open_folder_btn = QPushButton(tr("open_folder_btn", self.lang))
        open_folder_btn.clicked.connect(self.open_sessions_folder)
        bottom_row.addWidget(refresh_btn)
        bottom_row.addWidget(open_folder_btn)
        content_layout.addLayout(bottom_row)

        # Frameless windows have no native resize border, so a visible grip
        # in the corner is what lets the user actually resize the widget.
        # Native QSizeGrip painting is theme/style dependent (and was nearly
        # invisible against the light theme); _GripDots draws an explicit
        # diagonal dot pattern instead, so the resize handle always looks
        # like one regardless of theme.
        self.size_grip = _GripDots(self, "#999999")
        self.size_grip.setFixedSize(16, 16)
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip_row.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        content_layout.addLayout(grip_row)

        layout.addWidget(self.content_widget)
        self.setMinimumSize(240, 320)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._pinned and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None

    # -- minimize / collapse --------------------------------------------------

    def toggle_minimize(self) -> None:
        self._set_minimized(not self._minimized)

    def _set_mini_record_btn_color(self, recording: bool) -> None:
        if recording:
            self.mini_record_btn.setText("■")
            self.mini_record_btn.setStyleSheet(
                "background: #c0392b; color: white; border: 1px solid #e74c3c; border-radius: 4px;"
            )
            self.mini_record_btn.setToolTip(tr("record_stop", self.lang))
        else:
            self.mini_record_btn.setText("●")
            self.mini_record_btn.setStyleSheet(
                "background: #2e8b3d; color: white; border: 1px solid #3fae52; border-radius: 4px;"
            )
            self.mini_record_btn.setToolTip(tr("record_start", self.lang))

    def _set_minimized(self, minimized: bool, save: bool = True) -> None:
        self._minimized = minimized
        if minimized:
            self._expanded_size = self.size()
            self.content_widget.hide()
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.minimize_btn.setText("▲")
            self.minimize_btn.setToolTip(tr("minimize_tooltip_expand", self.lang))
            self.mini_record_btn.show()
            # Let the layout recalculate with content hidden, then force the
            # window to the resulting hint height.
            QApplication.processEvents()
            self.resize(self.width(), self.sizeHint().height())
        else:
            self.mini_record_btn.hide()
            self.setMinimumSize(240, 320)
            self.setMaximumSize(16777215, 16777215)
            self.content_widget.show()
            if self._expanded_size is not None:
                self.resize(self._expanded_size)
            self.minimize_btn.setText("▼")
            self.minimize_btn.setToolTip(tr("minimize_tooltip_collapse", self.lang))
        if save:
            self.settings["minimized"] = minimized
            config.save_gui_settings(self.settings)

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_btn.setStyleSheet(
            "font-size: 13px; background: #2e8b3d; border: 1px solid #3fae52;"
            if pinned else "font-size: 13px;"
        )
        self.pin_btn.setToolTip(
            tr("pin_tooltip_on", self.lang) if pinned else tr("pin_tooltip_off", self.lang)
        )
        self.settings["pinned"] = pinned
        config.save_gui_settings(self.settings)

    def set_keep_audio(self, privacy_on: bool) -> None:
        keep_audio = not privacy_on
        self.settings["keep_audio"] = keep_audio
        config.save_gui_settings(self.settings)
        if privacy_on:
            self.privacy_hint.setVisible(True)
            QTimer.singleShot(3000, lambda: self.privacy_hint.setVisible(False))
        else:
            self.privacy_hint.setVisible(False)

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
        self.settings_btn.setEnabled(False)
        self.status_label.setText(tr("settings_loading", self.lang))
        QApplication.processEvents()
        dialog = SettingsDialog(self)
        self.settings_btn.setEnabled(True)
        self.status_label.setText(tr("status_ready", self.lang))
        if dialog.exec() == QDialog.Accepted:
            self.settings = config.load_gui_settings()
            self._apply_always_on_top(self.settings.get("always_on_top", True))
            self._apply_theme()

    def restart_app(self) -> None:
        if self._record_worker is not None:
            QMessageBox.information(
                self, "TeamScribe", tr("restart_blocked", self.lang)
            )
            return
        self._save_position()
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        exe = str(pythonw) if pythonw.is_file() else str(python)
        subprocess.Popen([exe, "-m", "teamscribe.cli", "gui"], cwd=str(config.ROOT))
        QApplication.quit()

    # -- updates --------------------------------------------------

    def _prefetch_shortcuts(self) -> None:
        """Warm the shortcuts folder-path cache in a background thread.

        The first call to shortcuts.is_startup_enabled() spawns a PowerShell
        process (~300 ms). Doing it here at startup, off-thread, means the
        Settings dialog opens instantly on every subsequent click.
        """
        import threading
        threading.Thread(target=shortcuts.is_startup_enabled, daemon=True).start()

    def _prefetch_whisper(self) -> None:
        """Load the Whisper model into memory at startup, off the UI thread.

        Model loading takes several seconds on first use. Pre-loading it here
        means transcription starts immediately after the user stops recording,
        instead of waiting for the model to load first.
        """
        import threading
        from . import transcribe as transcribe_mod
        threading.Thread(target=transcribe_mod.warmup_model, daemon=True).start()

    def check_for_update(self) -> None:
        if self._update_check_worker is not None:
            return
        self._update_check_worker = UpdateCheckWorker()
        self._update_check_worker.checked.connect(self._on_update_checked)
        self._update_check_worker.start()

    def _on_update_checked(self, info: dict) -> None:
        self._update_check_worker = None
        self._update_info = info or {"available": False}
        self.update_badge.setVisible(bool(self._update_info.get("available")))

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
        menu.addAction(tr("delete_action", self.lang), self._delete_selected_sessions)
        menu.exec(self.session_list.mapToGlobal(pos))

    def _delete_selected_sessions(self) -> None:
        paths = self._selected_sessions()
        if not paths:
            return

        if len(paths) == 1:
            names = tr("names_single", self.lang, name=paths[0].name)
        else:
            names = tr("names_plural", self.lang, count=len(paths))

        box = QMessageBox(self)
        box.setWindowTitle(tr("delete_session_title", self.lang))
        box.setText(tr("delete_session_body", self.lang, names=names))
        box.setIcon(QMessageBox.Question)
        widget_btn = box.addButton(tr("remove_from_widget", self.lang), QMessageBox.ActionRole)
        delete_btn = box.addButton(tr("delete_folders", self.lang), QMessageBox.DestructiveRole)
        box.addButton(tr("cancel", self.lang), QMessageBox.RejectRole)
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
                tr("confirm_delete_title", self.lang),
                tr("confirm_delete_body", self.lang, names=names),
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
                    self, "TeamScribe", tr("delete_failed", self.lang, errors="\n".join(errors))
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
            self.status_label.setText(tr("stopping", self.lang))
            self.record_btn.setEnabled(False)
            self.mini_record_btn.setEnabled(False)
            self._record_worker.stop()
            return

        self._record_worker = RecordWorker(
            do_summarize=self.settings.get("auto_summarize", True),
            keep_audio=self.settings.get("keep_audio", True),
        )
        self._record_worker.tick.connect(self._on_tick)
        self._record_worker.info.connect(self._on_info)
        self._record_worker.phase.connect(self._on_phase)
        self._record_worker.finished_ok.connect(self._on_record_done)
        self._record_worker.failed.connect(self._on_record_failed)
        self._record_worker.start()

        self.record_btn.setText(tr("record_stop", self.lang))
        self._set_record_btn_color(recording=True)
        self._set_mini_record_btn_color(recording=True)
        self.status_dot.setStyleSheet("color: #e53935; font-size: 14px;")
        self.status_label.setText(tr("listening", self.lang, time="00:00:00"))

    def _on_phase(self, phase: str) -> None:
        _phase_keys = {
            "transcribing": "phase_transcribing",
            "summarizing": "phase_summarizing",
            "saving": "phase_saving",
        }
        text = tr(_phase_keys.get(phase, phase), self.lang)
        self.status_label.setText(text)
        self.mini_record_btn.setToolTip(text)
        self.mini_record_btn.setText("⟳")

    def _on_tick(self, elapsed: float) -> None:
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        self.status_label.setText(tr("listening", self.lang, time=f"{h:02d}:{m:02d}:{s:02d}"))

    def _on_info(self, mic_name: str, speaker_name: str) -> None:
        self.status_label.setToolTip(f"mic: {mic_name}\nspeakers: {speaker_name}")

    def _on_record_done(self, session: Path) -> None:
        self._record_worker = None
        self.record_btn.setEnabled(True)
        self.mini_record_btn.setEnabled(True)
        self.record_btn.setText(tr("record_start", self.lang))
        self._set_record_btn_color(recording=False)
        self._set_mini_record_btn_color(recording=False)
        self.status_dot.setStyleSheet("color: #666; font-size: 14px;")
        self.status_label.setText(tr("record_done", self.lang, name=session.name))
        self.refresh_sessions()

    def _on_record_failed(self, message: str) -> None:
        self._record_worker = None
        self.record_btn.setEnabled(True)
        self.mini_record_btn.setEnabled(True)
        self.record_btn.setText(tr("record_start", self.lang))
        self._set_record_btn_color(recording=False)
        self._set_mini_record_btn_color(recording=False)
        self.status_dot.setStyleSheet("color: #666; font-size: 14px;")
        self.status_label.setText(tr("error", self.lang))
        QMessageBox.warning(self, "TeamScribe", tr("record_failed", self.lang, error=message))

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
        self.status_label.setText(tr("error", self.lang))
        QMessageBox.warning(self, "TeamScribe", message)

    def run_summarize(self) -> None:
        session = self._selected_session()
        if session is None:
            QMessageBox.information(self, "TeamScribe", tr("select_session_first", self.lang))
            return

        from . import summarize as summarize_mod

        def fn():
            summarize_mod.summarize_session(session, log=lambda *_: None)
            return tr("resummarized", self.lang, name=session.name)

        self._run_task(
            fn,
            tr("summarizing", self.lang, name=session.name),
            tr("summary_done", self.lang),
        )

    def run_push_tasks(self) -> None:
        session = self._selected_session()
        if session is None:
            QMessageBox.information(self, "TeamScribe", tr("select_session_first", self.lang))
            return

        summary_path = session / "summary.json"
        needs_summarize = not summary_path.is_file()

        from . import planner, summarize as summarize_mod

        def fn():
            # Auto-format locally first if no summary exists yet, so the
            # formatted notes are always saved to disk even if Planner is
            # unreachable.
            if needs_summarize:
                summarize_mod.summarize_session(session, log=lambda *_: None)

            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            actions = summary.get("actions", [])
            if not actions:
                return tr("no_actions_to_push", self.lang)
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
                    due_iso=None, notes=tr("planner_task_notes", self.lang, name=session.name),
                )
                created += 1
            return tr("planner_tasks_created", self.lang, count=created)

        busy_text = (
            tr("formatting_then_pushing", self.lang)
            if needs_summarize
            else tr("sending_to_planner", self.lang)
        )
        self._run_task(fn, busy_text, tr("tasks_pushed", self.lang))


def main() -> None:
    app = QApplication.instance() or QApplication([])
    widget = TeamScribeWidget()
    widget.show()
    app.exec()


if __name__ == "__main__":
    main()
