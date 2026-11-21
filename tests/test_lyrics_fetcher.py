from __future__ import annotations

from typing import Any

import pytest

from lyrics_fetcher import LyricsClient


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        payload: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class FakeSession:
    def __init__(self, routes: dict[str, FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, params: dict[str, str] | None = None, timeout: Any = None) -> FakeResponse:
        self.calls.append((url, dict(params or {})))
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        return FakeResponse(404)

    def close(self) -> None:
        return None


def make_client(routes: dict[str, FakeResponse]) -> tuple[LyricsClient, FakeSession]:
    client = LyricsClient()
    fake = FakeSession(routes)
    client._session = fake  # type: ignore[assignment]
    return client, fake


def test_exact_lookup_returns_synced_lines() -> None:
    client, fake = make_client(
        {
            "/get": FakeResponse(
                200,
                {
                    "trackName": "Blinding Lights",
                    "artistName": "The Weeknd",
                    "duration": 200.0,
                    "instrumental": False,
                    "plainLyrics": "Hello\nWorld",
                    "syncedLyrics": "[00:01.00]Hello\n[00:05.00]World",
                },
            )
        }
    )
    lyrics = client.fetch("Blinding Lights", "The Weeknd", "After Hours", 200.4)

    assert lyrics is not None
    assert lyrics.synced
    assert [line.text for line in lyrics.lines] == ["Hello", "World"]
    assert lyrics.plain == "Hello\nWorld"
    assert lyrics.instrumental is False

    _, params = fake.calls[0]
    assert params["track_name"] == "Blinding Lights"
    assert params["artist_name"] == "The Weeknd"
    assert params["duration"] == "200"
    assert params["album_name"] == "After Hours"


def test_search_fallback_prefers_matching_artist() -> None:
    client, fake = make_client(
        {
            "/get": FakeResponse(404),
            "/search": FakeResponse(
                200,
                [
                    {
                        "trackName": "Blinding Lights",
                        "artistName": "Blinding Lights Tribute Band",
                        "duration": 200.0,
                        "syncedLyrics": "[00:01.00]Wrong artist",
                    },
                    {
                        "trackName": "Blinding Lights",
                        "artistName": "The Weeknd",
                        "duration": 200.2,
                        "syncedLyrics": "[00:01.00]Right artist",
                    },
                ],
            ),
        }
    )
    lyrics = client.fetch("Blinding Lights", "The Weeknd", "", 200.0)

    assert lyrics is not None
    assert lyrics.match_artist == "The Weeknd"
    assert lyrics.lines[0].text == "Right artist"
    assert fake.calls[1][0].endswith("/search")


def test_search_rejects_unrelated_song() -> None:
    client, _ = make_client(
        {
            "/get": FakeResponse(404),
            "/search": FakeResponse(
                200,
                [
                    {
                        "trackName": "Completely Different Song",
                        "artistName": "Nobody",
                        "duration": 180.0,
                        "syncedLyrics": "[00:01.00]Nope",
                    }
                ],
            ),
        }
    )
    assert client.fetch("Blinding Lights", "The Weeknd", "", 200.0) is None


def test_instrumental_track_is_reported() -> None:
    client, _ = make_client(
        {
            "/get": FakeResponse(
                200,
                {
                    "trackName": "Intro",
                    "artistName": "Artist",
                    "instrumental": True,
                    "plainLyrics": None,
                    "syncedLyrics": None,
                },
            )
        }
    )
    lyrics = client.fetch("Intro", "Artist", "", 60.0)
    assert lyrics is not None
    assert lyrics.instrumental
    assert not lyrics.synced


def test_http_error_returns_none_without_raising() -> None:
    client, _ = make_client({"/get": FakeResponse(500)})
    assert client.fetch("Song", "Artist", "", None) is None


def test_empty_title_skips_network() -> None:
    client, fake = make_client({})
    assert client.fetch("   ", "Artist", "", None) is None
    assert fake.calls == []
