# Floating Lyrics

A minimal, always-on-top lyrics overlay for Windows. It follows whatever media
app is currently playing — Spotify, a YouTube tab in Chrome/Edge, VLC, or any
other app that publishes a Windows media session — and shows two lines of
lyrics near the top-left of the screen.

Click-through by default, so it never blocks mouse input while you game or work.

```
        I've been on my own for long enough
           Maybe you can show me how to love, maybe
```

## Features

- **Any SMTC app**, not just Spotify. Picks the session that is actually playing.
- **Two-line overlay**: active line bold and larger, next line dimmed.
- **Zero background**: transparent window with outlined text for readability.
- **Click-through** by default (`WindowTransparentForInput`).
- **Tray controls**: lock/unlock position, font size (small/medium/large), reset
  position, quit.
- **LRCLIB** lyrics, no account or API key. Conservative matching: if no result
  is a confident match, it shows the track title instead of wrong lyrics.
- **Multi-monitor safe**: validates the saved position against current screens.
- **Non-blocking**: media polling and HTTP requests run off the UI thread.

## Requirements

- Windows 10 or 11
- Python **3.12** (the pinned `winsdk` wheel is CPython 3.12 only)
- Network access for lyric lookups

## Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

Or without activating:

```powershell
.venv\Scripts\python.exe main.py
```

A music-note icon appears in the system tray. Start something in Spotify or
YouTube and the overlay appears near the top-left of your primary screen
(one sixth in from the corner).

## Tray menu

| Item | Behaviour |
|---|---|
| **Lock position (click-through)** | Checked = the overlay ignores the mouse and clicks pass through to the app below. Uncheck to drag it. |
| **Reset position** | Moves the overlay back to bottom-center. Useful if it ends up off-screen. |
| **Quit** | Stops the media listener and lyric worker cleanly. |

Double-clicking the tray icon also toggles the lock.

## How it works

1. `media_listener.py` polls the Windows SMTC manager every 250 ms, ranks
   sessions (playing first, then the current session), and corrects the
   timeline snapshot for staleness using `last_updated_time`.
2. `lyrics_fetcher.py` asks LRCLIB for lyrics once per track, on a single
   worker thread. Only the newest request is kept, so skipping songs quickly
   never floods the API. Results are cached in memory for the session.
3. `text_utils.py` parses LRC timestamps and scores search results by title,
   artist and duration. Below the confidence threshold the app shows nothing
   rather than the wrong song's lyrics.
4. `overlay_ui.py` renders the active and next line with a custom
   outline/shadow, handles dragging and position persistence.

## Limitations

- **Exclusive fullscreen games** cannot be overlaid by normal desktop windows.
  Use borderless/windowed mode; the overlay will then appear above the game.
- The app can only see players that expose a Windows media session. Some
  portable players do not.
- DRM-protected or region-locked content may not report metadata.
- Lyrics come from LRCLIB at runtime. Coverage varies by track; instrumental
  tracks show an "Instrumental" label.
- The first lookup for a track needs a network round trip, so there can be a
  short delay before lyrics appear.

## Configuration

Settings are stored at `%APPDATA%\FloatingLyrics\settings.json` and logs at
`%APPDATA%\FloatingLyrics\app.log`. Delete the settings file to reset
everything.

For troubleshooting, set `FLOATING_LYRICS_DEBUG=1` before launching to log the
live playback position and the displayed lines.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

The suite covers LRC parsing, title cleanup, match scoring, timeline
extrapolation, session arbitration, and LRCLIB response handling.

## Privacy

The app sends only the current track's title, artist, album and duration to
`lrclib.net` to look up lyrics. There are no accounts, telemetry, or bundled
lyrics. Responses are cached in memory only and are not redistributed.

## License

MIT — see [LICENSE](LICENSE).
