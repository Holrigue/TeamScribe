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
    QFont,
    QFontMetrics,
    QPainterPath,
    QPen,
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
    QScrollArea,
    QWidgetAction,
    QSizeGrip,
    QSlider,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
    QLineEdit,
    QPlainTextEdit,
)

from . import config, export, shortcuts, update
from .i18n import tr


# --------------------------------------------------------------------------
# Background workers
# --------------------------------------------------------------------------

class RecordWorker(QThread):
    tick = Signal(float)
    info = Signal(str, str)
    phase = Signal(str)   # "transcribing" | "summarizing" | "saving"
    renamed = Signal()
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
        from . import capture, naming, summarize as summarize_mod, transcribe

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

            # Best-effort quick title from a short audio snippet, bounded to
            # ~7s, so the session shows a meaningful name right away instead
            # of a raw timestamp while the full pipeline (which can take
            # minutes on long meetings) still runs. If it doesn't finish in
            # time, this is simply skipped — finalize_session's slower,
            # full-transcript rename below still covers it.
            quick_result: dict[str, str] = {}

            def _quick_scan():
                title = naming.quick_title(audio_path)
                if title:
                    quick_result["slug"] = title

            quick_thread = threading.Thread(target=_quick_scan, daemon=True)
            quick_thread.start()
            quick_thread.join(timeout=7.0)
            if "slug" in quick_result:
                session = naming.quick_rename(session, quick_result["slug"])
                audio_path = next(session.glob("*.wav"), audio_path)
                self.renamed.emit()

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

# --------------------------------------------------------------------------
# Design language: neutral greys, one blue accent, red only for recording and
# alerts. Icons are thin single-colour line drawings.
# --------------------------------------------------------------------------

PALETTES = {
    # 1. Rétro: yellowed white, retro orange, pale grey.
    "retro": {
        "root": (244, 238, 220), "border": "#d3cdc1", "text": "#2f2b25", "muted": "#7c776e",
        "faint": "#b5b0a6", "surface": "#faf6ea", "surface_hover": "#efe8d5", "line": "#dad5ca",
        "field": "#fbf8f0", "selected": "#f5dfcc", "accent": "#d9662b", "accent_hover": "#c45823",
        "on_accent": "#ffffff", "red": "#d9662b", "scroll": "#cfcabe", "swatch": ("#f4eedc", "#d9662b", "#c9c6bf"),
    },
    # 2. Vitrail: stained-glass green, velvet brown, bluish black.
    "vitrail": {
        "root": (17, 23, 28), "border": "#29323a", "text": "#e9e3d9", "muted": "#9aa29d",
        "faint": "#5d6863", "surface": "#1a2128", "surface_hover": "#222b33", "line": "#2c353d",
        "field": "#141b21", "selected": "#3b2a25", "accent": "#3e8c73", "accent_hover": "#4aa084",
        "on_accent": "#ffffff", "red": "#a65c43", "scroll": "#3a444c", "swatch": ("#3e8c73", "#5b3a32", "#11171c"),
    },
    # 3. Techno: black, techno red, white.
    "techno": {
        "root": (11, 11, 12), "border": "#26262a", "text": "#f4f4f5", "muted": "#8f8f96",
        "faint": "#56565c", "surface": "#161618", "surface_hover": "#202023", "line": "#2a2a2e",
        "field": "#111113", "selected": "#2b1517", "accent": "#ff2e3f", "accent_hover": "#ff4a58",
        "on_accent": "#ffffff", "red": "#ff2e3f", "scroll": "#3a3a3f", "swatch": ("#0b0b0c", "#ff2e3f", "#f4f4f5"),
    },
}
PALETTE_ORDER = ("retro", "vitrail", "techno")
# Settings saved before the palettes existed used "dark" / "light".
_LEGACY_THEMES = {"dark": "techno", "light": "retro"}


def _plain(label: str) -> str:
    """Drop a leading glyph (●, ■…) from a translated label: the button
    already shows a drawn icon."""
    return label.lstrip("●■◼⏹▶ ").strip()


def palette_id(theme: str) -> str:
    theme = _LEGACY_THEMES.get(theme, theme)
    return theme if theme in PALETTES else "techno"


def palette(theme: str) -> dict:
    return PALETTES[palette_id(theme)]


def _swatch_icon(colors, size: int = 14) -> QIcon:
    """Three small squares showing a palette in the settings list."""
    pm = QPixmap(size * 3 + 4, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    for i, color in enumerate(colors):
        p.setPen(QPen(QColor("#888888"), 0.6))
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(i * (size + 2) + 0.5, 0.5, size - 1, size - 1), 3, 3)
    p.end()
    return QIcon(pm)


def _line_icon(name: str, color: str, size: int = 16, stroke: float = 1.5) -> QIcon:
    """Small monochrome line icons drawn in code (no emoji, no image files)."""
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(scale, scale)
    pen = QPen(QColor(color), stroke)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    s = float(size)

    def pt(x, y):
        return QPointF(x * s, y * s)

    def poly(*points, close=False):
        path = QPainterPath(pt(*points[0]))
        for q in points[1:]:
            path.lineTo(pt(*q))
        if close:
            path.closeSubpath()
        p.drawPath(path)

    if name == "search":
        p.drawEllipse(pt(0.44, 0.44), 0.26 * s, 0.26 * s)
        p.drawLine(pt(0.63, 0.63), pt(0.84, 0.84))
    elif name == "refresh":
        p.drawArc(QRectF(0.2 * s, 0.2 * s, 0.6 * s, 0.6 * s), 70 * 16, 290 * 16)
        poly((0.58, 0.12), (0.62, 0.22), (0.52, 0.27))
    elif name == "folder":
        poly((0.14, 0.28), (0.4, 0.28), (0.48, 0.36), (0.86, 0.36), (0.86, 0.78), (0.14, 0.78), close=True)
    elif name == "pin":
        poly((0.36, 0.14), (0.64, 0.14), (0.6, 0.4), (0.72, 0.58), (0.28, 0.58), (0.4, 0.4), close=True)
        p.drawLine(pt(0.5, 0.58), pt(0.5, 0.88))
    elif name == "gear":
        p.drawEllipse(pt(0.5, 0.5), 0.12 * s, 0.12 * s)
        p.drawEllipse(pt(0.5, 0.5), 0.27 * s, 0.27 * s)
        import math
        for i in range(8):
            a = math.radians(i * 45)
            p.drawLine(pt(0.5 + 0.29 * math.cos(a), 0.5 + 0.29 * math.sin(a)), pt(0.5 + 0.38 * math.cos(a), 0.5 + 0.38 * math.sin(a)))
    elif name == "power":
        p.drawArc(QRectF(0.22 * s, 0.24 * s, 0.56 * s, 0.56 * s), 125 * 16, 290 * 16)
        p.drawLine(pt(0.5, 0.14), pt(0.5, 0.46))
    elif name == "chevron_down":
        poly((0.3, 0.42), (0.5, 0.62), (0.7, 0.42))
    elif name == "chevron_up":
        poly((0.3, 0.6), (0.5, 0.4), (0.7, 0.6))
    elif name == "close":
        p.drawLine(pt(0.3, 0.3), pt(0.7, 0.7))
        p.drawLine(pt(0.7, 0.3), pt(0.3, 0.7))
    elif name == "arrow_up":
        p.drawLine(pt(0.5, 0.8), pt(0.5, 0.22))
        poly((0.28, 0.44), (0.5, 0.22), (0.72, 0.44))
    elif name == "rec":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(pt(0.5, 0.5), 0.2 * s, 0.2 * s)
    elif name == "stop":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(0.32 * s, 0.32 * s, 0.36 * s, 0.36 * s), 1.5, 1.5)
    elif name == "tasks":
        for i, y in enumerate((0.3, 0.5, 0.7)):
            poly((0.18, y), (0.23, y + 0.05), (0.3, y - 0.04))
            p.drawLine(pt(0.4, y), pt(0.84, y))
    elif name == "chevron_right":
        poly((0.42, 0.3), (0.62, 0.5), (0.42, 0.7))
    elif name == "play":
        poly((0.34, 0.24), (0.76, 0.5), (0.34, 0.76), close=True)
    elif name == "doc":
        poly((0.26, 0.14), (0.58, 0.14), (0.76, 0.32), (0.76, 0.86), (0.26, 0.86), close=True)
        p.drawLine(pt(0.36, 0.5), pt(0.66, 0.5))
        p.drawLine(pt(0.36, 0.66), pt(0.66, 0.66))
    elif name == "copy":
        p.drawRoundedRect(QRectF(0.32 * s, 0.32 * s, 0.5 * s, 0.52 * s), 2, 2)
        poly((0.2, 0.68), (0.2, 0.18), (0.6, 0.18))
    elif name == "ask":
        poly((0.16, 0.2), (0.84, 0.2), (0.84, 0.68), (0.46, 0.68), (0.28, 0.84), (0.3, 0.68), (0.16, 0.68), close=True)
    elif name == "trash":
        p.drawLine(pt(0.18, 0.26), pt(0.82, 0.26))
        poly((0.4, 0.26), (0.42, 0.16), (0.58, 0.16), (0.6, 0.26))
        poly((0.26, 0.26), (0.3, 0.86), (0.7, 0.86), (0.74, 0.26))
    elif name == "audio":
        poly((0.16, 0.4), (0.32, 0.4), (0.52, 0.22), (0.52, 0.78), (0.32, 0.6), (0.16, 0.6), close=True)
        p.drawArc(QRectF(0.5 * s, 0.32 * s, 0.24 * s, 0.36 * s), -60 * 16, 120 * 16)
    elif name == "busy":
        p.drawArc(QRectF(0.24 * s, 0.24 * s, 0.52 * s, 0.52 * s), 90 * 16, 270 * 16)
    p.end()
    return QIcon(pm)


class _Switch(QCheckBox):
    """A small on/off switch (track + knob) drawn in the theme colours."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(32, 18)
        self.colors = ("#7b93ff", "#3a3a42", "#ffffff")

    def sizeHint(self) -> QSize:
        return QSize(32, 18)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, event) -> None:
        on, off, knob = self.colors
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        track = QRectF(1, 2, 30, 14)
        p.setBrush(QColor(on if self.isChecked() else off))
        p.drawRoundedRect(track, 7, 7)
        x = track.right() - 7 if self.isChecked() else track.left() + 7
        p.setBrush(QColor(knob))
        p.drawEllipse(QPointF(x, track.center().y()), 5, 5)
        p.end()


class _ReplyDialog(QDialog):
    """Shows the AI's answer, with Copy and Close."""

    def __init__(self, parent, title: str, text: str, lang: str, stylesheet: str):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setObjectName("root")
        self.setStyleSheet(stylesheet)
        self.resize(460, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        body = QPlainTextEdit(text)
        body.setReadOnly(True)
        layout.addWidget(body, 1)
        row = QHBoxLayout()
        self.note = QLabel("")
        self.note.setObjectName("muted")
        copy_btn = QPushButton(tr("copy_btn", lang))
        copy_btn.setObjectName("primary")
        copy_btn.clicked.connect(lambda: (QGuiApplication.clipboard().setText(text), self.note.setText(tr("copied", lang))))
        close_btn = QPushButton(tr("close_btn", lang))
        close_btn.clicked.connect(self.accept)
        row.addWidget(self.note, 1)
        row.addWidget(close_btn)
        row.addWidget(copy_btn)
        layout.addLayout(row)


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
# Session list rows: status dot, title, date underneath
# --------------------------------------------------------------------------

TITLE_ROLE = Qt.UserRole + 1
DATE_ROLE = Qt.UserRole + 2
DONE_ROLE = Qt.UserRole + 3

_MONTHS = {
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def _format_when(when, lang: str) -> str:
    months = _MONTHS.get(lang)
    if not months:
        return f"{when:%Y-%m-%d · %H:%M}"
    if lang == "fr":
        return f"{when.day} {months[when.month - 1]} {when.year} · {when:%H:%M}"
    return f"{months[when.month - 1]} {when.day}, {when.year} · {when:%H:%M}"


class _SessionDelegate(QStyledItemDelegate):
    """Two-line rows: a status mark (filled = summary ready, ring = not yet),
    the meeting title, and its date in small muted text."""

    ROW = 44

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pal = palette("dark")

    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), self.ROW if index.data(Qt.UserRole) else 34)

    def paint(self, painter: QPainter, option, index) -> None:
        pal = self.pal
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.State_Selected)
        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal["selected"]))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setBrush(QColor(pal["accent"]))
            painter.drawRoundedRect(QRectF(rect.left() + 1, rect.top() + 8, 2.5, rect.height() - 16), 1.2, 1.2)
        elif option.state & QStyle.State_MouseOver:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal["surface_hover"]))
            painter.drawRoundedRect(rect, 7, 7)

        if not index.data(Qt.UserRole):
            painter.setPen(QColor(pal["muted"]))
            painter.drawText(rect, Qt.AlignCenter | Qt.TextWordWrap, index.data(Qt.DisplayRole) or "")
            painter.restore()
            return

        center = QPointF(rect.left() + 13, rect.top() + 14)
        if index.data(DONE_ROLE):
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(pal["muted"]))
            painter.drawEllipse(center, 3, 3)
        else:
            painter.setPen(QPen(QColor(pal["faint"]), 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, 3, 3)

        left = rect.left() + 24
        width = rect.width() - 30
        title_font = QFont(option.font)
        title_font.setWeight(QFont.Medium)
        painter.setFont(title_font)
        painter.setPen(QColor(pal["text"]))
        title = QFontMetrics(title_font).elidedText(index.data(TITLE_ROLE) or "", Qt.ElideRight, width)
        painter.drawText(QRectF(left, rect.top() + 4, width, 18), Qt.AlignLeft | Qt.AlignVCenter, title)

        date_font = QFont(option.font)
        date_font.setPointSizeF(max(7.0, option.font.pointSizeF() - 1.5))
        painter.setFont(date_font)
        painter.setPen(QColor(pal["muted"]))
        painter.drawText(QRectF(left, rect.top() + 22, width, 16), Qt.AlignLeft | Qt.AlignVCenter, index.data(DATE_ROLE) or "")
        painter.restore()

# --------------------------------------------------------------------------
# Keyword search over sessions (title, summary, transcript)
# --------------------------------------------------------------------------

import unicodedata as _ud

_SEARCH_CACHE: dict[str, tuple[float, str, str]] = {}


def _fold(text: str) -> str:
    """Lowercase without accents, for accent-insensitive search."""
    return "".join(c for c in _ud.normalize("NFD", text.lower()) if _ud.category(c) != "Mn")


def _session_text(path: Path) -> tuple[str, str]:
    """(searchable folded text, original text) for a session, cached by mtime."""
    files = [path / "summary.json", path / "transcript.txt"]
    stamp = max((f.stat().st_mtime for f in files if f.is_file()), default=0.0)
    cached = _SEARCH_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return cached[1], cached[2]
    parts = [path.name.replace("-", " ").replace("_", " ")]
    summary = path / "summary.json"
    if summary.is_file():
        try:
            data = json.loads(summary.read_text(encoding="utf-8"))
            parts += data.get("sujets") or []
            parts += data.get("decisions") or []
            for a in data.get("actions") or []:
                parts += [a.get("description") or "", *(a.get("etapes") or [])]
        except (ValueError, OSError):
            pass
    transcript = path / "transcript.txt"
    if transcript.is_file():
        try:
            parts.append(transcript.read_text(encoding="utf-8"))
        except OSError:
            pass
    original = "\n".join(str(p) for p in parts)
    folded = _fold(original)
    _SEARCH_CACHE[str(path)] = (stamp, folded, original)
    return folded, original


def _search_snippet(folded: str, original: str, word: str, width: int = 70) -> str:
    """A short excerpt around the first match (folding keeps positions for
    most French text; good enough for a tooltip)."""
    i = folded.find(word)
    if i < 0 or len(folded) != len(original):
        return ""
    start, end = max(0, i - width), min(len(original), i + len(word) + width)
    return ("…" if start else "") + " ".join(original[start:end].split()) + ("…" if end < len(original) else "")

# --------------------------------------------------------------------------
# Settings dialog
# --------------------------------------------------------------------------

# What the Settings connection panel needs per AI provider: the .env name of
# its key, where users create one, and what a key looks like.
_AI_PROVIDERS = {
    "anthropic": {
        "label": "Claude (Anthropic)", "key_env": "ANTHROPIC_API_KEY",
        "key_url": "https://console.anthropic.com/settings/keys", "key_hint": "sk-ant-…",
    },
    "openai": {
        "label": "ChatGPT (OpenAI)", "key_env": "OPENAI_API_KEY",
        "key_url": "https://platform.openai.com/api-keys", "key_hint": "sk-…",
    },
    "azure_openai": {
        "label": "Copilot (Azure OpenAI)", "key_env": "AZURE_OPENAI_API_KEY",
        "key_url": "https://portal.azure.com/", "key_hint": "",
    },
    "gemini": {
        "label": "Gemini (Google)", "key_env": "GEMINI_API_KEY",
        "key_url": "https://aistudio.google.com/app/apikey", "key_hint": "AIza…",
    },
}


def _friendly_ai_error(exc: Exception, lang: str) -> str:
    """Turn a provider SDK error into one short, actionable sentence."""
    text = str(exc)
    low = text.lower()
    if "missing required setting" in low:
        return tr("ai_err_missing", lang)
    if any(k in low for k in ("401", "403", "invalid api key", "incorrect api key", "api key not valid",
                               "authentication", "permission_denied", "unauthorized")):
        return tr("ai_err_auth", lang)
    if any(k in low for k in ("insufficient_quota", "credit balance", "quota", "billing", "429")):
        return tr("ai_err_quota", lang)
    if any(k in low for k in ("404", "not_found", "model_not_found", "does not exist", "deploymentnotfound")):
        return tr("ai_err_model", lang)
    if any(k in low for k in ("connection", "timed out", "timeout", "getaddrinfo", "name resolution")):
        return tr("ai_err_network", lang)
    return text[:200]


_UPDATE_CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000  # every 6 hours


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = config.load_gui_settings()
        self.lang = self.settings.get("language", "en")
        self.setWindowTitle(tr("settings_title", self.lang))
        theme = self.settings.get("theme", "dark")
        pal = palette(theme)
        self.setObjectName("root")
        self.setStyleSheet(TeamScribeWidget._stylesheet(theme, 100))
        self.resize(420, 640)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(18, 16, 18, 8)
        title = QLabel(tr("settings_heading", self.lang))
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        from . import __version__
        version_label = QLabel(tr("version_label", self.lang, version=__version__))
        version_label.setObjectName("muted")
        head.addWidget(title)
        head.addStretch()
        head.addWidget(version_label)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 4, 18, 12)
        layout.setSpacing(6)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        def section(key: str) -> QVBoxLayout:
            label = QLabel(tr(key, self.lang).upper())
            label.setObjectName("section")
            layout.addSpacing(8)
            layout.addWidget(label)
            card = QFrame()
            card.setObjectName("card")
            inner = QVBoxLayout(card)
            inner.setContentsMargins(12, 8, 12, 8)
            inner.setSpacing(6)
            layout.addWidget(card)
            return inner

        def divider(inner: QVBoxLayout) -> None:
            line = QFrame()
            line.setObjectName("divider")
            inner.addWidget(line)

        def switch_row(inner: QVBoxLayout, key: str, checked: bool, hint_key: str = "") -> _Switch:
            row = QHBoxLayout()
            texts = QVBoxLayout()
            texts.setSpacing(1)
            label = QLabel(tr(key, self.lang))
            label.setWordWrap(True)
            texts.addWidget(label)
            if hint_key:
                hint = QLabel(tr(hint_key, self.lang))
                hint.setObjectName("muted")
                hint.setWordWrap(True)
                texts.addWidget(hint)
            switch = _Switch()
            switch.colors = (pal["accent"], pal["line"], "#ffffff")
            switch.setChecked(checked)
            row.addLayout(texts, 1)
            row.addWidget(switch, 0, Qt.AlignVCenter)
            inner.addLayout(row)
            return switch

        def field(inner: QVBoxLayout, key: str, widget, note_keys=()) -> None:
            label = QLabel(tr(key, self.lang).rstrip(" :"))
            inner.addWidget(label)
            inner.addWidget(widget)
            for nk in note_keys:
                note = QLabel(tr(nk, self.lang))
                note.setObjectName("muted")
                note.setWordWrap(True)
                inner.addWidget(note)

        # Démarrage et enregistrement
        rec = section("section_recording")
        self.startup_box = switch_row(rec, "launch_at_startup", shortcuts.is_startup_enabled())
        divider(rec)
        self.ontop_box = switch_row(rec, "always_on_top", self.settings.get("always_on_top", True))
        divider(rec)
        self.summarize_box = switch_row(rec, "auto_summarize", self.settings.get("auto_summarize", True))
        divider(rec)
        self.copy_meeting_box = switch_row(rec, "auto_copy_meeting", self.settings.get("auto_copy_meeting", True), "auto_copy_meeting_hint")

        # IA et langue
        ai = section("section_ai")
        self.provider_combo = QComboBox()
        for code, info in _AI_PROVIDERS.items():
            self.provider_combo.addItem(info["label"], code)
        current_provider = self.settings.get("llm_provider") or config.llm_provider()
        codes = list(_AI_PROVIDERS)
        self.provider_combo.setCurrentIndex(codes.index(current_provider) if current_provider in codes else 0)
        field(ai, "ai_provider_label", self.provider_combo)

        # Connection panel: where to get a key, paste it, test it — no .env editing.
        self.ai_steps = QLabel()
        self.ai_steps.setObjectName("muted")
        self.ai_steps.setWordWrap(True)
        self.ai_steps.setOpenExternalLinks(True)
        ai.addWidget(self.ai_steps)
        self.ai_inputs: dict[str, QLineEdit] = {}
        self.ai_input_labels: dict[str, QLabel] = {}
        for name, label_key in (
            ("key", "ai_key_label"),
            ("AZURE_OPENAI_ENDPOINT", "ai_endpoint_label"),
            ("AZURE_OPENAI_DEPLOYMENT", "ai_deployment_label"),
        ):
            label = QLabel(tr(label_key, self.lang))
            edit = QLineEdit()
            edit.setClearButtonEnabled(True)
            edit.textChanged.connect(self._remember_ai_input)
            ai.addWidget(label)
            ai.addWidget(edit)
            self.ai_input_labels[name], self.ai_inputs[name] = label, edit
        self.ai_inputs["key"].setEchoMode(QLineEdit.Password)
        test_row = QHBoxLayout()
        self.ai_test_btn = QPushButton(tr("ai_test_btn", self.lang))
        self.ai_test_btn.clicked.connect(self._test_ai_connection)
        self.ai_test_result = QLabel("")
        self.ai_test_result.setWordWrap(True)
        test_row.addWidget(self.ai_test_btn, 0, Qt.AlignTop)
        test_row.addWidget(self.ai_test_result, 1)
        ai.addLayout(test_row)
        privacy = QLabel(tr("ai_key_privacy", self.lang))
        privacy.setObjectName("muted")
        privacy.setWordWrap(True)
        ai.addWidget(privacy)
        # Values typed per provider, kept while switching providers; saved on Save.
        self._ai_typed: dict[str, dict[str, str]] = {code: {} for code in _AI_PROVIDERS}
        self._ai_filling = False
        self.provider_combo.currentIndexChanged.connect(self._show_ai_provider)
        self._show_ai_provider()
        divider(ai)
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
        field(ai, "language_label", self.lang_combo, ("language_restart_note", "language_transcription_note"))

        # Apparence
        look = section("section_appearance")
        self.palette_combo = QComboBox()
        self.palette_combo.setIconSize(QSize(46, 14))
        for pid in PALETTE_ORDER:
            self.palette_combo.addItem(_swatch_icon(PALETTES[pid]["swatch"]), tr(f"palette_{pid}", self.lang), pid)
        self.palette_combo.setCurrentIndex(PALETTE_ORDER.index(palette_id(theme)))
        field(look, "palette_label", self.palette_combo)
        divider(look)
        self.glass_label = QLabel()
        look.addWidget(self.glass_label)
        self.glass_slider = QSlider(Qt.Horizontal)
        self.glass_slider.setRange(20, 100)
        self.glass_slider.setValue(self.settings.get("glass_opacity", 100))
        self.glass_slider.valueChanged.connect(self._update_glass_label)
        self._update_glass_label(self.glass_slider.value())
        look.addWidget(self.glass_slider)

        # Outils
        tools = section("section_tools")
        tools_row = QHBoxLayout()
        desktop_btn = QPushButton(tr("create_desktop_shortcut", self.lang))
        desktop_btn.clicked.connect(self._create_desktop_shortcut)
        self.devices_btn = QPushButton(tr("check_audio", self.lang))
        self.devices_btn.clicked.connect(self._run_selftest)
        tools_row.addWidget(desktop_btn)
        tools_row.addWidget(self.devices_btn)
        tools.addLayout(tools_row)

        # Mises à jour
        upd = section("section_updates")
        self.update_status_label = QLabel("…")
        self.update_status_label.setWordWrap(True)
        upd.addWidget(self.update_status_label)
        self._link_color = pal["text"]
        self.update_notes_label = QLabel("")
        self.update_notes_label.setObjectName("muted")
        self.update_notes_label.setOpenExternalLinks(True)
        upd.addWidget(self.update_notes_label)
        update_row = QHBoxLayout()
        check_update_btn = QPushButton(tr("check_now", self.lang))
        check_update_btn.clicked.connect(self._check_update_now)
        self.apply_update_btn = QPushButton(tr("update_now", self.lang))
        self.apply_update_btn.setObjectName("primary")
        self.apply_update_btn.clicked.connect(self._apply_update_now)
        update_row.addWidget(check_update_btn)
        update_row.addWidget(self.apply_update_btn)
        upd.addLayout(update_row)
        self._refresh_update_status((parent._update_info if parent else None) or {})
        layout.addStretch()

        footer = QHBoxLayout()
        footer.setContentsMargins(18, 10, 18, 14)
        kofi_label = QLabel(tr("kofi_link", self.lang).replace("<a ", f"<a style='color: {pal['muted']};' "))
        kofi_label.setObjectName("muted")
        kofi_label.setOpenExternalLinks(True)
        save_btn = QPushButton(tr("save", self.lang))
        save_btn.setObjectName("primary")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton(tr("cancel", self.lang))
        cancel_btn.clicked.connect(self.reject)
        footer.addWidget(kofi_label)
        footer.addStretch()
        footer.addWidget(cancel_btn)
        footer.addWidget(save_btn)
        outer.addLayout(footer)

    def _refresh_update_status(self, info: dict) -> None:
        available = bool(info.get("available"))
        if info.get("error"):
            self.update_status_label.setText(tr("update_check_failed", self.lang, error=info["error"]))
        elif available:
            self.update_status_label.setText(tr(
                "update_available_version", self.lang,
                latest=info.get("latest", ""), current=info.get("current", ""),
            ))
        else:
            self.update_status_label.setText(tr("update_up_to_date", self.lang))
        self.apply_update_btn.setEnabled(available)
        self.update_notes_label.setVisible(available)
        if available:
            self.update_notes_label.setText(
                f"<a style='color: {self._link_color};' href='{info.get('url', '')}'>"
                f"{tr('update_whats_new', self.lang)}</a>"
            )

    def _check_update_now(self) -> None:
        self.update_status_label.setText(tr("update_checking", self.lang))
        QApplication.processEvents()
        info = update.check_for_update()
        parent = self.parent()
        if parent is not None:
            parent._on_update_checked(info)
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
            parent = self.parent()
            update.apply_update(
                (parent._update_info if parent is not None else None),
                log=lambda msg: (self.update_status_label.setText(msg), QApplication.processEvents()),
            )
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

    # -- AI connection panel ----------------------------------------------

    def _show_ai_provider(self) -> None:
        """Refill the connection panel for the provider picked in the combo."""
        code = self.provider_combo.currentData()
        info = _AI_PROVIDERS[code]
        typed = self._ai_typed[code]
        link_color = palette(self.settings.get("theme", "dark"))["text"]
        domain = info["key_url"].split("/")[2]
        self.ai_steps.setText(tr(
            "ai_key_steps", self.lang,
            link=f"<a style='color: {link_color};' href='{info['key_url']}'>{domain}</a>",
        ))
        self._ai_filling = True
        is_azure = code == "azure_openai"
        for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
            self.ai_input_labels[name].setVisible(is_azure)
            self.ai_inputs[name].setVisible(is_azure)
            self.ai_inputs[name].setText(typed.get(name, config.env(name, "") or "") if is_azure else "")
        self.ai_inputs["AZURE_OPENAI_ENDPOINT"].setPlaceholderText("https://<resource>.openai.azure.com/")
        self.ai_inputs["AZURE_OPENAI_DEPLOYMENT"].setPlaceholderText("gpt-4o")
        key_edit = self.ai_inputs["key"]
        key_edit.setText(typed.get("key", ""))
        if info["key_env"] not in config.missing_provider_settings(code):
            saved = config.env(info["key_env"], "") or ""
            key_edit.setPlaceholderText(tr("ai_key_saved", self.lang, tail=saved[-4:]))
        elif info["key_hint"]:
            key_edit.setPlaceholderText(tr("ai_key_placeholder_hint", self.lang, hint=info["key_hint"]))
        else:
            key_edit.setPlaceholderText(tr("ai_key_placeholder", self.lang))
        self._ai_filling = False
        self.ai_test_result.setText(
            tr("ai_status_ready", self.lang) if not config.missing_provider_settings(code)
            else tr("ai_status_missing", self.lang)
        )

    def _remember_ai_input(self) -> None:
        if self._ai_filling:
            return
        typed = self._ai_typed[self.provider_combo.currentData()]
        for name, edit in self.ai_inputs.items():
            if edit.isVisible() or name == "key":
                typed[name] = edit.text().strip()

    def _ai_env_values(self, code: str) -> dict[str, str]:
        """.env values typed for ``code`` (only non-empty ones; empty = keep saved)."""
        typed = self._ai_typed[code]
        values = {_AI_PROVIDERS[code]["key_env"]: typed.get("key", "")}
        if code == "azure_openai":
            values["AZURE_OPENAI_ENDPOINT"] = typed.get("AZURE_OPENAI_ENDPOINT", "")
            values["AZURE_OPENAI_DEPLOYMENT"] = typed.get("AZURE_OPENAI_DEPLOYMENT", "")
        return {name: value for name, value in values.items() if value}

    def _test_ai_connection(self) -> None:
        from . import summarize

        code = self.provider_combo.currentData()
        values = self._ai_env_values(code)
        label = _AI_PROVIDERS[code]["label"]
        lang = self.lang
        self.ai_test_btn.setEnabled(False)
        self.ai_test_result.setText(tr("ai_testing", lang))

        def work() -> str:
            summarize.test_connection(code, values)
            return tr("ai_test_ok", lang, provider=label)

        def show(text: str) -> None:
            self.ai_test_btn.setEnabled(True)
            self.ai_test_result.setText(text)

        self._ai_test_worker = TaskWorker(work)
        self._ai_test_worker.finished_ok.connect(show)
        self._ai_test_worker.failed.connect(
            lambda err: show(tr("ai_test_fail", lang, error=_friendly_ai_error(Exception(err), lang)))
        )
        self._ai_test_worker.start()

    _PROBE_SECONDS = 6

    def _run_selftest(self) -> None:
        """Record speakers and mic separately for a few seconds and report
        whether each one actually carries sound (not just its device name)."""
        from . import capture

        seconds = self._PROBE_SECONDS
        if QMessageBox.information(
            self, tr("audio_title", self.lang),
            tr("audio_check_intro", self.lang, seconds=seconds),
            QMessageBox.Ok | QMessageBox.Cancel, QMessageBox.Ok,
        ) != QMessageBox.Ok:
            return

        lang = self.lang

        def verdict(rms: float) -> str:
            # Same thresholds as `teamscribe selftest`.
            if rms >= 0.01:
                return tr("audio_level_ok", lang)
            if rms > 0.0005:
                return tr("audio_level_quiet", lang)
            return tr("audio_level_silent", lang)

        def work() -> str:
            result = capture.probe(seconds)
            loop, mic = result["loopback"], result["microphone"]
            return tr(
                "audio_check_levels", lang,
                speakers=loop["name"], speakers_verdict=verdict(loop["rms"]),
                mic=mic["name"], mic_verdict=verdict(mic["rms"]),
            )

        def restore_button() -> None:
            self.devices_btn.setEnabled(True)
            self.devices_btn.setText(tr("check_audio", lang))

        self.devices_btn.setEnabled(False)
        self.devices_btn.setText(tr("audio_check_running", lang, seconds=seconds))
        self._probe_worker = TaskWorker(work)
        self._probe_worker.finished_ok.connect(
            lambda msg: (restore_button(), QMessageBox.information(self, tr("audio_title", lang), msg))
        )
        self._probe_worker.failed.connect(
            lambda err: (restore_button(), QMessageBox.warning(self, "TeamScribe", err))
        )
        self._probe_worker.start()

    def done(self, result: int) -> None:
        # accept()/reject() both end here: let a running audio/AI check finish so
        # its QThread isn't destroyed mid-run when the dialog goes away.
        for attr in ("_probe_worker", "_ai_test_worker"):
            worker = getattr(self, attr, None)
            if worker is not None and worker.isRunning():
                worker.finished_ok.disconnect()
                worker.failed.disconnect()
                worker.wait()
        super().done(result)

    def accept(self) -> None:
        try:
            shortcuts.set_startup_enabled(self.startup_box.isChecked())
        except Exception as exc:
            QMessageBox.warning(
                self, "TeamScribe", tr("startup_toggle_failed", self.lang, error=exc)
            )
            return

        provider = self.provider_combo.currentData()
        # Keys typed in the connection panel go to the local .env (never the
        # registry or GitHub); set_env_value also applies them right away.
        for code in _AI_PROVIDERS:
            for name, value in self._ai_env_values(code).items():
                config.set_env_value(name, value)
        missing = config.missing_provider_settings(provider)
        if missing and QMessageBox.warning(
            self, "TeamScribe",
            tr("ai_provider_missing", self.lang, provider=self.provider_combo.currentText(),
               settings="\n".join(f"  • {name}" for name in missing), path=config.ENV_PATH),
            QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Cancel,
        ) != QMessageBox.Save:
            return

        previous_lang = self.settings.get("language", "en")
        self.settings["auto_summarize"] = self.summarize_box.isChecked()
        self.settings["auto_copy_meeting"] = self.copy_meeting_box.isChecked()
        self.settings["always_on_top"] = self.ontop_box.isChecked()
        self.settings["theme"] = self.palette_combo.currentData()
        self.settings["glass_opacity"] = self.glass_slider.value()
        self.settings["llm_provider"] = self.provider_combo.currentData()
        self.settings["language"] = self.lang_combo.currentData()
        config.save_gui_settings(self.settings)
        language_changed = self.settings["language"] != previous_lang
        super().accept()
        if language_changed and self.parent() is not None:
            # Defer until after dialog.exec() unwinds so QApplication.quit()
            # inside restart_app() doesn't fire while exec() is still on the stack.
            QTimer.singleShot(0, self.parent().restart_app)


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
        self.resize(320, 620)

        self._drag_offset = None
        self._pinned = False
        self._minimized = False
        self._expanded_size: QSize | None = None
        self._record_worker: RecordWorker | None = None
        # Workers whose recording phase is done but that are still
        # transcribing/summarizing in the background — kept referenced so a
        # new recording can start immediately without waiting on them.
        self._background_workers: list[RecordWorker] = []
        self._task_worker: TaskWorker | None = None
        self._update_check_worker: UpdateCheckWorker | None = None
        self._update_info: dict = {"available": False}
        # The widget usually stays open for days (it starts with Windows), so
        # look for a new Release periodically, not only at launch.
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(_UPDATE_CHECK_INTERVAL_MS)
        self._update_timer.timeout.connect(self.check_for_update)
        self._update_timer.start()
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
        c = palette(theme)
        alpha = max(0, min(100, glass_opacity)) / 100 * 255
        r, g, b = c["root"]
        return (
            f"#root {{ background: rgba({r}, {g}, {b}, {alpha:.0f}); border: 1px solid {c['border']}; border-radius: 12px; }}"
            f"QDialog#root {{ background: rgb({r}, {g}, {b}); border: none; border-radius: 0; }}"
            # Message boxes opened from a styled dialog inherit its text colour,
            # so give them the palette background too (not the Windows default).
            f"QMessageBox {{ background: rgb({r}, {g}, {b}); }}"
            f"QLabel {{ color: {c['text']}; background: transparent; }}"
            f"QLabel#title {{ font-weight: 600; letter-spacing: 0.3px; }}"
            f"QLabel#muted {{ color: {c['muted']}; font-size: 11px; }}"
            f"QLabel#section {{ color: {c['muted']}; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; }}"
            f"QPushButton {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['line']};"
            " border-radius: 8px; padding: 6px 10px; }"
            f"QPushButton:hover {{ background: {c['surface_hover']}; }}"
            f"QPushButton:disabled {{ color: {c['faint']}; }}"
            "QPushButton#icon { background: transparent; border: none; padding: 0; border-radius: 6px; }"
            f"QPushButton#icon:hover {{ background: {c['surface_hover']}; }}"
            f"QPushButton#icon:checked {{ background: {c['selected']}; }}"
            f"QPushButton#link {{ background: transparent; border: none; color: {c['muted']}; padding: 2px 2px; font-size: 11px; }}"
            f"QPushButton#link:hover {{ color: {c['text']}; }}"
            f"QPushButton#record {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['line']};"
            " border-radius: 9px; padding: 8px; font-weight: 600; }"
            f"QPushButton#record:hover {{ background: {c['surface_hover']}; }}"
            f"QPushButton#recording {{ background: {c['red']}; color: #ffffff; border: none;"
            " border-radius: 9px; padding: 8px; font-weight: 600; }"
            f"QPushButton#primary, QPushButton#send {{ background: {c['accent']}; color: {c['on_accent']}; border: none; }}"
            f"QPushButton#primary:hover, QPushButton#send:hover {{ background: {c['accent_hover']}; }}"
            "QPushButton#send { border-radius: 8px; padding: 0; min-width: 30px; max-width: 30px; min-height: 30px; max-height: 30px; }"
            f"QLineEdit, QPlainTextEdit {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['line']};"
            f" border-radius: 8px; padding: 6px 8px; selection-background-color: {c['accent']}; }}"
            f"QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {c['accent']}; }}"
            f"QListWidget {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['line']};"
            " border-radius: 10px; padding: 3px; outline: none; }"
            f"QComboBox {{ background: {c['field']}; color: {c['text']}; border: 1px solid {c['line']};"
            " border-radius: 8px; padding: 5px 8px; }"
            f"QComboBox:focus {{ border: 1px solid {c['accent']}; }}"
            "QComboBox::drop-down { border: none; width: 22px; }"
            f"QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['line']};"
            f" selection-background-color: {c['selected']}; selection-color: {c['text']}; outline: none; padding: 4px; }}"
            f"QSlider::groove:horizontal {{ height: 4px; background: {c['line']}; border-radius: 2px; }}"
            f"QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}"
            f"QSlider::handle:horizontal {{ background: {c['accent']}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}"
            f"QFrame#card {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; }}"
            "QFrame#card QLabel { background: transparent; }"
            f"QFrame#divider {{ background: {c['line']}; max-height: 1px; min-height: 1px; border: none; }}"
            "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }"
            f"QPushButton#tile {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 0; text-align: left; }}"
            f"QPushButton#tile:hover {{ background: {c['surface_hover']}; border: 1px solid {c['faint']}; }}"
            f"QMenu {{ background: {c['surface']}; color: {c['text']}; border: 1px solid {c['line']}; border-radius: 10px; padding: 6px; }}"
            "QMenu::item { padding: 6px 18px 6px 8px; border-radius: 6px; }"
            f"QMenu::item:selected {{ background: {c['selected']}; }}"
            f"QMenu::item:disabled {{ color: {c['faint']}; }}"
            f"QMenu::separator {{ height: 1px; background: {c['line']}; margin: 5px 6px; }}"
            "QMenu::icon { padding-left: 6px; }"
            f"QLabel#menuHeader {{ color: {c['muted']}; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; padding: 4px 10px 2px 10px; }}"
            f"QLabel#danger {{ color: {c['red']}; }}"
            "QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 1px; }"
            f"QScrollBar::handle:vertical {{ background: {c['scroll']}; border-radius: 3px; min-height: 24px; }}"
            "QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; height: 0; }"
        )

    def _apply_theme(self) -> None:
        theme = self.settings.get("theme", "dark")
        glass_opacity = self.settings.get("glass_opacity", 100)
        self.pal = palette(theme)
        self.root_frame.setStyleSheet(self._stylesheet(theme, glass_opacity))
        self._delegate.pal = self.pal
        self.session_list.viewport().update()
        self.size_grip.set_color(self.pal["faint"])
        self.privacy_switch.colors = (self.pal["accent"], self.pal["line"], "#ffffff")
        self.privacy_switch.update()
        self._apply_icons()

    def _apply_icons(self) -> None:
        pal = self.pal
        ic = lambda name, color=None, size=16: _line_icon(name, color or pal["muted"], size)
        self.pin_btn.setIcon(ic("pin"))
        self.settings_btn.setIcon(ic("gear"))
        self.restart_btn.setIcon(ic("power"))
        self.minimize_btn.setIcon(ic("chevron_up" if self._minimized else "chevron_down"))
        self.close_btn.setIcon(ic("close"))
        self.refresh_btn.setIcon(ic("refresh"))
        self.folder_btn.setIcon(ic("folder"))
        self._search_action.setIcon(ic("search"))
        self.assist_btn.setIcon(ic("arrow_up", pal["on_accent"]))
        self.planner_icon.setPixmap(_line_icon("tasks", pal["text"], 18).pixmap(18, 18))
        self.planner_chevron.setPixmap(_line_icon("chevron_right", pal["faint"], 14).pixmap(14, 14))
        recording = self.record_btn.objectName() == "recording"
        self.record_btn.setIcon(ic("stop" if recording else "rec", "#ffffff" if recording else pal["red"]))
        self.status_dot.setStyleSheet(f"color: {pal['red'] if recording else pal['faint']}; font-size: 10px;")
        self._set_mini_record_btn_color(recording)

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
        title = QLabel("TeamScribe")
        title.setObjectName("title")
        self.pin_btn = QPushButton()
        self.pin_btn.setFixedSize(24, 24)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip(tr("pin_tooltip_off", self.lang))
        self.pin_btn.toggled.connect(self.set_pinned)
        self.settings_btn = QPushButton()
        self.settings_btn.setFixedSize(24, 24)
        self.settings_btn.setToolTip(tr("settings_tooltip", self.lang))
        self.settings_btn.clicked.connect(self.open_settings)
        self.update_badge = QLabel(self.settings_btn)
        self.update_badge.setFixedSize(8, 8)
        self.update_badge.setStyleSheet(
            "background: #e5484d; border-radius: 4px; border: none;"
        )
        self.update_badge.move(16, -1)
        self.update_badge.hide()
        self.restart_btn = restart_btn = QPushButton()
        restart_btn.setFixedSize(24, 24)
        restart_btn.setToolTip(tr("restart_tooltip", self.lang))
        restart_btn.clicked.connect(self.restart_app)
        self.minimize_btn = QPushButton()
        self.minimize_btn.setFixedSize(24, 24)
        self.minimize_btn.setToolTip(tr("minimize_tooltip_collapse", self.lang))
        self.minimize_btn.clicked.connect(self.toggle_minimize)
        self.close_btn = close_btn = QPushButton()
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.close)
        self.mini_record_btn = QPushButton()
        self.mini_record_btn.setObjectName("icon")
        self.mini_record_btn.setFixedSize(24, 24)
        self.mini_record_btn.setToolTip(tr("record_start", self.lang))
        self.mini_record_btn.clicked.connect(self.toggle_recording)
        self.mini_record_btn.hide()

        for btn in (self.pin_btn, self.settings_btn, restart_btn, self.minimize_btn, close_btn):
            btn.setObjectName("icon")
        title_row.setSpacing(4)
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

        # New-release banner: hidden until a newer official Release exists.
        self.update_banner = QPushButton()
        self.update_banner.setObjectName("primary")
        self.update_banner.setCursor(Qt.PointingHandCursor)
        self.update_banner.clicked.connect(self.open_settings)
        self.update_banner.hide()
        layout.addWidget(self.update_banner)

        # Collapsible content — everything below the title bar is wrapped in
        # a single widget so toggle_minimize() can show/hide it in one call.
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)

        content_layout.setSpacing(8)

        # Status + privacy switch on one line.
        status_row = QHBoxLayout()
        self.status_label = QLabel(tr("status_ready", self.lang))
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        # Privacy mode switch: when ON the raw audio file is deleted after
        # transcription so only the text transcript is kept.
        privacy_label = QLabel(tr("privacy_mode_label", self.lang))
        privacy_label.setObjectName("muted")
        self.privacy_switch = _Switch()
        # Privacy mode ON by default (keep_audio defaults to False now)
        privacy_on_default = not self.settings.get("keep_audio", False)
        self.privacy_switch.setChecked(privacy_on_default)
        self.privacy_switch.setToolTip(tr("privacy_mode_tooltip", self.lang))
        self.privacy_switch.toggled.connect(self.set_keep_audio)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(privacy_label)
        status_row.addWidget(self.privacy_switch)
        content_layout.addLayout(status_row)
        self.privacy_hint = QLabel(tr("privacy_mode_hint", self.lang))
        self.privacy_hint.setObjectName("muted")
        self.privacy_hint.setVisible(False)
        content_layout.addWidget(self.privacy_hint)
        # Restoring the saved choice at launch: no consent prompt (it was
        # given when the switch was turned off).
        self.set_keep_audio(privacy_on_default, ask=False)

        # Record button
        self.record_btn = QPushButton(_plain(tr("record_start", self.lang)))
        self.record_btn.setObjectName("record")
        self.record_btn.setMinimumHeight(36)
        self.record_btn.clicked.connect(self.toggle_recording)
        self._set_record_btn_color(recording=False)
        content_layout.addWidget(self.record_btn)

        # Meetings: section title with small refresh / folder buttons.
        sessions_head = QHBoxLayout()
        sessions_title = QLabel(tr("recent_sessions", self.lang).rstrip(" :").upper())
        sessions_title.setObjectName("section")
        self.refresh_btn = refresh_btn = QPushButton()
        refresh_btn.setObjectName("icon")
        refresh_btn.setFixedSize(24, 22)
        refresh_btn.setToolTip(tr("refresh_list_btn", self.lang))
        refresh_btn.clicked.connect(self.refresh_sessions)
        self.folder_btn = open_folder_btn = QPushButton()
        open_folder_btn.setObjectName("icon")
        open_folder_btn.setFixedSize(24, 22)
        open_folder_btn.setToolTip(tr("open_folder_btn", self.lang))
        open_folder_btn.clicked.connect(self.open_sessions_folder)
        sessions_head.addWidget(sessions_title)
        sessions_head.addStretch()
        sessions_head.addWidget(refresh_btn)
        sessions_head.addWidget(open_folder_btn)
        content_layout.addSpacing(4)
        content_layout.addLayout(sessions_head)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(tr("search_placeholder", self.lang))
        self._search_action = self.search_edit.addAction(QIcon(), QLineEdit.LeadingPosition)
        self.search_edit.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh_sessions)
        self.search_edit.textChanged.connect(lambda _t: self._search_timer.start())
        content_layout.addWidget(self.search_edit)

        self.session_list = QListWidget()
        self._delegate = _SessionDelegate(self.session_list)
        self.session_list.setItemDelegate(self._delegate)
        self.session_list.setMouseTracking(True)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.session_list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        # Always room for at least 5 meetings.
        self.session_list.setMinimumHeight(5 * _SessionDelegate.ROW + 10)
        self.session_list.setSelectionMode(QListWidget.ExtendedSelection)
        self.session_list.itemDoubleClicked.connect(self._open_session_folder)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._show_session_menu)
        self.session_list.installEventFilter(self)
        content_layout.addWidget(self.session_list, 1)

        # In-app adjustments: one request about the selected meeting (or the
        # most recent one). The AI redoes/edits the summary, copies it for
        # the Dashboard, or answers a question.
        assist_title = QLabel(tr("assist_title", self.lang).upper())
        assist_title.setObjectName("section")
        content_layout.addSpacing(4)
        content_layout.addWidget(assist_title)
        self.assist_target = QLabel()
        self.assist_target.setObjectName("muted")
        self.session_list.itemSelectionChanged.connect(self._update_assist_target)
        assist_row = QHBoxLayout()
        assist_row.setSpacing(6)
        self.assist_edit = QLineEdit()
        self.assist_edit.setPlaceholderText(tr("assist_placeholder_short", self.lang))
        self.assist_edit.setToolTip(tr("assist_tooltip", self.lang))
        self.assist_edit.returnPressed.connect(self.run_assist)
        self.assist_btn = QPushButton()
        self.assist_btn.setObjectName("send")
        self.assist_btn.setToolTip(tr("assist_send", self.lang))
        self.assist_btn.clicked.connect(self.run_assist)
        assist_row.addWidget(self.assist_edit, 1)
        assist_row.addWidget(self.assist_btn)
        content_layout.addLayout(assist_row)
        content_layout.addWidget(self.assist_target)

        # Planner push: secondary, as a discreet link.
        # Planner push: a long, low tile (icon, title + hint, chevron).
        self.push_btn = QPushButton()
        self.push_btn.setObjectName("tile")
        self.push_btn.setCursor(Qt.PointingHandCursor)
        self.push_btn.setMinimumHeight(48)
        self.push_btn.setToolTip(tr("planner_tile_hint", self.lang))
        self.push_btn.clicked.connect(self.run_push_tasks)
        tile = QHBoxLayout(self.push_btn)
        tile.setContentsMargins(12, 6, 10, 6)
        tile.setSpacing(10)
        self.planner_icon = QLabel()
        texts = QVBoxLayout()
        texts.setSpacing(0)
        tile_title = QLabel(tr("planner_tile_title", self.lang))
        tile_hint = QLabel(tr("planner_tile_hint", self.lang))
        tile_hint.setObjectName("muted")
        texts.addWidget(tile_title)
        texts.addWidget(tile_hint)
        self.planner_chevron = QLabel()
        for w in (self.planner_icon, tile_title, tile_hint, self.planner_chevron):
            w.setAttribute(Qt.WA_TransparentForMouseEvents)
        tile.addWidget(self.planner_icon)
        tile.addLayout(texts, 1)
        tile.addWidget(self.planner_chevron)
        content_layout.addSpacing(2)
        content_layout.addWidget(self.push_btn)

        # Frameless windows have no native resize border, so a visible grip
        # in the corner is what lets the user actually resize the widget.
        # Native QSizeGrip painting is theme/style dependent (and was nearly
        # invisible against the light theme); _GripDots draws an explicit
        # diagonal dot pattern instead, so the resize handle always looks
        # like one regardless of theme.
        self.size_grip = _GripDots(self, "#999999")
        self.size_grip.setFixedSize(16, 16)
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 0, 0)
        grip_row.addStretch()
        grip_row.addWidget(self.size_grip, 0, Qt.AlignBottom | Qt.AlignRight)
        content_layout.addLayout(grip_row)

        layout.addWidget(self.content_widget)
        self.setMinimumSize(260, 560)

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
        pal = getattr(self, "pal", palette("dark"))
        self.mini_record_btn.setIcon(_line_icon("stop" if recording else "rec", pal["red"]))
        self.mini_record_btn.setToolTip(tr("record_stop" if recording else "record_start", self.lang))

    def _set_minimized(self, minimized: bool, save: bool = True) -> None:
        self._minimized = minimized
        if minimized:
            self._expanded_size = self.size()
            self.content_widget.hide()
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
            self.minimize_btn.setIcon(_line_icon("chevron_up", self.pal["muted"]))
            self.minimize_btn.setToolTip(tr("minimize_tooltip_expand", self.lang))
            self.mini_record_btn.show()
            # Let the layout recalculate with content hidden, then force the
            # window to the resulting hint height.
            QApplication.processEvents()
            self.resize(self.width(), self.sizeHint().height())
        else:
            self.mini_record_btn.hide()
            self.setMinimumSize(260, 560)
            self.setMaximumSize(16777215, 16777215)
            self.content_widget.show()
            if self._expanded_size is not None:
                self.resize(self._expanded_size)
            self.minimize_btn.setIcon(_line_icon("chevron_down", self.pal["muted"]))
            self.minimize_btn.setToolTip(tr("minimize_tooltip_collapse", self.lang))
        if save:
            self.settings["minimized"] = minimized
            config.save_gui_settings(self.settings)

    def set_pinned(self, pinned: bool) -> None:
        self._pinned = pinned
        self.pin_btn.setToolTip(
            tr("pin_tooltip_on", self.lang) if pinned else tr("pin_tooltip_off", self.lang)
        )
        self.settings["pinned"] = pinned
        config.save_gui_settings(self.settings)

    def set_keep_audio(self, privacy_on: bool, ask: bool = True) -> None:
        if not privacy_on and ask:
            # Privacy mode is being turned OFF — show a consent notice and let
            # the user confirm before applying the change.
            confirm = QMessageBox.warning(
                self,
                tr("privacy_off_title", self.lang),
                tr("privacy_off_body", self.lang),
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if confirm != QMessageBox.Ok:
                # User cancelled — revert the switch silently.
                self.privacy_switch.blockSignals(True)
                self.privacy_switch.setChecked(True)
                self.privacy_switch.blockSignals(False)
                return

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
        if self._record_worker is not None or self._background_workers:
            QMessageBox.information(
                self, "TeamScribe", tr("restart_blocked", self.lang)
            )
            return
        self._save_position()
        python = Path(sys.executable)
        pythonw = python.with_name("pythonw.exe")
        exe = str(pythonw) if pythonw.is_file() else str(python)
        subprocess.Popen([exe, "-m", "teamscribe.cli", "gui"], cwd=str(config.ROOT), creationflags=config.NO_WINDOW)
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
        available = bool(self._update_info.get("available"))
        self.update_badge.setVisible(available)
        if available:
            self.update_banner.setText(tr("update_banner", self.lang, latest=self._update_info.get("latest", "")))
        self.update_banner.setVisible(available)

    # -- sessions list --------------------------------------------------

    def open_sessions_folder(self) -> None:
        config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(config.SESSIONS_DIR))

    def refresh_sessions(self) -> None:
        selected = {str(p) for p in self._selected_sessions()} if hasattr(self, "session_list") else set()
        self.session_list.clear()
        if not config.SESSIONS_DIR.is_dir():
            self._update_assist_target()
            return
        hidden = set(self.settings.get("hidden_sessions", []))
        # Most recent recording first: the date comes from the folder name
        # (the folder is renamed after the meeting's topic, so sorting by
        # name would sort by title).
        dated = []
        for p in config.SESSIONS_DIR.glob("*"):
            if p.is_dir() and p.name not in hidden:
                try:
                    dated.append((export.session_date(p), p))
                except (OSError, ValueError):
                    continue
        dated.sort(key=lambda x: x[0], reverse=True)
        query = _fold(self.search_edit.text().strip()) if hasattr(self, "search_edit") else ""
        words = query.split()
        shown = 0
        for when, path in dated:
            tooltip = path.name
            if words:
                folded, original = _session_text(path)
                if not all(w in folded for w in words):
                    continue
                snippet = _search_snippet(folded, original, words[0])
                if snippet:
                    tooltip = f"{path.name}\n\n{snippet}"
            elif shown >= 20:
                break
            # summary.json keeps a fixed name regardless of the contextual
            # rename in naming.finalize_session, so it's a stable signal.
            has_summary = (path / "summary.json").is_file()
            title = export.title_from_name(path)
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, str(path))
            item.setData(TITLE_ROLE, title)
            item.setData(DATE_ROLE, _format_when(when, self.lang))
            item.setData(DONE_ROLE, has_summary)
            status = tr("session_ready" if has_summary else "session_processing", self.lang)
            item.setToolTip(f"{tooltip}\n{status}")
            self.session_list.addItem(item)
            if str(path) in selected:
                item.setSelected(True)
            shown += 1
        if words and not shown:
            empty = QListWidgetItem(tr("search_no_result", self.lang))
            empty.setFlags(Qt.NoItemFlags)
            self.session_list.addItem(empty)
        self._update_assist_target()

    def _assist_sessions(self) -> list[Path]:
        """The meetings the AI works on: the selection, else the most recent."""
        chosen = [p for p in self._selected_sessions() if p]
        if chosen:
            return chosen
        if not config.SESSIONS_DIR.is_dir():
            return []
        hidden = set(self.settings.get("hidden_sessions", []))
        dated = []
        for p in config.SESSIONS_DIR.glob("*"):
            if p.is_dir() and p.name not in hidden:
                try:
                    dated.append((export.session_date(p), p))
                except (OSError, ValueError):
                    continue
        return [max(dated, key=lambda x: x[0])[1]] if dated else []

    def _update_assist_target(self) -> None:
        if not hasattr(self, "assist_target"):
            return
        targets = self._assist_sessions()
        explicit = bool(self._selected_sessions())
        if not targets:
            self.assist_target.setText(tr("assist_no_session", self.lang))
            self.assist_target.setToolTip("")
        elif len(targets) > 1:
            self.assist_target.setText(tr("assist_target_many", self.lang, count=len(targets)))
            self.assist_target.setToolTip("\n".join(export.title_from_name(p) for p in targets))
        else:
            key = "assist_target_selected" if explicit else "assist_target_latest"
            name = export.title_from_name(targets[0])
            self.assist_target.setToolTip(name)
            name = QFontMetrics(self.assist_target.font()).elidedText(name, Qt.ElideRight, max(80, self.assist_target.width() - 110))
            self.assist_target.setText(tr(key, self.lang, name=name))

    def _copy_meetings(self, sessions: list[Path]) -> None:
        """Copy one meeting (teamscribe.meeting/1) or several (a bundle the
        Dashboard imports one after the other)."""
        ready = [s for s in sessions if (s / "summary.json").is_file()]
        if not ready:
            return
        try:
            text = export.export_sessions(ready)
        except Exception as exc:
            QMessageBox.warning(self, "TeamScribe", tr("copy_meeting_failed", self.lang, error=exc))
            return
        QGuiApplication.clipboard().setText(text)
        self.status_label.setText(
            tr("meeting_copied", self.lang) if len(ready) == 1 else tr("meetings_copied", self.lang, count=len(ready))
        )

    def _show_reply(self, text: str) -> None:
        theme = self.settings.get("theme", "dark")
        _ReplyDialog(self, tr("assist_reply_title", self.lang), text, self.lang, self._stylesheet(theme, 100)).exec()

    def run_assist(self) -> None:
        request = self.assist_edit.text().strip()
        if not request:
            self.assist_edit.setFocus()
            return
        sessions = self._assist_sessions()
        if not sessions:
            QMessageBox.information(self, "TeamScribe", tr("assist_no_session", self.lang))
            return

        from . import summarize as summarize_mod

        def fn():
            return json.dumps(summarize_mod.assist_sessions(sessions, request, log=lambda *_: None))

        def done(msg: str) -> None:
            try:
                result = json.loads(msg)
            except ValueError:
                result = {"action": "answer", "reply": msg, "updated": []}
            reply = result.get("reply") or tr("assist_done", self.lang)
            self.assist_edit.clear()
            self.status_label.setText(tr("assist_done", self.lang))
            if result.get("action") == "copy_for_dashboard":
                self._copy_meetings(sessions)
            elif result.get("action") == "update_summaries":
                names = ", ".join(export.title_from_name(Path(n)) for n in result.get("updated") or [])
                self._show_reply(f"{reply}\n\n{tr('assist_updated', self.lang, names=names)}")
            else:
                self._show_reply(reply)

        label = export.title_from_name(sessions[0]) if len(sessions) == 1 else tr("assist_target_many", self.lang, count=len(sessions))
        self._run_task(fn, tr("assist_working", self.lang, name=label), "", on_done=done)

    def _selected_session(self) -> Path | None:
        item = self.session_list.currentItem()
        if item is None:
            return None
        return Path(item.data(Qt.UserRole))

    def _selected_sessions(self) -> list[Path]:
        return [Path(item.data(Qt.UserRole)) for item in self.session_list.selectedItems()]

    def _open_session_folder(self, item: QListWidgetItem) -> None:
        path = Path(item.data(Qt.UserRole))
        os.startfile(str(path))

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
        if item is None or item.data(Qt.UserRole) is None:
            return
        # Keep the existing multi-selection (shift/ctrl-click) intact; only
        # fall back to selecting just this item if it isn't already part of
        # the current selection (e.g. a right-click outside any selection).
        if item not in self.session_list.selectedItems():
            self.session_list.setCurrentItem(item)
        path = Path(item.data(Qt.UserRole))
        selected = self._selected_sessions()
        many = len(selected) > 1
        pal = self.pal
        ic = lambda name, color=None: _line_icon(name, color or pal["muted"], 16)
        menu = QMenu(self)
        menu.setStyleSheet(self.root_frame.styleSheet())
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)

        def header(text: str) -> None:
            label = QLabel(text.upper())
            label.setObjectName("menuHeader")
            action = QWidgetAction(menu)
            action.setDefaultWidget(label)
            action.setEnabled(False)
            menu.addAction(action)

        title = export.title_from_name(path) if not many else tr("assist_target_many", self.lang, count=len(selected)).split(": ", 1)[-1]
        header(QFontMetrics(menu.font()).elidedText(title, Qt.ElideRight, 220))
        if not many:
            menu.addAction(ic("folder"), tr("open_folder_action", self.lang), lambda: os.startfile(str(path)))
            audio_files = list(path.glob("*.wav"))
            if audio_files:
                menu.addAction(ic("audio"), tr("open_audio_action", self.lang), lambda: os.startfile(str(audio_files[0])))
            notes_files = (
                list(path.glob("*_notes.txt"))
                or list(path.glob("summary.txt"))
                or list(path.glob("*_notes.md"))
                or list(path.glob("summary.md"))
            )
            if notes_files:
                menu.addAction(ic("doc"), tr("open_notes_action", self.lang), lambda: webbrowser.open(notes_files[0].as_uri()))
            menu.addSeparator()

        chosen = [p for p in selected if (p / "summary.json").is_file()]
        if len(chosen) > 1:
            menu.addAction(ic("copy"), tr("copy_meetings_action", self.lang, count=len(chosen)), lambda: self._copy_meetings(chosen))
        elif (path / "summary.json").is_file():
            menu.addAction(ic("copy"), tr("copy_meeting_action", self.lang), lambda: self._copy_meeting(path))
        menu.addAction(ic("ask"), tr("ask_ai_action", self.lang), lambda: (self.assist_edit.setFocus(), self.assist_edit.selectAll()))
        if not many:
            redo = menu.addAction(ic("refresh"), tr("resummarize_action", self.lang), self.run_summarize)
            redo.setEnabled((path / "transcript.txt").is_file())
            menu.addAction(ic("tasks"), tr("push_planner_action", self.lang), self.run_push_tasks)
        menu.addSeparator()
        menu.addAction(ic("trash", pal["red"]), tr("delete_action", self.lang), self._delete_selected_sessions)
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
        self.record_btn.setObjectName("recording" if recording else "record")
        # Re-polish so the stylesheet rule for the new object name applies.
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        if hasattr(self, "pal"):
            self._apply_icons()

    def toggle_recording(self) -> None:
        if self._record_worker is not None:
            self.status_label.setText(tr("stopping", self.lang))
            self.record_btn.setEnabled(False)
            self.mini_record_btn.setEnabled(False)
            self._record_worker.stop()
            return

        worker = RecordWorker(
            do_summarize=self.settings.get("auto_summarize", True),
            keep_audio=self.settings.get("keep_audio", True),
        )
        worker.tick.connect(self._on_tick)
        worker.info.connect(self._on_info)
        worker.phase.connect(self._on_phase)
        worker.renamed.connect(self.refresh_sessions)
        worker.finished_ok.connect(self._on_record_done)
        worker.failed.connect(self._on_record_failed)
        self._record_worker = worker
        self._background_workers.append(worker)
        worker.start()

        self.record_btn.setText(_plain(tr("record_stop", self.lang)))
        self._set_record_btn_color(recording=True)
        self._set_mini_record_btn_color(recording=True)
        self.status_label.setText(tr("listening", self.lang, time="00:00:00"))

    def _unblock_record_button(self) -> None:
        self._record_worker = None
        self.record_btn.setEnabled(True)
        self.mini_record_btn.setEnabled(True)
        self.record_btn.setText(_plain(tr("record_start", self.lang)))
        self._set_record_btn_color(recording=False)
        self._set_mini_record_btn_color(recording=False)

    def _on_phase(self, phase: str) -> None:
        _phase_keys = {
            "transcribing": "phase_transcribing",
            "summarizing": "phase_summarizing",
            "saving": "phase_saving",
        }
        worker = self.sender()
        if phase == "transcribing" and worker is self._record_worker:
            # The recording itself just finished; unblock the record button
            # right away so back-to-back meetings don't have to wait for
            # this session's transcription/summarization to finish in the
            # background before a new one can start.
            self._unblock_record_button()
        # Don't let a background session's progress clobber the status line
        # of a recording that's actively in progress.
        if self._record_worker is None:
            text = tr(_phase_keys.get(phase, phase), self.lang)
            self.status_label.setText(text)
            self.mini_record_btn.setToolTip(text)
            self.mini_record_btn.setIcon(_line_icon("busy", self.pal["muted"]))

    def _on_tick(self, elapsed: float) -> None:
        if self.sender() is not self._record_worker:
            return
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        self.status_label.setText(tr("listening", self.lang, time=f"{h:02d}:{m:02d}:{s:02d}"))

    def _on_info(self, mic_name: str, speaker_name: str) -> None:
        if self.sender() is not self._record_worker:
            return
        self.status_label.setToolTip(f"mic: {mic_name}\nspeakers: {speaker_name}")

    def _on_record_done(self, session: Path) -> None:
        worker = self.sender()
        if worker in self._background_workers:
            self._background_workers.remove(worker)
        if self._record_worker is None:
            self.status_label.setText(tr("record_done", self.lang, name=session.name))
        self.refresh_sessions()
        if self.settings.get("auto_copy_meeting", True) and (session / "summary.json").is_file():
            self._copy_meeting(session, quiet=self._record_worker is not None)

    def _copy_meeting(self, session: Path, quiet: bool = False) -> None:
        """Copy the portable meeting summary (teamscribe.meeting/1), ready to
        paste into a task manager, and keep a meeting.json in the session."""
        try:
            text = export.export_session(session)
        except Exception as exc:
            QMessageBox.warning(self, "TeamScribe", tr("copy_meeting_failed", self.lang, error=exc))
            return
        QGuiApplication.clipboard().setText(text)
        if not quiet:
            self.status_label.setText(tr("meeting_copied", self.lang))

    def _on_record_failed(self, message: str) -> None:
        worker = self.sender()
        if worker is self._record_worker:
            # Failed before the transcribing-phase handoff (e.g. capture
            # error), so nothing else has unblocked the button yet.
            self._unblock_record_button()
        elif worker in self._background_workers:
            self._background_workers.remove(worker)
        if self._record_worker is None:
            self.status_label.setText(tr("error", self.lang))
        QMessageBox.warning(self, "TeamScribe", tr("record_failed", self.lang, error=message))

    # -- summarize / push-tasks --------------------------------------------------

    def _run_task(self, fn, busy_text: str, done_prefix: str, on_done=None) -> None:
        if self._task_worker is not None:
            return
        self.status_label.setText(busy_text)
        self._task_worker = TaskWorker(fn)
        self._task_worker.finished_ok.connect(
            lambda msg: (self._on_task_done(done_prefix, msg), on_done and on_done(msg))
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


def _log_to_file() -> None:
    """Under pythonw.exe stdout/stderr are None: anything printed (a library
    progress bar, a traceback) is lost or raises, and a native crash leaves
    no trace. Send them to teamscribe.log, with faulthandler for hard crashes."""
    if sys.stderr is not None and sys.stdout is not None:
        return
    import faulthandler
    from datetime import datetime

    try:
        if config.LOG_PATH.is_file() and config.LOG_PATH.stat().st_size > 1_000_000:
            config.LOG_PATH.replace(config.LOG_PATH.with_suffix(".log.old"))
        log = open(config.LOG_PATH, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    log.write(f"\n=== TeamScribe started {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log
    faulthandler.enable(log)


def main() -> None:
    _log_to_file()
    app = QApplication.instance() or QApplication([])
    widget = TeamScribeWidget()
    widget.show()
    app.exec()


if __name__ == "__main__":
    main()
