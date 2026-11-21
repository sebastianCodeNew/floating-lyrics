from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
)

from media_listener import TrackSnapshot, _rank, _timeline_position


class FakeSession:
    def __init__(
        self,
        app_id: str,
        status: PlaybackStatus,
        *,
        position: float = 10.0,
        duration: float = 200.0,
        updated: datetime | None = None,
    ) -> None:
        self.source_app_user_model_id = app_id
        self._status = status
        self._position = position
        self._duration = duration
        self._updated = updated

    def get_playback_info(self) -> SimpleNamespace:
        return SimpleNamespace(playback_status=self._status)

    def get_timeline_properties(self) -> SimpleNamespace:
        return SimpleNamespace(
            position=timedelta(seconds=self._position),
            end_time=timedelta(seconds=self._duration),
            last_updated_time=self._updated,
        )


def test_position_extrapolates_while_playing() -> None:
    snapshot = TrackSnapshot(
        title="Song",
        artist="Artist",
        album="",
        app_id="Spotify",
        playing=True,
        position=10.0,
        duration=100.0,
        sampled_at=1000.0,
    )
    assert snapshot.position_at(1002.5) == pytest.approx(12.5)
    # Chrome reports a snapshot; elapsed time must keep advancing until the
    # reported position reaches the track duration.
    assert snapshot.position_at(2000.0) == pytest.approx(100.0)


def test_position_frozen_while_paused() -> None:
    snapshot = TrackSnapshot(
        title="Song",
        artist="Artist",
        album="",
        app_id="Spotify",
        playing=False,
        position=42.0,
        duration=100.0,
        sampled_at=1000.0,
    )
    assert snapshot.position_at(1030.0) == pytest.approx(42.0)


def test_timeline_corrects_stale_snapshot() -> None:
    updated = datetime.now(timezone.utc) - timedelta(seconds=1.5)
    session = FakeSession("Chrome", PlaybackStatus.PLAYING, position=10.0, updated=updated)
    position, duration = _timeline_position(session)
    assert position == pytest.approx(11.5, abs=0.4)
    assert duration == pytest.approx(200.0)


def test_timeline_extrapolates_minutes_of_stale_snapshot() -> None:
    # Chrome only refreshes the timeline on state changes; a 90 s old snapshot
    # must still advance instead of freezing the lyrics.
    updated = datetime.now(timezone.utc) - timedelta(seconds=90)
    session = FakeSession(
        "Chrome",
        PlaybackStatus.PLAYING,
        position=78.5,
        duration=182.0,
        updated=updated,
    )
    position, _ = _timeline_position(session)
    assert position == pytest.approx(168.5, abs=0.5)


def test_timeline_does_not_extrapolate_when_paused() -> None:
    updated = datetime.now(timezone.utc) - timedelta(seconds=30)
    session = FakeSession("Spotify", PlaybackStatus.PAUSED, position=10.0, updated=updated)
    position, _ = _timeline_position(session)
    assert position == pytest.approx(10.0)


def test_rank_prefers_playing_session_over_current_paused() -> None:
    spotify = FakeSession(
        "Spotify.exe", PlaybackStatus.PAUSED, updated=datetime.now(timezone.utc)
    )
    chrome = FakeSession(
        "Chrome", PlaybackStatus.PLAYING, updated=datetime.now(timezone.utc)
    )
    ranked = _rank([spotify, chrome], current=spotify)
    assert ranked[0] is chrome


def test_rank_prefers_current_session_between_playing_sessions() -> None:
    now = datetime.now(timezone.utc)
    spotify = FakeSession("Spotify.exe", PlaybackStatus.PLAYING, updated=now)
    chrome = FakeSession(
        "Chrome", PlaybackStatus.PLAYING, updated=now - timedelta(seconds=5)
    )
    ranked = _rank([chrome, spotify], current=spotify)
    assert ranked[0] is spotify


def test_rank_falls_back_to_current_when_nothing_plays() -> None:
    now = datetime.now(timezone.utc)
    spotify = FakeSession("Spotify.exe", PlaybackStatus.PAUSED, updated=now)
    chrome = FakeSession(
        "Chrome", PlaybackStatus.STOPPED, updated=now - timedelta(minutes=5)
    )
    ranked = _rank([chrome, spotify], current=spotify)
    assert ranked[0] is spotify
