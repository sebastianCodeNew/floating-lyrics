"""Frameless, translucent, click-through lyrics overlay window."""

from __future__ import annotations

import logging

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from config import Settings, save_settings
from lyrics_fetcher import Lyrics
from media_listener import TrackSnapshot
from text_utils import LyricLine, active_line_index, wrap_text

log = logging.getLogger(__name__)

PADDING_X = 28
PADDING_Y = 16
LINE_GAP = 8
MIN_WIDTH = 420
MAX_SCREEN_FRACTION = 0.86
TICK_MS = 100


class OverlayWindow(QWidget):
    """Two-line always-on-top lyric display."""

    track_changed = Signal(str, str, str, str, float)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._snapshot: TrackSnapshot | None = None
        self._lyrics: Lyrics | None = None
        self._current_key: str | None = None
        self._active_text = ""
        self._next_text = ""
        self._active_lines: list[str] = []
        self._next_lines: list[str] = []
        self._positioned = False
        self._drag_offset: QPoint | None = None

        self.setWindowTitle("Floating Lyrics")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._active_font = QFont("Segoe UI")
        self._active_font.setPixelSize(settings.active_px)
        self._active_font.setWeight(QFont.Weight.DemiBold)

        self._next_font = QFont("Segoe UI")
        self._next_font.setPixelSize(settings.next_px)
        self._next_font.setWeight(QFont.Weight.Normal)

        self._apply_flags()
        self._resize_to_content()

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(lambda _screen: self._ensure_on_screen())
            app.screenRemoved.connect(lambda _screen: self._ensure_on_screen())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_key(self) -> str | None:
        return self._current_key

    def on_snapshot(self, snapshot: TrackSnapshot | None) -> None:
        """Handle a new media snapshot from the listener thread."""
        if snapshot is None:
            self._snapshot = None
            self._current_key = None
            self._lyrics = None
            self._active_text = ""
            self._next_text = ""
            self.hide()
            return

        if snapshot.key != self._current_key:
            self._current_key = snapshot.key
            self._lyrics = None
            self.track_changed.emit(
                snapshot.key,
                snapshot.title,
                snapshot.artist,
                snapshot.album,
                snapshot.duration,
            )

        self._snapshot = snapshot
        if not self.isVisible():
            self._ensure_position()
            self.show()
        self._refresh_text()

        log.debug(
            "Snapshot: %s pos=%.1f/%.1f playing=%s",
            snapshot.title,
            snapshot.position_at(),
            snapshot.duration,
            snapshot.playing,
        )

    def set_lyrics(self, key: str, lyrics: Lyrics | None) -> None:
        """Apply lyrics only if they still belong to the current track."""
        if key != self._current_key:
            return
        self._lyrics = lyrics
        self._refresh_text()

    def set_font_size(self, preset: str) -> None:
        self._settings.font_size = preset
        self._active_font.setPixelSize(self._settings.active_px)
        self._next_font.setPixelSize(self._settings.next_px)
        self._resize_to_content()
        self._refresh_text()
        save_settings(self._settings)

    def set_locked(self, locked: bool) -> None:
        self._settings.locked = locked
        self._apply_flags()
        self.show()
        save_settings(self._settings)
        self.update()

    def reset_position(self) -> None:
        self._settings.x = None
        self._settings.y = None
        self._settings.screen = None
        self._positioned = False
        self._ensure_position()
        save_settings(self._settings)

    # ------------------------------------------------------------------
    # Window behaviour
    # ------------------------------------------------------------------

    def _apply_flags(self) -> None:
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.NoDropShadowWindowHint
        )
        if self._settings.locked:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, self._settings.locked
        )
        self.setCursor(
            Qt.CursorShape.ArrowCursor
            if self._settings.locked
            else Qt.CursorShape.SizeAllCursor
        )

    def _ensure_position(self) -> None:
        if self._positioned:
            return
        self._positioned = True

        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return

        saved = self._settings
        if saved.x is not None and saved.y is not None:
            point = QPoint(saved.x, saved.y)
            if self._is_on_some_screen(point):
                self.move(point)
                return

        available = screen.availableGeometry()
        # Default anchor: 40px in from the left, 100px from the top.
        x = available.x() + 40
        y = available.y() + 100
        margin = 16
        x = min(x, available.x() + available.width() - self.width() - margin)
        y = min(y, available.y() + available.height() - self.height() - margin)
        x = max(available.x() + margin, x)
        y = max(available.y() + margin, y)
        self.move(x, y)
        self._remember_position()

    def _is_on_some_screen(self, point: QPoint) -> bool:
        for screen in QGuiApplication.screens():
            if screen.availableGeometry().contains(point):
                return True
        return False

    def _ensure_on_screen(self) -> None:
        if not self.isVisible():
            return
        if not self._is_on_some_screen(self.pos()):
            self._positioned = False
            self._ensure_position()

    def _remember_position(self) -> None:
        self._settings.x = self.x()
        self._settings.y = self.y()
        screen = QGuiApplication.screenAt(self.geometry().center())
        self._settings.screen = screen.name() if screen else None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._settings.locked or event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is None:
            return
        self.move(event.globalPosition().toPoint() - self._drag_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_offset is None:
            return
        self._drag_offset = None
        self._remember_position()
        save_settings(self._settings)
        event.accept()

    # ------------------------------------------------------------------
    # Content
    # ------------------------------------------------------------------

    def _tick(self) -> None:
        if self._snapshot is not None:
            self._refresh_text()

    def _refresh_text(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return

        active, upcoming = self._compose(snapshot)
        if active == self._active_text and upcoming == self._next_text:
            return

        self._active_text = active
        self._next_text = upcoming
        self._resize_to_content()
        self.update()
        log.debug("Overlay: %r | %r", active, upcoming)

    def _compose(self, snapshot: TrackSnapshot) -> tuple[str, str]:
        lyrics = self._lyrics
        if lyrics is None:
            return snapshot.title, snapshot.artist

        if lyrics.instrumental:
            return snapshot.title, "Instrumental"

        if not lyrics.synced:
            return snapshot.title, "No synced lyrics"

        lines: tuple[LyricLine, ...] = lyrics.lines
        index = active_line_index(list(lines), snapshot.position_at())
        if index < 0:
            return "", lines[0].text
        active = lines[index].text
        upcoming = lines[index + 1].text if index + 1 < len(lines) else ""
        return active, upcoming

    def _resize_to_content(self) -> None:
        active_metrics = QFontMetrics(self._active_font)
        next_metrics = QFontMetrics(self._next_font)

        screen = self.screen() or QGuiApplication.primaryScreen()
        available_width = screen.availableGeometry().width() if screen else 1920
        # Cap line length so long lyrics wrap vertically instead of spanning
        # the whole screen. 1/3 of screen width is comfortable.
        max_text_width = max(220, int(available_width * 0.34) - PADDING_X * 2)

        self._active_lines = wrap_text(
            self._active_text, max_text_width, active_metrics.horizontalAdvance
        )
        self._next_lines = wrap_text(
            self._next_text, max_text_width, next_metrics.horizontalAdvance
        )

        widths = [active_metrics.horizontalAdvance(line) for line in self._active_lines]
        widths += [next_metrics.horizontalAdvance(line) for line in self._next_lines]
        needed = max([*widths, MIN_WIDTH - PADDING_X * 2])
        width = min(needed, max_text_width) + PADDING_X * 2

        height = (
            PADDING_Y * 2
            + len(self._active_lines) * active_metrics.height()
            + LINE_GAP
            + len(self._next_lines) * next_metrics.height()
        )
        height = max(height, PADDING_Y * 2 + active_metrics.height())

        old_center = self.geometry().center().x() if self._positioned else None
        old_bottom = self.geometry().bottom() if self._positioned else None

        self.resize(width, height)

        if old_center is not None and old_bottom is not None:
            # Keep the left edge fixed; grow rightward/downward as needed.
            self.move(self.x(), old_bottom - height + 1)
            self._remember_position()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        if not self._settings.locked:
            rect = self.rect().adjusted(1, 1, -1, -1)
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 0, 0, 55))
            painter.drawRoundedRect(rect, 12, 12)

        active_metrics = QFontMetrics(self._active_font)
        next_metrics = QFontMetrics(self._next_font)

        top = PADDING_Y
        for line in self._active_lines:
            self._draw_line(
                painter,
                line,
                self._active_font,
                active_metrics,
                top,
                QColor(255, 255, 255, 255),
                190,
            )
            top += active_metrics.height()

        top += LINE_GAP
        for line in self._next_lines:
            self._draw_line(
                painter,
                line,
                self._next_font,
                next_metrics,
                top,
                QColor(255, 255, 255, 150),
                150,
            )
            top += next_metrics.height()

        if not self._active_lines and not self._next_lines and not self._settings.locked:
            hint_font = QFont("Segoe UI")
            hint_font.setPixelSize(18)
            painter.setFont(hint_font)
            painter.setPen(QColor(255, 255, 255, 170))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Drag to position, then lock from the tray",
            )

    def _draw_line(
        self,
        painter: QPainter,
        text: str,
        font: QFont,
        metrics: QFontMetrics,
        top: int,
        color: QColor,
        outline_alpha: int,
    ) -> None:
        if not text:
            return
        text_width = metrics.horizontalAdvance(text)
        x = (self.width() - text_width) / 2
        baseline = top + metrics.ascent()

        path = QPainterPath()
        path.addText(QPointF(x, baseline), font, text)

        painter.save()
        painter.translate(0, 2)
        painter.fillPath(path, QColor(0, 0, 0, 110))
        painter.restore()

        pen = QPen(QColor(0, 0, 0, outline_alpha), 3.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, pen)
        painter.fillPath(path, color)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._tick_timer.stop()
        super().closeEvent(event)
