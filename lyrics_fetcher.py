"""LRCLIB lyrics retrieval, caching and background worker.

Network access happens on a single dedicated thread. Only the most recent
request is kept, so rapid track skipping can never queue up a backlog of
stale lookups.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import requests
from PySide6.QtCore import QThread, Signal

from text_utils import (
    MIN_MATCH_SCORE,
    Candidate,
    LyricLine,
    clean_artist,
    clean_title,
    looks_like_channel,
    parse_lrc,
    score_candidate,
    split_artist_from_title,
    strip_artist_prefix,
)

log = logging.getLogger(__name__)

API_ROOT = "https://lrclib.net/api"
USER_AGENT = "FloatingLyrics/0.1.0 (Windows desktop overlay)"
TIMEOUT = (4.0, 8.0)
NEGATIVE_TTL = 300.0


@dataclass(frozen=True, slots=True)
class Lyrics:
    """Lyrics for one track, timed when available."""

    lines: tuple[LyricLine, ...]
    plain: str | None
    instrumental: bool
    match_title: str
    match_artist: str

    @property
    def synced(self) -> bool:
        return bool(self.lines)


class LyricsClient:
    """Thin, rate-limit-aware LRCLIB client."""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )

    def close(self) -> None:
        self._session.close()

    def fetch(
        self,
        title: str,
        artist: str,
        album: str,
        duration: float | None,
    ) -> Lyrics | None:
        """Look up lyrics; returns ``None`` when no trustworthy match exists."""
        clean = clean_title(title)
        artist_clean = clean_artist(artist)

        # Chrome reports the YouTube *channel* as the artist (e.g.
        # "ArianaGrandeVevo") while the real artist is the first part of the
        # title. Recover both before building the query.
        stripped_title = clean_title(title)
        if not stripped_title:
            return None

        if looks_like_channel(artist_clean) or not artist_clean:
            clean, derived_artist = split_artist_from_title(title)
            if derived_artist:
                artist_clean = derived_artist
        else:
            # Artist field is usable, but the title may still repeat it:
            # "The Weeknd - Closing Night" with artist "The Weeknd".
            clean = strip_artist_prefix(stripped_title, artist_clean)

        if not clean or not clean.strip():
            return None

        params: dict[str, str] = {"track_name": clean, "artist_name": artist_clean}
        if album:
            params["album_name"] = album
        # LRCLIB rejects duration=0 with HTTP 400, and browser/stream sessions
        # frequently report 0. Omit it rather than sending an invalid value.
        if duration and duration > 0:
            params["duration"] = str(int(round(duration)))

        payload = self._request("/get", params)
        if payload:
            return self._parse(payload)

        query = " ".join(part for part in (clean, artist_clean) if part)
        results = self._request("/search", {"q": query})
        if not isinstance(results, list):
            return None

        best: dict[str, object] | None = None
        best_score = 0.0
        for item in results:
            if not isinstance(item, dict):
                continue
            if not (item.get("syncedLyrics") or item.get("plainLyrics")):
                continue
            candidate = Candidate(
                track_name=str(item.get("trackName") or ""),
                artist_name=str(item.get("artistName") or ""),
                duration=_as_float(item.get("duration")),
                instrumental=bool(item.get("instrumental")),
            )
            score = score_candidate(candidate, clean, artist_clean, duration)
            # Prefer a synced record over a plain-only one at equal match
            # quality, since synced lyrics are what the overlay actually needs.
            if item.get("syncedLyrics"):
                score += 1.5
            if score > best_score:
                best_score = score
                best = item

        if best is None or best_score < MIN_MATCH_SCORE:
            log.info("No confident lyrics match for %r - %r (%.1f)", artist, title, best_score)
            return None
        return self._parse(best)

    def _request(self, path: str, params: dict[str, str]) -> object | None:
        """GET a JSON document, retrying once on 429/503 using Retry-After."""
        for attempt in range(2):
            try:
                response = self._session.get(
                    f"{API_ROOT}{path}", params=params, timeout=TIMEOUT
                )
            except requests.RequestException as exc:
                log.warning("LRCLIB request failed (%s): %s", path, exc)
                return None

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    log.warning("LRCLIB returned invalid JSON for %s", path)
                    return None
            if response.status_code == 404:
                return None
            if response.status_code in (429, 503) and attempt == 0:
                delay = _retry_after(response, default=2.0)
                log.info("LRCLIB throttled us; retrying in %.1fs", delay)
                threading.Event().wait(delay)
                continue
            log.warning(
                "LRCLIB %s returned HTTP %s", path, response.status_code
            )
            return None
        return None

    @staticmethod
    def _parse(item: dict[str, object]) -> Lyrics:
        instrumental = bool(item.get("instrumental"))
        synced = item.get("syncedLyrics")
        plain = item.get("plainLyrics")
        lines = parse_lrc(synced) if isinstance(synced, str) else []
        return Lyrics(
            lines=tuple(lines),
            plain=plain if isinstance(plain, str) and plain.strip() else None,
            instrumental=instrumental,
            match_title=str(item.get("trackName") or ""),
            match_artist=str(item.get("artistName") or ""),
        )


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _retry_after(response: requests.Response, default: float) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return max(0.5, min(float(raw), 5.0))
    except ValueError:
        return default


@dataclass(frozen=True, slots=True)
class _Request:
    key: str
    title: str
    artist: str
    album: str
    duration: float | None


class LyricsWorker(QThread):
    """Resolve lyric lookups off the UI thread, keeping only the latest one."""

    resolved = Signal(str, object)  # key, Lyrics | None

    def __init__(self) -> None:
        super().__init__()
        self._client = LyricsClient()
        self._wake = threading.Event()
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._pending: _Request | None = None
        self._cache: dict[str, tuple[Lyrics | None, float]] = {}

    def request(
        self,
        key: str,
        title: str,
        artist: str,
        album: str,
        duration: float | None,
    ) -> None:
        with self._lock:
            self._pending = _Request(key, title, artist, album, duration)
        self._wake.set()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake.set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            while not self._stop_event.is_set():
                self._wake.wait(0.5)
                self._wake.clear()
                with self._lock:
                    request = self._pending
                    self._pending = None
                if request is None or self._stop_event.is_set():
                    continue

                entry = self._cache.get(request.key)
                if entry is not None:
                    cached, stored_at = entry
                    # Misses expire so a temporary outage is retried later.
                    if cached is None and time.monotonic() - stored_at > NEGATIVE_TTL:
                        entry = None
                else:
                    cached = None

                if entry is None:
                    try:
                        cached = self._client.fetch(
                            request.title,
                            request.artist,
                            request.album,
                            request.duration,
                        )
                    except Exception:  # noqa: BLE001 - never kill the worker
                        log.exception("Lyrics lookup crashed")
                        cached = None
                    if len(self._cache) > 200:
                        self._cache.clear()
                    self._cache[request.key] = (cached, time.monotonic())

                if self._stop_event.is_set():
                    break
                if cached is None:
                    log.info("No lyrics found for %s", request.key.replace("\x1f", " - "))
                else:
                    log.info(
                        "Lyrics ready: %d synced lines for %s",
                        len(cached.lines),
                        request.key.replace("\x1f", " - "),
                    )
                self.resolved.emit(request.key, cached)
        finally:
            self._client.close()
