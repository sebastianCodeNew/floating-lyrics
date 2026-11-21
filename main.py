"""Floating Lyrics - a minimal always-on-top lyric overlay for Windows.

Run with:  .venv\\Scripts\\python.exe main.py
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLockFile, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from config import FONT_PRESETS, config_dir, load_settings, save_settings
from lyrics_fetcher import LyricsWorker
from media_listener import MediaListener
from overlay_ui import OverlayWindow

log = logging.getLogger("floating_lyrics")


def setup_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(
            RotatingFileHandler(
                config_dir() / "app.log",
                maxBytes=512 * 1024,
                backupCount=2,
                encoding="utf-8",
            )
        )
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("FLOATING_LYRICS_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def make_tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(28, 28, 32, 235))
    painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
    font = QFont("Segoe UI Symbol")
    font.setPixelSize(40)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QColor(255, 255, 255))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "\u266a")
    painter.end()
    return QIcon(pixmap)


class FloatingLyricsApp:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._settings = load_settings()
        self._last_requested_key: str | None = None
        self._shutting_down = False

        self.overlay = OverlayWindow(self._settings)
        self.overlay.track_changed.connect(self._on_track_changed)

        self.listener = MediaListener()
        self.listener.snapshot.connect(self.overlay.on_snapshot)
        self.listener.failed.connect(self._on_media_failed)

        self.worker = LyricsWorker()
        self.worker.resolved.connect(self.overlay.set_lyrics)

        self.tray = QSystemTrayIcon(make_tray_icon(), app)
        self.tray.setToolTip("Floating Lyrics")
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.listener.start()
        self.worker.start()

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self._lock_action = QAction("Lock position (click-through)", menu)
        self._lock_action.setCheckable(True)
        self._lock_action.setChecked(self._settings.locked)
        self._lock_action.toggled.connect(self._on_lock_toggled)
        menu.addAction(self._lock_action)
        menu.addSeparator()

        size_menu = menu.addMenu("Font size")
        group = QActionGroup(size_menu)
        group.setExclusive(True)
        for preset in FONT_PRESETS:
            action = QAction(preset.capitalize(), size_menu)
            action.setCheckable(True)
            action.setChecked(preset == self._settings.font_size)
            action.triggered.connect(lambda _checked, p=preset: self._on_font_selected(p))
            group.addAction(action)
            size_menu.addAction(action)

        reset = QAction("Reset position", menu)
        reset.triggered.connect(self.overlay.reset_position)
        menu.addAction(reset)
        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        return menu

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _on_track_changed(
        self,
        key: str,
        title: str,
        artist: str,
        album: str,
        duration: float,
    ) -> None:
        if key == self._last_requested_key:
            return
        self._last_requested_key = key
        log.info("Lyrics lookup: %s - %s", artist or "?", title)
        self.worker.request(
            key,
            title,
            artist,
            album,
            duration if duration > 5.0 else None,
        )

    def _on_media_failed(self, message: str) -> None:
        log.error("%s", message)
        self.tray.showMessage(
            "Floating Lyrics",
            message,
            QSystemTrayIcon.MessageIcon.Warning,
            6000,
        )

    def _on_lock_toggled(self, locked: bool) -> None:
        self.overlay.set_locked(locked)
        if not locked:
            self.tray.showMessage(
                "Floating Lyrics",
                "Drag the lyrics to move them, then lock again.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )

    def _on_font_selected(self, preset: str) -> None:
        self.overlay.set_font_size(preset)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._lock_action.setChecked(not self._lock_action.isChecked())

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def quit(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        log.info("Shutting down")
        self.listener.stop()
        self.worker.stop()
        self.listener.wait(3000)
        self.worker.wait(3000)
        save_settings(self._settings)
        self.tray.hide()
        self.overlay.close()
        self._app.quit()


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Floating Lyrics")
    app.setQuitOnLastWindowClosed(False)

    lock = QLockFile(str(config_dir() / "app.lock"))
    lock.setStaleLockTime(30_000)
    if not lock.tryLock(100):
        QMessageBox.information(
            None,
            "Floating Lyrics",
            "Floating Lyrics is already running.\n"
            "Use the tray icon to control it.",
        )
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("No system tray available; controls will be unreachable")

    controller = FloatingLyricsApp(app)
    app.aboutToQuit.connect(controller.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
