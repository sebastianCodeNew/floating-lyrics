"""Text normalisation, LRC parsing and lyric matching heuristics.

Everything here is pure Python (no Qt, no network) so it can be unit tested
quickly and reused by both the media and lyrics layers.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Title / artist cleanup
# ---------------------------------------------------------------------------

# Bracketed decorations commonly appended by YouTube, VEVO and Spotify.
_BRACKET_RE = re.compile(
    r"\s*[\(\[]\s*(?:"
    r"official(?:\s+(?:music|lyric|audio|video|visualizer|hd|4k))*"
    r"|lyrics?|lyric\s+video|audio|video|music\s+video|visualizer"
    r"|hd|hq|4k|8k|1080p|720p"
    r"|remaster(?:ed)?(?:\s+\d{4})?"
    r"|live(?:\s+at\s+[^\)\]]*)?"
    r"|explicit|clean|radio\s+edit"
    r"|color\s+coded|colou?r\s+coded"
    r")\s*[\)\]]",
    re.IGNORECASE,
)

# "- Topic" is appended to auto-generated YouTube artist channels.
_TOPIC_RE = re.compile(r"\s*-\s*topic\s*$", re.IGNORECASE)
_VEVO_RE = re.compile(r"\s*-\s*vevo\s*$", re.IGNORECASE)
_FEAT_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+[^,\-\u2013]+$", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def clean_title(title: str) -> str:
    """Remove common browser/YouTube decorations from a track title."""
    value = unicodedata.normalize("NFKC", title or "")
    value = _BRACKET_RE.sub("", value)
    value = _TOPIC_RE.sub("", value)
    value = _VEVO_RE.sub("", value)
    value = _FEAT_RE.sub("", value)
    # Trailing separators left behind by removed decorations.
    value = re.sub(r"\s*[-\u2013|/]\s*$", "", value)
    return _WS_RE.sub(" ", value).strip()


def clean_artist(artist: str) -> str:
    """Normalise an artist field (channel names often contain decorations)."""
    value = unicodedata.normalize("NFKC", artist or "")
    value = _TOPIC_RE.sub("", value)
    value = _VEVO_RE.sub("", value)
    return _WS_RE.sub(" ", value).strip()


def _fold(value: str) -> str:
    """Casefold and strip punctuation for tolerant comparisons."""
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", value.casefold())).strip()


def _tokens(value: str) -> set[str]:
    return {token for token in _fold(value).split() if len(token) > 1}


# ---------------------------------------------------------------------------
# LRC parsing
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET_RE = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class LyricLine:
    """A single timed lyric line."""

    time: float
    text: str


def parse_lrc(payload: str) -> list[LyricLine]:
    """Parse an LRC document into sorted, non-empty timed lines.

    Supports multiple timestamps per line, ``[offset:ms]`` and ignores
    metadata tags. Fractional seconds of 1-3 digits are interpreted
    correctly (``.5`` = 500 ms, ``.50`` = 500 ms, ``.500`` = 500 ms).
    """
    if not payload:
        return []

    offset_match = _OFFSET_RE.search(payload)
    offset = int(offset_match.group(1)) / 1000.0 if offset_match else 0.0

    lines: list[LyricLine] = []
    for raw_line in payload.splitlines():
        stamps = list(_TIMESTAMP_RE.finditer(raw_line))
        if not stamps:
            continue
        text = _TIMESTAMP_RE.sub("", raw_line).strip()
        if not text:
            continue
        for stamp in stamps:
            minutes = int(stamp.group(1))
            seconds = int(stamp.group(2))
            fraction = stamp.group(3) or "0"
            millis = int(fraction.ljust(3, "0")[:3])
            time = minutes * 60 + seconds + millis / 1000.0 + offset
            if time < 0:
                continue
            lines.append(LyricLine(time=time, text=text))

    # A line may legitimately repeat; keep stable order for equal timestamps.
    lines.sort(key=lambda line: line.time)
    return lines


def active_line_index(lines: list[LyricLine], position: float) -> int:
    """Return the index of the line playing at ``position`` or -1 before start."""
    if not lines:
        return -1
    index = bisect.bisect_right([line.time for line in lines], position) - 1
    return index


def wrap_text(
    text: str,
    max_width: int,
    measure: Callable[[str], int],
) -> list[str]:
    """Wrap ``text`` so every line fits ``max_width``, never truncating.

    ``measure`` returns the rendered width of a string (normally
    ``QFontMetrics.horizontalAdvance``). Words wider than the limit are split
    by character, which also handles CJK text that has no spaces.
    """
    normalized = _WS_RE.sub(" ", text).strip()
    if not normalized:
        return []
    if max_width <= 0:
        return [normalized]

    lines: list[str] = []
    current = ""
    for word in normalized.split(" "):
        candidate = f"{current} {word}" if current else word
        if measure(candidate) <= max_width:
            current = candidate
            continue

        if current:
            lines.append(current)
            current = ""

        if measure(word) <= max_width:
            current = word
            continue

        chunk = ""
        for char in word:
            if chunk and measure(chunk + char) > max_width:
                lines.append(chunk)
                chunk = char
            else:
                chunk += char
        current = chunk

    if current:
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# LRCLIB candidate ranking
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    """Minimal view of an LRCLIB search result used for ranking."""

    track_name: str
    artist_name: str
    duration: float | None
    instrumental: bool = False


def score_candidate(
    candidate: Candidate,
    title: str,
    artist: str,
    duration: float | None,
) -> float:
    """Score how well an LRCLIB result matches the playing track.

    The score is intentionally conservative: a result must share title tokens
    to be considered at all, and artist/duration only refine the choice.
    """
    wanted_title = _fold(clean_title(title))
    got_title = _fold(clean_title(candidate.track_name))
    if not wanted_title or not got_title:
        return 0.0

    score = 0.0
    if wanted_title == got_title:
        score += 6.0
    elif wanted_title in got_title or got_title in wanted_title:
        score += 4.0
    else:
        wanted_tokens = _tokens(wanted_title)
        got_tokens = _tokens(got_title)
        if not wanted_tokens or not got_tokens:
            return 0.0
        overlap = len(wanted_tokens & got_tokens) / len(wanted_tokens)
        if overlap < 0.6:
            return 0.0
        score += 3.0 * overlap

    wanted_artist = _fold(clean_artist(artist))
    got_artist = _fold(clean_artist(candidate.artist_name))
    if wanted_artist and got_artist:
        if wanted_artist == got_artist:
            score += 3.0
        elif wanted_artist in got_artist or got_artist in wanted_artist:
            score += 2.0
        else:
            wanted_tokens = _tokens(wanted_artist)
            got_tokens = _tokens(got_artist)
            if wanted_tokens and got_tokens:
                overlap = len(wanted_tokens & got_tokens) / len(wanted_tokens)
                score += 2.0 * overlap

    if duration and candidate.duration:
        delta = abs(duration - candidate.duration)
        if delta <= 2.0:
            score += 3.0
        elif delta <= 5.0:
            score += 1.5
        elif delta > 30.0:
            score -= 1.0

    return score


MIN_MATCH_SCORE = 4.0
"""Below this score we show nothing rather than the wrong lyrics."""
