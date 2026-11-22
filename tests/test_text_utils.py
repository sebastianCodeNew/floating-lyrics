from __future__ import annotations

import pytest

from text_utils import (
    MIN_MATCH_SCORE,
    Candidate,
    active_line_index,
    clean_artist,
    clean_title,
    parse_lrc,
    promoted_line_index,
    score_candidate,
    strip_artist_prefix,
    wrap_text,
)


def test_clean_title_removes_common_decorations() -> None:
    assert clean_title("Blinding Lights (Official Video)") == "Blinding Lights"
    assert clean_title("Song [4K]") == "Song"
    assert clean_title("Track (Official Lyric Video)") == "Track"
    assert clean_title("Track - Topic") == "Track"
    assert clean_title("Track - VEVO") == "Track"
    assert clean_title("Track feat. Someone") == "Track"
    assert clean_title("  Spaced   Out  ") == "Spaced Out"


def test_clean_artist_strips_channel_suffixes() -> None:
    assert clean_artist("NIKI - Topic") == "NIKI"
    assert clean_artist("Artist - VEVO") == "Artist"
    assert clean_artist("The Weeknd") == "The Weeknd"


def test_parse_lrc_basic_and_sorted() -> None:
    payload = """[ar:Artist]
[ti:Song]
[00:12.50]Second line
[00:01.00]First line
"""
    lines = parse_lrc(payload)
    assert [line.text for line in lines] == ["First line", "Second line"]
    assert lines[0].time == pytest.approx(1.0)
    assert lines[1].time == pytest.approx(12.5)


def test_parse_lrc_multiple_timestamps_and_offset() -> None:
    payload = """[offset:-500]
[00:10.00][00:20.00]Chorus
[00:30.000]End
"""
    lines = parse_lrc(payload)
    assert [line.time for line in lines] == pytest.approx([9.5, 19.5, 29.5])
    assert all(line.text in {"Chorus", "End"} for line in lines)


def test_parse_lrc_fraction_digits() -> None:
    lines = parse_lrc("[00:01.5]a\n[00:02.50]b\n[00:03.500]c")
    assert [line.time for line in lines] == pytest.approx([1.5, 2.5, 3.5])


def test_parse_lrc_ignores_metadata_and_blank_lines() -> None:
    assert parse_lrc("[ar:X]\n[00:01.00]\n") == []
    assert parse_lrc("") == []


def test_active_line_index_boundaries() -> None:
    lines = parse_lrc("[00:05.00]one\n[00:10.00]two\n[00:15.00]three")
    assert active_line_index(lines, 0.0) == -1
    assert active_line_index(lines, 4.99) == -1
    assert active_line_index(lines, 5.0) == 0
    assert active_line_index(lines, 12.0) == 1
    assert active_line_index(lines, 99.0) == 2
    assert active_line_index([], 1.0) == -1


def test_promoted_line_index_looks_ahead_only_when_playing() -> None:
    lines = parse_lrc("[00:00.00]one\n[00:10.00]two\n[00:20.00]three")
    assert promoted_line_index(lines, 9.95, playing=True, lead_seconds=0.14) == 1
    assert promoted_line_index(lines, 9.95, playing=False, lead_seconds=0.14) == 0
    assert promoted_line_index(lines, 9.80, playing=True, lead_seconds=0.14) == 0
    assert promoted_line_index(lines, 10.05, playing=True, lead_seconds=0.14) == 1


def test_promoted_line_index_clamps_lead_to_half_the_gap() -> None:
    lines = parse_lrc("[00:00.00]one\n[00:00.20]two\n[00:00.40]three")
    # gap 0.2 -> lead 0.1, so 0.11 is early and 0.09 is not.
    assert promoted_line_index(lines, 0.11, playing=True, lead_seconds=0.14) == 1
    assert promoted_line_index(lines, 0.09, playing=True, lead_seconds=0.14) == 0


def test_promoted_line_index_handles_start_and_end() -> None:
    lines = parse_lrc("[00:01.00]one\n[00:10.00]two")
    assert promoted_line_index(lines, 0.9, playing=True, lead_seconds=0.14) == 0
    assert promoted_line_index(lines, 0.8, playing=True, lead_seconds=0.14) == -1
    assert promoted_line_index(lines, 99.0, playing=True, lead_seconds=0.14) == 1
    assert promoted_line_index([], 5.0, playing=True, lead_seconds=0.14) == -1


def test_score_prefers_exact_match() -> None:
    exact = Candidate("Blinding Lights", "The Weeknd", 200.0)
    cover = Candidate("Blinding Lights", "Some Cover Band", 240.0)
    title, artist, duration = "Blinding Lights", "The Weeknd", 200.0
    assert score_candidate(exact, title, artist, duration) > score_candidate(
        cover, title, artist, duration
    )
    assert score_candidate(exact, title, artist, duration) >= 10.0


def test_score_rejects_unrelated_title() -> None:
    unrelated = Candidate("Completely Different Song", "Other Artist", 200.0)
    assert score_candidate(unrelated, "Blinding Lights", "The Weeknd", 200.0) == 0.0


def test_score_tolerates_youtube_title_prefix() -> None:
    candidate = Candidate("Take A Chance With Me", "NIKI", 304.0)
    score = score_candidate(
        candidate,
        "NIKI - Take A Chance With Me (Official Video)",
        "NIKI",
        303.9,
    )
    assert score >= 9.0


def test_score_rejects_weak_partial_title() -> None:
    weak = Candidate("Blinding Night Lights", "Nobody", None)
    score = score_candidate(weak, "Blinding Lights", "The Weeknd", None)
    assert 0.0 < score < MIN_MATCH_SCORE


def test_wrap_text_never_truncates() -> None:
    measure = lambda value: len(value) * 10  # noqa: E731 - tiny local helper
    assert wrap_text("short line", 200, measure) == ["short line"]
    assert wrap_text("one two three", 100, measure) == ["one two", "three"]
    assert wrap_text("abcdefghijkl", 100, measure) == ["abcdefghij", "kl"]
    assert wrap_text("", 100, measure) == []


def test_wrap_text_splits_unspaced_text() -> None:
    measure = lambda value: len(value) * 10  # noqa: E731
    assert wrap_text("\u4f60\u597d\u4e16\u754c\u4f60\u597d", 40, measure) == [
        "\u4f60\u597d\u4e16\u754c",
        "\u4f60\u597d",
    ]


def test_strip_artist_prefix_removes_duplicated_artist() -> None:
    # Chrome/YouTube report "Artist - Title" while artist is already "Artist".
    assert (
        strip_artist_prefix("The Weeknd - Closing Night", "The Weeknd")
        == "Closing Night"
    )
    assert strip_artist_prefix("NIKI - lowkey", "NIKI") == "lowkey"
    assert strip_artist_prefix("Song Name", "Artist") == "Song Name"


def test_strip_artist_prefix_handles_dash_variants() -> None:
    assert strip_artist_prefix("Artist \u2013 Title", "Artist") == "Title"
    assert strip_artist_prefix("Artist | Title", "Artist") == "Title"
    assert strip_artist_prefix("Artist Title", "Artist") == "Title"


def test_score_penalises_implausible_duration() -> None:
    real = Candidate("Closing Night", "The Weeknd", 227.0)
    junk = Candidate("Closing Night [Bonus]", "The Weeknd", 1.0)
    assert score_candidate(real, "Closing Night", "The Weeknd", 227.0) > score_candidate(
        junk, "Closing Night", "The Weeknd", 227.0
    )


def test_score_penalises_wildly_wrong_duration() -> None:
    # Title (6) + artist (3) - wrong-duration penalty (3) = 6.0, below the
    # 10.0 an exact duration match would earn.
    candidate = Candidate("Closing Night", "The Weeknd", 600.0)
    close = Candidate("Closing Night", "The Weeknd", 227.0)
    assert score_candidate(candidate, "Closing Night", "The Weeknd", 227.0) == 6.0
    assert score_candidate(close, "Closing Night", "The Weeknd", 227.0) == 12.0
