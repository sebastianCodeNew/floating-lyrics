"""Windows SMTC/GSMTC media session polling.

A single background thread polls the Windows media session manager and emits
snapshots to the UI thread. Polling (rather than COM event handlers) keeps the
lifecycle simple and is fast enough for a 250 ms overlay refresh.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from PySide6.QtCore import QThread, Signal

try:  # pragma: no cover - import guard only
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSession as Session,
    )
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as SessionManager,
    )
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
    )
except ImportError:  # pragma: no cover
    Session = object  # type: ignore[assignment,misc]
    SessionManager = None  # type: ignore[assignment]
    PlaybackStatus = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.25


@dataclass(frozen=True, slots=True)
class TrackSnapshot:
    """Immutable view of the active media session at one point in time."""

    title: str
    artist: str
    album: str
    app_id: str
    playing: bool
    position: float
    duration: float
    sampled_at: float

    @property
    def key(self) -> str:
        """Identity used for lyric lookups and change detection."""
        return f"{self.title}\x1f{self.artist}"

    def position_at(self, now: float | None = None) -> float:
        """Extrapolate the playback position to ``now`` (monotonic seconds)."""
        current = time.monotonic() if now is None else now
        position = self.position
        if self.playing:
            position += max(0.0, current - self.sampled_at)
        if self.duration > 0:
            position = min(position, self.duration)
        return max(0.0, position)


def _playback_status(session: Session) -> PlaybackStatus | None:
    try:
        return session.get_playback_info().playback_status
    except Exception:  # noqa: BLE001 - WinRT can fail for closing sessions
        return None


def _timeline_position(session: Session) -> tuple[float, float]:
    """Return (position, duration) seconds, correcting for snapshot staleness."""
    try:
        timeline = session.get_timeline_properties()
        position = timeline.position.total_seconds()
        duration = timeline.end_time.total_seconds()
    except Exception:  # noqa: BLE001
        return 0.0, 0.0

    if _playback_status(session) == PlaybackStatus.PLAYING:
        try:
            updated = timeline.last_updated_time
            if updated is not None:
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                stale = (datetime.now(timezone.utc) - updated).total_seconds()
                # Chrome/Edge only refresh the timeline on state changes, so a
                # large gap is normal. Extrapolate the full elapsed time.
                position += max(0.0, stale)
        except Exception:  # noqa: BLE001
            pass

    duration = max(0.0, duration)
    if duration:
        position = min(position, duration)
    return max(0.0, position), duration


def _rank(sessions: list[Session], current: Session | None) -> list[Session]:
    """Order sessions by: playing first, then current, then newest timeline."""

    def score(session: Session) -> tuple[bool, bool, float]:
        playing = _playback_status(session) == PlaybackStatus.PLAYING
        is_current = current is not None and session.source_app_user_model_id == (
            current.source_app_user_model_id
        )
        try:
            updated = session.get_timeline_properties().last_updated_time
            stamp = updated.timestamp() if updated else 0.0
        except Exception:  # noqa: BLE001
            stamp = 0.0
        return playing, is_current, stamp

    return sorted(sessions, key=score, reverse=True)


async def _read_session(session: Session) -> TrackSnapshot | None:
    try:
        media = await session.try_get_media_properties_async()
    except Exception:  # noqa: BLE001
        return None
    if media is None:
        return None

    title = (media.title or "").strip()
    if not title:
        return None

    status = _playback_status(session)
    position, duration = _timeline_position(session)
    return TrackSnapshot(
        title=title,
        artist=(media.artist or "").strip(),
        album=(media.album_title or "").strip(),
        app_id=(session.source_app_user_model_id or "").strip(),
        playing=status == PlaybackStatus.PLAYING,
        position=position,
        duration=duration,
        sampled_at=time.monotonic(),
    )


class MediaListener(QThread):
    """Poll the active Windows media session and emit :class:`TrackSnapshot`."""

    snapshot = Signal(object)  # TrackSnapshot | None
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._stop_event = threading.Event()
        self._last_key: str | None = None

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        if SessionManager is None:
            self.failed.emit(
                "winsdk is not installed. Run: pip install -r requirements.txt"
            )
            return
        try:
            asyncio.run(self._poll_forever())
        except Exception as exc:  # noqa: BLE001 - thread must not crash silently
            log.exception("Media listener stopped unexpectedly")
            self.failed.emit(f"Media listener error: {exc}")

    async def _poll_forever(self) -> None:
        manager = None
        backoff = 1.0
        while not self._stop_event.is_set():
            if manager is None:
                try:
                    manager = await SessionManager.request_async()
                    backoff = 1.0
                except Exception as exc:  # noqa: BLE001
                    log.warning("Cannot access media sessions: %s", exc)
                    self.snapshot.emit(None)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 10.0)
                    continue

            try:
                sessions = list(manager.get_sessions())
                current = manager.get_current_session()
            except Exception as exc:  # noqa: BLE001
                log.warning("Media session manager lost: %s", exc)
                manager = None
                continue

            chosen = await self._select(sessions, current)
            if chosen is None:
                self._last_key = None
                self.snapshot.emit(None)
            else:
                self.snapshot.emit(chosen)
                if chosen.key != self._last_key:
                    self._last_key = chosen.key
                    log.info(
                        "Now playing: %s - %s [%s]",
                        chosen.artist or "?",
                        chosen.title,
                        chosen.app_id or "?",
                    )

            await asyncio.sleep(POLL_INTERVAL)

    async def _select(
        self,
        sessions: list[Session],
        current: Session | None,
    ) -> TrackSnapshot | None:
        for session in _rank(sessions, current):
            if self._stop_event.is_set():
                return None
            result = await _read_session(session)
            if result is not None:
                return result
        return None
