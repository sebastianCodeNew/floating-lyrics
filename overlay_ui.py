"""Frameless, translucent, click-through lyrics overlay with smooth scrolling."""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from config import Settings, save_settings
from lyrics_fetcher import Lyrics
from media_listener import TrackSnapshot
from text_utils import LyricLine, promoted_line_index, wrap_text

log = logging.getLogger(__name__)

PADDING_X = 28
PADDING_Y = 16
LINE_GAP = 10
MAX_SCREEN_WIDTH_FRACTION = 0.34
TICK_MS = 30
SCROLL_ANIM_MS = 200
# Start the promotion this far before the next line's timestamp. The scroll
# takes 200 ms, so without a lead the highlight would land ~100 ms after the
# vocal. Clamped to half the gap between lines so fast passages do not overlap.
SCROLL_LEAD_MS = 140
# 16 ms ~= 60 fps. Windows coalesces timers below the system tick
# (~15.6 ms), so asking for less just wastes wakeups.
SCROLL_ANIM_INTERVAL_MS = 16

# The sprite holds the glyph box plus room for the outline and drop shadow.
_SPRITE_PAD = 5.0
_SPRITE_SHADOW = 2.0


@dataclass(frozen=True, slots=True)
class _LineSprite:
    """One pre-rendered lyric line (shadow, outline and fill baked in)."""

    pixmap: QPixmap
    width: float       # logical width, including _SPRITE_PAD on both sides
    pad: float         # logical padding from the pixmap edge to the glyph box

    @property
    def glyph_width(self) -> float:
        return self.width - self.pad * 2


class OverlayWindow(QWidget):
    """Two-line always-on-top lyric display with smooth scroll transitions."""

    track_changed = Signal(str, str, str, str, float)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._snapshot: TrackSnapshot | None = None
        self._lyrics: Lyrics | None = None
        self._lyrics_resolved = False
        self._current_key: str | None = None
        self._positioned = False
        self._drag_offset: QPoint | None = None

        # Scroll animation state (timer-driven tween; no Qt property machinery)
        self._scroll_offset = 0.0
        self._scroll_distance = 0.0
        self._scroll_progress = 0.0
        self._scroll_started_at = 0.0
        self._scroll_active = False
        self._easing = QEasingCurve(QEasingCurve.Type.InOutQuad)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setInterval(SCROLL_ANIM_INTERVAL_MS)
        self._scroll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._scroll_timer.timeout.connect(self._advance_scroll)

        # Current displayed text
        self._active_text = ""
        self._next_text = ""
        self._active_lines: list[str] = []
        self._next_lines: list[str] = []
        self._active_sprites: list[_LineSprite] = []
        self._next_sprites: list[_LineSprite] = []

        # Text being scrolled (for animation rendering)
        self._outgoing_active_lines: list[str] = []
        self._shared_lines: list[str] = []
        self._incoming_next_lines: list[str] = []
        self._outgoing_sprites: list[_LineSprite] = []
        self._shared_sprites: list[_LineSprite] = []
        self._shared_from_sprites: list[_LineSprite] = []
        self._incoming_sprites: list[_LineSprite] = []
        self._outgoing_block_h = 0
        self._incoming_active = ""
        self._incoming_next = ""

        self.setWindowTitle("Floating Lyrics")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self._active_font = QFont("Segoe UI")
        self._active_font.setPixelSize(settings.active_px)
        self._active_font.setWeight(QFont.Weight.DemiBold)

        # Both rows use the same weight so promoting a line only changes its
        # size and brightness. A weight change mid-scroll is a visible pop,
        # because Qt can only select discrete font weights.
        self._next_font = QFont("Segoe UI")
        self._next_font.setPixelSize(settings.next_px)
        self._next_font.setWeight(QFont.Weight.DemiBold)

        self._active_metrics = QFontMetrics(self._active_font)
        self._next_metrics = QFontMetrics(self._next_font)

        self._apply_flags()
        self._apply_geometry()
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
        if snapshot is None:
            self._snapshot = None
            self._current_key = None
            self._lyrics = None
            self._lyrics_resolved = False
            self._active_text = ""
            self._next_text = ""
            self._scroll_offset = 0.0
            self._stop_scroll()
            self.hide()
            return

        if snapshot.key != self._current_key:
            self._current_key = snapshot.key
            self._lyrics = None
            self._lyrics_resolved = False
            self._active_text = ""
            self._next_text = ""
            self._scroll_offset = 0.0
            self._stop_scroll()
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

    def set_lyrics(self, key: str, lyrics: Lyrics | None) -> None:
        if key != self._current_key:
            return
        self._lyrics = lyrics
        self._lyrics_resolved = True
        # Size the fixed surface for the longest wrapped line in this song, so
        # three-line wraps are never clipped and no resize happens per line.
        self._apply_geometry()
        self._resize_to_content()
        self._refresh_text()

    def set_font_size(self, preset: str) -> None:
        # Settle any running transition before the fonts change underneath it.
        self._complete_scroll()
        self._settings.font_size = preset
        self._active_font.setPixelSize(self._settings.active_px)
        self._next_font.setPixelSize(self._settings.next_px)
        self._active_metrics = QFontMetrics(self._active_font)
        self._next_metrics = QFontMetrics(self._next_font)
        self._apply_geometry()
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
        self._apply_geometry()
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
        self._drag_offset = (
            event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        )
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
    # Scroll animation
    # ------------------------------------------------------------------

    def _stop_scroll(self) -> None:
        """Abandon a transition (used when the track itself changes)."""
        self._scroll_timer.stop()
        self._scroll_active = False
        self._scroll_offset = 0.0
        self._scroll_progress = 0.0
        self._outgoing_active_lines = []
        self._shared_lines = []
        self._incoming_next_lines = []
        self._outgoing_sprites = []
        self._shared_sprites = []
        self._shared_from_sprites = []
        self._incoming_sprites = []
        self._incoming_active = ""
        self._incoming_next = ""

    def _complete_scroll(self) -> None:
        """Jump to the end of a running transition (used before re-layout)."""
        if not self._scroll_active:
            return
        self._scroll_timer.stop()
        self._finish_scroll()

    def _start_scroll(self, outgoing_next: str, incoming_active: str,
                      incoming_next: str) -> None:
        """Start the timer-driven scroll from outgoing to incoming text.

        A two-line overlay only needs to travel one line: the outgoing *next*
        line is the incoming *active* line, so it glides up into the highlighted
        slot while the previous active line exits through the top.

        Everything is laid out so the final frame is pixel-identical to the
        settled state. That invariant is what removes the snap at the end.
        """
        self._scroll_timer.stop()

        screen = self.screen() or QGuiApplication.primaryScreen()
        available_width = screen.availableGeometry().width() if screen else 1920
        max_text_width = self._max_text_width(available_width)
        measure = self._active_metrics.horizontalAdvance

        # Lines leaving through the top.
        self._outgoing_active_lines = list(self._active_lines)
        self._outgoing_sprites = list(self._active_sprites)

        # The shared line: old "next" becomes new "active". It is wrapped with
        # the active font from the start so its line count never changes when
        # it grows, and so the final frame matches the settled layout exactly.
        if self._next_text == outgoing_next:
            self._shared_lines = list(self._next_lines)
        else:
            self._shared_lines = wrap_text(outgoing_next, max_text_width, measure)

        # Render the shared line at its final size once; the animation scales
        # the sprite down, which avoids re-rasterising glyphs every frame.
        self._shared_sprites = [
            self._make_sprite(line, self._active_font, 255, 190)
            for line in self._shared_lines
        ]

        # The first frame must look exactly like the settled state, so keep the
        # native next-size sprites too and crossfade away from them. Scaling the
        # active-size sprite down to next size is close but not pixel-identical,
        # which would show as a faint flicker at the very start.
        if self._next_text == outgoing_next:
            self._shared_from_sprites = list(self._next_sprites)
        else:
            self._shared_from_sprites = [
                self._make_sprite(line, self._next_font, 255, 255)
                for line in self._shared_lines
            ]

        # Lines entering from the bottom.
        self._incoming_next_lines = wrap_text(
            incoming_next, max_text_width, measure
        )
        self._incoming_sprites = [
            self._make_sprite(line, self._next_font, 255, 255)
            for line in self._incoming_next_lines
        ]

        # Remember the target state; installed when the scroll completes.
        self._incoming_active = incoming_active
        self._incoming_next = incoming_next

        self._outgoing_block_h = (
            len(self._outgoing_active_lines) * self._active_metrics.height()
        )

        # Travel exactly one active row: the shared line starts where the old
        # "next" row sat and finishes at PADDING_Y.
        self._scroll_distance = float(self._outgoing_block_h + LINE_GAP)

        self._scroll_offset = 0.0
        self._scroll_progress = 0.0
        self._scroll_started_at = time.monotonic()
        self._scroll_active = True

        # No resize here: the window is a fixed surface (see _apply_geometry),
        # so the animation only repaints and never moves the native window.
        self._scroll_timer.start()

    def _advance_scroll(self) -> None:
        """Advance the tween by one frame using real elapsed time.

        InOutQuad starts and ends gently, so the first and last frames move
        less than a pixel instead of front-loading a visible jump (ease-out
        cubic put ~22% of the distance into the first 16 ms).
        """
        elapsed = time.monotonic() - self._scroll_started_at
        raw = min(1.0, elapsed / (SCROLL_ANIM_MS / 1000.0))
        self._scroll_progress = self._easing.valueForProgress(raw)
        self._scroll_offset = self._scroll_distance * self._scroll_progress
        # Paint synchronously: the sprite blit costs well under a millisecond,
        # and update() can defer the frame to the next event-loop pass, which
        # shows up as uneven 16/31 ms steps.
        self.repaint()

        if raw >= 1.0:
            self._scroll_timer.stop()
            self._finish_scroll()

    def _finish_scroll(self) -> None:
        """Install the incoming text now that the scroll has finished.

        The stored blocks were wrapped with the active font at scroll start,
        so they are exactly what the final animation frame drew. Re-wrapping
        here would risk a different line count and a visible jump.
        """
        self._scroll_active = False
        self._scroll_progress = 0.0
        self._scroll_offset = 0.0

        self._active_text = self._incoming_active
        self._next_text = self._incoming_next
        self._active_lines = list(self._shared_lines)
        self._next_lines = list(self._incoming_next_lines)
        # Reuse the sprites rendered at scroll start: they are exactly the
        # pixels the final animation frame drew.
        self._active_sprites = list(self._shared_sprites)
        self._next_sprites = list(self._incoming_sprites)

        self._outgoing_active_lines = []
        self._shared_lines = []
        self._incoming_next_lines = []
        self._outgoing_sprites = []
        self._shared_sprites = []
        self._shared_from_sprites = []
        self._incoming_sprites = []
        self._incoming_active = ""
        self._incoming_next = ""
        self.update()

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

        # Determine new text
        lyrics = self._lyrics
        if lyrics is None:
            if not self._lyrics_resolved:
                # Still waiting on the network. Keep whatever is on screen
                # rather than flashing the raw title over good lyrics.
                return
            new_active = snapshot.title
            new_next = "No lyrics found"
        elif lyrics.instrumental:
            new_active = snapshot.title
            new_next = "Instrumental"
        elif lyrics.synced:
            lines: tuple[LyricLine, ...] = lyrics.lines
            position = snapshot.position_at()
            # Promote the upcoming line slightly before its timestamp so the
            # highlight settles with the vocal rather than a whole animation
            # late. Only while playing: seeking and pausing must not jump ahead.
            index = promoted_line_index(
                lines,
                position,
                playing=snapshot.playing,
                lead_seconds=SCROLL_LEAD_MS / 1000.0,
            )
            if index < 0:
                new_active = ""
                new_next = lines[0].text if lines else ""
            else:
                new_active = lines[index].text
                new_next = lines[index + 1].text if index + 1 < len(lines) else ""
        elif lyrics.plain:
            # No timestamps available: spread the lines evenly across the track
            # duration so the text still advances with the song.
            new_active, new_next = self._plain_lyrics_window(snapshot)
        else:
            new_active = snapshot.title
            new_next = "No synced lyrics"

        # A running scroll already knows where it is going. If playback still
        # agrees, let it finish; a seek, pause or skipped line means the target
        # is stale, so abandon the animation and show reality immediately.
        if self._scroll_active:
            if new_active == self._incoming_active and new_next == self._incoming_next:
                return
            self._stop_scroll()
            self._apply_text_immediate(new_active, new_next)
            return

        # No change needed
        if new_active == self._active_text and new_next == self._next_text:
            return

        # First line ever: show immediately instead of sliding from nothing.
        if not self._active_text and not self._next_text:
            self._apply_text_immediate(new_active, new_next)
            return

        # A seek or a jump of more than one line has no shared line to promote.
        # Snapping is honest; sliding the wrong line up would look broken.
        if new_active != self._next_text:
            self._apply_text_immediate(new_active, new_next)
            return

        # Normal line change: the old "next" line becomes the new active line.
        log.debug("Scroll: %r -> %r", self._active_text, new_active)
        self._start_scroll(self._next_text, new_active, new_next)

    def _plain_lyrics_window(self, snapshot: TrackSnapshot) -> tuple[str, str]:
        """Pick a two-line window over unsynced lyrics.

        Without timestamps there is no exact position, so the lines are spread
        evenly across the track duration. This keeps the text moving with the
        song instead of sitting on the first line for the whole track.
        """
        plain = (self._lyrics.plain or "") if self._lyrics else ""
        lines = [line.strip() for line in plain.splitlines() if line.strip()]
        if not lines:
            return snapshot.title, "No lyrics"

        duration = snapshot.duration
        position = snapshot.position_at()

        if duration > 0 and len(lines) > 1:
            index = int(position / duration * len(lines))
            index = max(0, min(index, len(lines) - 1))
        else:
            index = 0

        active = lines[index]
        upcoming = lines[index + 1] if index + 1 < len(lines) else ""
        return active, upcoming

    def _apply_text_immediate(self, active: str, next_text: str) -> None:
        """Show text immediately without animation."""
        self._stop_scroll()
        self._active_text = active
        self._next_text = next_text
        self._resize_to_content()
        self.update()

    def _resize_to_content(self) -> None:
        """Re-wrap the current text. The window geometry itself is fixed.

        Both rows are wrapped with the *active* font metrics. The next line is
        drawn smaller, so this can wrap it one word earlier than strictly
        needed, but it guarantees the line count is identical before and after
        promotion. Otherwise a line that grows into a second row would jump.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        available_width = screen.availableGeometry().width() if screen else 1920
        max_text_width = self._max_text_width(available_width)
        measure = self._active_metrics.horizontalAdvance

        self._active_lines = wrap_text(
            self._active_text, max_text_width, measure
        )
        self._next_lines = wrap_text(
            self._next_text, max_text_width, measure
        )
        self._active_sprites = [
            self._make_sprite(line, self._active_font, 255, 190)
            for line in self._active_lines
        ]
        # Rendered at full opacity; the paint code applies the dimming, so the
        # same sprite can be faded in during a transition.
        self._next_sprites = [
            self._make_sprite(line, self._next_font, 255, 255)
            for line in self._next_lines
        ]

    def _max_text_width(self, available_width: int) -> int:
        return max(
            220, int(available_width * MAX_SCREEN_WIDTH_FRACTION) - PADDING_X * 2
        )

    def _max_wrap_lines(self, available_width: int) -> int:
        """Longest wrapped line in the current lyrics, in rows.

        Sizing the surface per song (rather than per line) keeps the native
        window fixed during playback while still giving every line room.
        """
        lyrics = self._lyrics
        if lyrics is None:
            return 2

        if lyrics.lines:
            texts = [line.text for line in lyrics.lines]
        else:
            texts = [
                line for line in (lyrics.plain or "").splitlines() if line.strip()
            ]
        if not texts:
            return 2

        max_text_width = self._max_text_width(available_width)
        measure = self._active_metrics.horizontalAdvance
        longest = 1
        for text in texts:
            longest = max(longest, len(wrap_text(text, max_text_width, measure)))
        # Sanity bound: one pathological line must not create a screen-high
        # transparent window.
        return max(1, min(longest, 4))

    def _apply_geometry(self) -> None:
        """Size the window once per song, big enough for wrapped lines, and pin it.

        A fixed surface keeps scrolling smooth: there is no native resize per
        line change, and the window never drifts away from where the user put it.
        The height comes from the longest line in the current lyrics, so a
        three-line wrap is never clipped.
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        available_width = available.width() if available else 1920
        max_text_width = self._max_text_width(available_width)

        width = max_text_width + PADDING_X * 2

        max_lines = self._max_wrap_lines(available_width)
        a_h = self._active_metrics.height()
        n_h = self._next_metrics.height()

        # During a scroll the surface must hold the outgoing active block, the
        # shared line at its final (active) size, and the incoming next block.
        scroll_height = max_lines * (2 * a_h + n_h) + 2 * LINE_GAP
        settled_height = max_lines * (a_h + n_h) + LINE_GAP
        content_height = max(scroll_height, settled_height)

        height = PADDING_Y * 2 + content_height

        if not self._positioned:
            self._positioned = True
            if available is not None:
                saved = self._settings
                if saved.x is not None and saved.y is not None:
                    point = QPoint(saved.x, saved.y)
                    if self._is_on_some_screen(point):
                        self.setGeometry(
                            saved.x, saved.y, width, height
                        )
                        return
                x = available.x() + 40
                y = available.y() + 100
                margin = 16
                x = min(x, available.x() + available.width() - width - margin)
                y = min(y, available.y() + available.height() - height - margin)
                self.setGeometry(
                    max(available.x() + margin, x),
                    max(available.y() + margin, y),
                    width,
                    height,
                )
                self._remember_position()
                return

        # Repositioned already: keep the user's top-left corner, just resize.
        self.resize(width, height)

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def _make_sprite(
        self,
        text: str,
        font: QFont,
        fill_alpha: int,
        outline_alpha: int,
    ) -> _LineSprite:
        """Rasterise one line (shadow + outline + fill) into a pixmap.

        Rasterising glyph paths every frame is expensive, and a changing font
        size defeats Qt's glyph cache. Baking each line once and blitting it at
        a fractional offset is both faster and smoother.
        """
        if not text:
            return _LineSprite(QPixmap(), 0.0, _SPRITE_PAD)

        metrics = QFontMetrics(font)
        glyph_width = metrics.horizontalAdvance(text)
        ascent = metrics.ascent()
        logical_width = glyph_width + _SPRITE_PAD * 2
        logical_height = metrics.height() + _SPRITE_PAD * 2 + _SPRITE_SHADOW

        dpr = max(1.0, self.devicePixelRatioF())
        pixmap = QPixmap(
            math.ceil(logical_width * dpr), math.ceil(logical_height * dpr)
        )
        pixmap.fill(Qt.GlobalColor.transparent)
        pixmap.setDevicePixelRatio(dpr)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        path = QPainterPath()
        path.addText(QPointF(_SPRITE_PAD, _SPRITE_PAD + ascent), font, text)

        painter.save()
        painter.translate(0.0, _SPRITE_SHADOW)
        painter.fillPath(path, QColor(0, 0, 0, 110))
        painter.restore()

        pen = QPen(QColor(0, 0, 0, outline_alpha), 3.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(path, pen)
        painter.fillPath(path, QColor(255, 255, 255, fill_alpha))
        painter.end()

        return _LineSprite(pixmap, logical_width, _SPRITE_PAD)

    def _draw_sprite(
        self,
        painter: QPainter,
        sprite: _LineSprite,
        top: float,
        opacity: float,
        scale: float = 1.0,
    ) -> None:
        """Blit a sprite so its glyph box starts at ``top`` and is centred."""
        if sprite.pixmap.isNull() or opacity <= 0.0:
            return

        painter.save()
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        if scale == 1.0:
            x = (self.width() - sprite.glyph_width) / 2 - sprite.pad
            painter.drawPixmap(QPointF(x, top - sprite.pad), sprite.pixmap)
        else:
            x = (self.width() - sprite.glyph_width * scale) / 2
            painter.translate(x, top)
            painter.scale(scale, scale)
            painter.drawPixmap(QPointF(-sprite.pad, -sprite.pad), sprite.pixmap)
        painter.restore()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if not self._settings.locked:
            border_height = min(float(self.height()), self._border_height())
            rect = QRectF(
                1.0, 1.0, self.width() - 2.0, max(24.0, border_height - 2.0)
            )
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(QColor(0, 0, 0, 55))
            painter.drawRoundedRect(rect, 12, 12)

        active_metrics = self._active_metrics
        next_metrics = self._next_metrics
        a_h = active_metrics.height()
        n_h = next_metrics.height()

        # Keep the offset fractional: rounding to whole pixels makes the motion
        # visibly step at small sizes.
        scroll_offset = self._scroll_offset

        if self._scroll_active:
            # One column, three parts, all moving up together. The shared line
            # starts in the dim "next" slot and rises into the highlighted slot.
            progress = self._scroll_progress

            # Outgoing active block, exiting through the top. Fade the last
            # sliver so it is fully gone when the settled frame takes over.
            outgoing_opacity = (
                1.0 if progress <= 0.7 else max(0.0, (1.0 - progress) / 0.3)
            )
            top = PADDING_Y - scroll_offset
            for sprite in self._outgoing_sprites:
                self._draw_sprite(painter, sprite, top, outgoing_opacity)
                top += a_h

            # The shared line grows from next size to active size as it rises.
            # The sprite is rendered once at the final size and scaled, which
            # keeps the glyph cache warm and the motion continuous.
            next_px = self._next_font.pixelSize()
            active_px = self._active_font.pixelSize()
            shared_scale = max(
                0.01, (next_px + (active_px - next_px) * progress) / active_px
            )
            shared_h = n_h + (a_h - n_h) * progress

            shared_top = (
                PADDING_Y + self._outgoing_block_h + LINE_GAP - scroll_offset
            )
            # Crossfade from the native next-size sprite to the growing active
            # sprite. At progress 0 only the former is drawn (identical to the
            # settled frame), and by 25% the size difference is small enough
            # that dropping it is invisible.
            morph = min(1.0, progress / 0.25)
            from_opacity = (1.0 - morph) * (150.0 / 255.0)
            shared_opacity = ((150.0 + 105.0 * progress) / 255.0) * morph
            for index, to_sprite in enumerate(self._shared_sprites):
                if index < len(self._shared_from_sprites):
                    self._draw_sprite(
                        painter,
                        self._shared_from_sprites[index],
                        shared_top,
                        from_opacity,
                    )
                self._draw_sprite(
                    painter, to_sprite, shared_top, shared_opacity, shared_scale
                )
                shared_top += shared_h

            # Incoming block, rising in from the bottom. Fade it in over the
            # first half so new text does not pop into an empty area.
            incoming_opacity = min(1.0, progress / 0.55) * (150.0 / 255.0)
            incoming_top = shared_top + LINE_GAP
            for sprite in self._incoming_sprites:
                self._draw_sprite(
                    painter, sprite, incoming_top, incoming_opacity
                )
                incoming_top += n_h

        else:
            # Normal state: render current text
            top = PADDING_Y
            for sprite in self._active_sprites:
                self._draw_sprite(painter, sprite, top, 1.0)
                top += a_h

            top += LINE_GAP
            next_opacity = 150.0 / 255.0
            for sprite in self._next_sprites:
                self._draw_sprite(painter, sprite, top, next_opacity)
                top += n_h

        if (
            not self._active_lines
            and not self._next_lines
            and not self._scroll_active
            and not self._settings.locked
        ):
            hint_font = QFont("Segoe UI")
            hint_font.setPixelSize(18)
            painter.setFont(hint_font)
            painter.setPen(QColor(255, 255, 255, 170))
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "Drag to position, then lock from the tray",
            )

    def _border_height(self) -> float:
        """Height of the drag outline, hugging the visible text block.

        The window can be taller than the text to give the scroll headroom, so
        the outline follows the content instead of the full surface. During a
        transition it covers both the outgoing and incoming blocks.
        """
        if not self._active_lines and not self._next_lines:
            return float(self.height())

        a_h = self._active_metrics.height()
        n_h = self._next_metrics.height()
        if self._scroll_active:
            # Use the taller of the outgoing and incoming blocks for the whole
            # transition. The incoming line fades in below the outgoing one, so
            # a border that interpolated between them would cut through it.
            outgoing = (
                PADDING_Y * 2
                + len(self._outgoing_active_lines) * a_h
                + LINE_GAP
                + len(self._shared_lines) * n_h
            )
            incoming = (
                PADDING_Y * 2
                + len(self._shared_lines) * a_h
                + LINE_GAP
                + len(self._incoming_next_lines) * n_h
            )
            return max(outgoing, incoming)
        return (
            PADDING_Y * 2
            + len(self._active_lines) * a_h
            + LINE_GAP
            + len(self._next_lines) * n_h
        )

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        # The real device pixel ratio is known once the widget has a screen.
        # Rebuild the sprites so they stay crisp on scaled displays.
        self._resize_to_content()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._stop_scroll()
        self._tick_timer.stop()
        super().closeEvent(event)