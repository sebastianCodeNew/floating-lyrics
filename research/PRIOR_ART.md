# Prior Art Research — Windows Floating Lyrics Overlay

**Research date:** 2026-11-21  
**Scope:** existing Windows desktop lyrics overlays, especially projects using Windows SMTC/GSMTC, LRCLIB, Python, Qt, or a pure-text overlay.  
**Method:** GitHub repository search/API, repository READMEs and source files, PyPI metadata, LRCLIB API documentation, and Lyricify documentation. Star counts are point-in-time snapshots and are not a quality score.

---

## TL;DR

**Yes, this has already been built — repeatedly.** The requested feature set is not novel. At least four projects already cover most or all of it:

| Project | Why it matters |
|---|---|
| [viraj-rgb/spotify-lyrics-overlay](https://github.com/viraj-rgb/spotify-lyrics-overlay) | **Closest Python stack:** PyQt6 + `winsdk` + LRCLIB. Transparent always-on-top overlay, click-through, tray, cache, installer. MIT. |
| [ScotsGamez/CLyrics](https://github.com/ScotsGamez/CLyrics) | **Closest UX:** PySide6, 100% transparent 3-line text, click-through, tray, font/theme controls. Uses a PowerShell WinRT reader + `syncedlyrics`, not direct `winsdk`. MIT. Very new and low adoption. |
| [The-MuffinDev/LyricsOnTheGo](https://github.com/The-MuffinDev/LyricsOnTheGo) | **Closest C# equivalent:** WPF + SMTC + LRCLIB, line sync, interpolation, disk cache, plain-lyrics fallback, tray, click-through. MIT. |
| [Epi-Lo/desktop-lyric](https://github.com/Epi-Lo/desktop-lyric) | Text overlay + SMTC + multiple lyric sources (NetEase/QQ/Kugou/LRCLIB), translation, song picker, timing editor, click-through. MIT. |
| [WXRIW/Lyricify-App](https://github.com/WXRIW/Lyricify-App) | **Market leader:** ~7.4k stars. Lyricify Fusion supports all SMTC apps, desktop lyrics, taskbar lyrics, dynamic island, translations, per-app lyric sources/delays. The app itself is closed-source freeware; the lyrics helper library is Apache-2.0. |
| [jayfunc/BetterLyrics](https://github.com/jayfunc/BetterLyrics) | **Largest open-source visualizer:** ~2.2k stars, C#/WinUI3/Win2D, GPL-3.0. Full music player + lyrics visualizer, desktop/taskbar/wallpaper/fullscreen modes, LRCLIB, plugins. |

The **exact minimal feature set** in the original request is a subset of these apps. In particular, viraj's MIT project already combines transparent Qt overlay + `winsdk` + LRCLIB + click-through + tray + caching. CLyrics already combines pure-text PySide6 overlay + click-through + tray + 3-line lyrics. There are also many low-star clones with nearly identical descriptions.

**Implication:** building it is still reasonable for learning, customization, or a deliberately small/auditable codebase, but it will not be a novel product by feature list alone. To be meaningfully differentiated, it needs a sharper angle. The strongest technical angle found is **true word-level sync using LRCLIB's new `lyricsfile` YAML data**, combined with **conservative matching** (hide rather than show wrong lyrics) and **correct multi-session arbitration**.

---

## Comparison Matrix

| Project | Stack | License | Stars* | Media source | Lyrics | UI | Click-through | Notable extras |
|---|---|---:|---:|---|---|---|---|---|
| Lyricify Fusion | Closed source (C#/.NET) | App: none; helper: Apache-2.0 | 7,410 (whole app repo) | All SMTC apps | Own sources/lyric management | Desktop, taskbar, dynamic island, fullscreen | Yes (desktop lyrics) | Translations, per-app sources, offsets, lyric management |
| BetterLyrics | C#, WinUI3, Win2D | GPL-3.0 | 2,240 | SMTC, local, streams | LRCLIB + others, plugins | Full player, desktop/taskbar/wallpaper/fullscreen | Likely (desktop mode) | Per-syllable karaoke, visualizers, plugins, translations |
| LyricsOnTheGo | C#, WPF (.NET 8) | MIT | 18 | SMTC | LRCLIB | Acrylic glass card, fullscreen karaoke | Toggle | Per-song offset, disk cache, local LRCLIB DB, progress |
| FrontLine Lyrics | Python backend + WPF/Avalonia | GPL-3.0 | 14 | SMTC + Shazam audio recognition | Multiple | Overlay + manual search | Yes | Audio fingerprinting, translations, festival mode |
| desktop-lyric | C#, WPF | MIT | 12 | SMTC | NetEase, QQ, Kugou, LRCLIB | Text overlay | When mouse is not over lyrics | Translation, song picker, timing editor, karaoke |
| Lucent | Electron/JS | MIT | 9 | SMTC, built-in player | NetEase etc. | Liquid-glass capsule | Optional | LAN room sync, built-in player, word sync |
| viraj Spotify Lyrics Overlay | Python, PyQt6 + WebEngine, `winsdk` | MIT | 5 | SMTC (Spotify preferred, falls back to current session) | LRCLIB | Transparent card in WebView | Yes | Album-color theme, derived word-by-word timing, cache, installer |
| AhPlace2 Lyrics Overlay | Rust + egui/DirectX 12 | AGPL-3.0 | 5 | SMTC + Shazam | LRCLIB, NetEase | Now-playing card + big floating lyrics | Yes | Word-by-word, sync nudge, arrange mode, 13 MB exe |
| Lyrow | C#, WinUI3 | MIT | 2 | SMTC | LRCLIB | Full lyrics window, compact mode, edge glow | Yes (edge glow) | WASAPI visualizer, 728 tests, playback controls |
| CLyrics | Python, PySide6 | MIT | 0 | SMTC via PowerShell WinRT daemon | `syncedlyrics` aggregator (LRCLIB/NetEase) | 100% transparent 3-line text | Yes (Win32 `WS_EX_TRANSPARENT`) | Themes, album art, settings GUI, 5-song cache, manual offset |
| LyricParser | C#/.NET | MIT | 6 | Windows 10 media control | Multiple | Overlay | — | Older, last pushed 2021 |
| xsaardo Spotify-Lyrics-Overlay | JS/Electron | None | 18 | Spotify | Lyrics provider | Overlay | — | Historical; last push 2022 |

\* Star counts from the GitHub API on 2026-11-21. The Lyricify number is for the whole multi-version app repository, not only Fusion.

**Long tail:** GitHub has many recent low-star Python/Qt overlays with nearly identical descriptions, e.g. `juzorei/lyrics-overlay` (PySide6, SMTC/MPRIS, LRCLIB/NetEase/Genius), `wenjun-cheng/spotify-desktop-lyrics`, `Anngiie/PhantomLyrics`, `Chip-ik/lyrics-overlay`, and others. Most have 0–2 stars and unclear maintenance. This is a crowded “weekend project” category.

---

## Deep Dives

### 1. viraj-rgb/spotify-lyrics-overlay — closest Python stack

- **License:** MIT
- **Stack:** PyQt6, PyQt6-WebEngine, `winsdk`, `requests`
- **Media:** Windows SMTC. README says Spotify desktop, but the code prefers Spotify and falls back to the current session, so it can read other players too.
- **Lyrics:** LRCLIB `/api/get` and `/api/search`, with a disk cache that also stores misses.
- **UI:** Transparent, always-on-top Qt shell whose visible surface is a WebView card. Frameless, click-through, drag/resize, tray, settings.
- **Timing:** Corrects SMTC timeline snapshot staleness using `last_updated_time`; derives word-by-word timings from line timestamps (not authored word timing).
- **Why it matters:** This is essentially the requested app plus a heavier WebView UI, album-color theming, an installer, and more settings. It proves the `PyQt6 + winsdk + LRCLIB` combination works on Windows.
- **Weaknesses for this use case:** Spotify-first bias; 155 MB installer due to Qt WebEngine; word timing is guessed; card UI rather than pure text; the README targets Spotify users.
- **Source:** https://github.com/viraj-rgb/spotify-lyrics-overlay

### 2. ScotsGamez/CLyrics — closest UX

- **License:** MIT
- **Stack:** PySide6 + `syncedlyrics`; media reader is a PowerShell script using WinRT, not `winsdk`.
- **Media:** SMTC via a 150 ms polling PowerShell daemon with JSON output.
- **Lyrics:** `syncedlyrics` aggregator (README emphasizes LRCLIB and NetEase), with a rolling 5-song cache.
- **UI:** 100% transparent, frameless, always-on-top 3-line text (previous/active/next), themes, album art, settings dialog, tray.
- **Interaction:** Native Win32 `WS_EX_TRANSPARENT` for click-through; drag/resize when unlocked; right-click menu.
- **Timing:** Monotonic interpolation, manual sync offsets, intro/instrumental detection.
- **Why it matters:** It already implements almost the exact visual concept and interaction model requested.
- **Weaknesses:** Very new (created 2026-10-17), 0 stars, no evidence of broad testing; PowerShell subprocess architecture is less elegant than direct `winsdk`; more features and settings than the requested minimal app.
- **Source:** https://github.com/ScotsGamez/CLyrics

### 3. The-MuffinDev/LyricsOnTheGo — closest C# feature set

- **License:** MIT
- **Stack:** WPF (.NET 8), Windows SMTC, LRCLIB, `System.Windows.Forms.NotifyIcon`
- **Media:** SMTC, 1 s poll with local interpolation at ~4 Hz.
- **Lyrics:** LRCLIB, disk cache keyed by track metadata, bounded retry state machine, optional local LRCLIB database (~117 GB dump).
- **UI:** Acrylic-glass card, line-by-line active lyric, auto-centering, karaoke fullscreen, progress bar, settings.
- **Interaction:** Click-through toggle, drag, resize, scroll lyrics, per-song offset, tray.
- **Matching:** Explicitly handles browser metadata by searching by title only, cleaning decorations, ranking candidates by title containment and duration.
- **Why it matters:** Its README describes the exact hard problems this project will face: messy browser metadata, position interpolation, click-through recovery, caching, and plain-lyrics fallback.
- **Source:** https://github.com/The-MuffinDev/LyricsOnTheGo

### 4. Epi-Lo/desktop-lyric — text overlay with mature matching

- **License:** MIT
- **Stack:** C#/WPF, Windows SMTC.
- **Lyrics:** Searches NetEase, QQ Music, Kugou, and LRCLIB in parallel, picks the result closest to track duration.
- **UI:** Floating text overlay with translation, karaoke highlight, fullscreen mode.
- **Interaction:** Clicks pass through when the mouse is not on the lyrics; lock button; song picker when matching is wrong; timing editor with per-line shifts and LRC import/export.
- **Why it matters:** Demonstrates that “wrong lyrics” is a real user problem and that a manual picker/lock is often necessary.
- **Source:** https://github.com/Epi-Lo/desktop-lyric

### 5. Lyricify Fusion — the established product

- **License:** Closed-source freeware; the lyrics helper library is Apache-2.0.
- **Media:** All apps that expose SMTC, including Spotify, Apple Music, QQ Music, NetEase UWP, Kugou, Foobar2000, Groove, etc.
- **UI:** Desktop lyrics, taskbar lyrics, dynamic lyrics island, Apple Music-style lyrics, fullscreen “lyrics stage”.
- **Features:** Per-application lyric source and delay settings, translations, lyric management, custom fonts, OBS capture support.
- **Why it matters:** This is the de facto reference product for “lyrics for whatever is playing on Windows.” Its documentation FAQ also lists the common failure modes: song-switch lag, 429 errors, Media Session not connected, inaccurate timeline, disappearing desktop lyrics, and stutter on track change.
- **Implication:** A new minimal app must be clearly simpler or better in a specific dimension; it will not out-feature Lyricify.
- **Sources:** https://github.com/WXRIW/Lyricify-App · https://docs.lyricify.app/lyricify-fusion/

### 6. jayfunc/BetterLyrics — the open-source heavyweight

- **License:** GPL-3.0
- **Stack:** C#, WinUI3, Win2D.
- **Features:** Full music player + lyrics visualizer; per-syllable karaoke, visualizers, dynamic backgrounds, desktop/taskbar/wallpaper/fullscreen modes, plugins, translations, LRCLIB and other providers.
- **Why it matters:** Shows the high end of the category: deep customization, plugin ecosystem, hardware-accelerated rendering, and a full player. A minimal Python overlay competes on simplicity, not features.
- **Source:** https://github.com/jayfunc/BetterLyrics

### 7. AhPlace2/lyrics-overlay — performance-focused alternative

- **License:** AGPL-3.0
- **Stack:** Rust + egui, DirectX 12 or CPU rendering.
- **Features:** SMTC + Shazam recognition, LRCLIB/NetEase, word-by-word lyrics, sync nudge, arrange mode, click-through, 13 MB executable, 57–176 MB RAM.
- **Why it matters:** If “lightweight” is the goal, a native Rust app sets a much higher bar than a Python/Qt app. Python/PySide6 can still win on hackability and development speed, but not on binary size or memory.
- **Source:** https://github.com/AhPlace2/lyrics-overlay

### 8. Others

- **FrontLine Lyrics** (GPL-3.0): Python backend + WPF; adds Shazam-style audio recognition when metadata is bad. Heavier but addresses the “unknown track” problem.
- **Lucent** (MIT): Electron, liquid-glass capsule, LAN room sync, built-in player. Different product direction.
- **Lyrow** (MIT): C#/WinUI3, full lyrics window + compact player + WASAPI edge glow; 728 tests. Good example of a polished native Windows app.
- **LyricParser** (MIT): older C# app reading Windows 10 media control; last pushed 2021. Historical prior art.
- **xsaardo/Spotify-Lyrics-Overlay** (no license): old Electron/Spotify-only overlay; not a good base for a new project.

---

## What Is Already Commoditized

These are not differentiators anymore:

- Frameless, transparent, always-on-top Qt window.
- Click-through with a tray-menu lock/unlock toggle.
- Drag to reposition, save coordinates.
- Font size presets.
- Windows SMTC/GSMTC detection with no API key.
- LRCLIB lookup with LRC parsing.
- Active line + next line display.
- System tray icon with quit.
- Basic caching.
- “Minimalist” claims.

Any of these alone will make the project look like another clone.

---

## Genuine Gaps / Opportunities

### 1. True word-level sync from LRCLIB's `lyricsfile` (strongest technical angle)

LRCLIB now exposes a `lyricsfile` YAML field alongside `syncedLyrics`. The docs say:

> `hasWordSync`: `true` when `lyricsfile` contains word-level timings (`lines[].words`).

This is real, authored word timing — not the length-proportional guessing used by viraj and many other apps. I verified a live record (`GET /api/get/3204715`, “Blinding Lights” by The Weeknd) with `hasWordSync: true` and word-level `start_ms` values.

A two-line overlay can render this as a **progressive fill/karaoke sweep inside the active line**, with no extra UI. When word sync is unavailable, fall back to line-level LRC. This would be a meaningful improvement over most Python overlays and is fully compatible with the requested minimalist UI.

**Cost:** add a YAML parser (`PyYAML`) and a word-level renderer; more complex tests.  
**Payoff:** genuinely better sync for the subset of tracks that have word-synced data.

### 2. Conservative matching: hide rather than show wrong lyrics

Most apps force a best match. A “no wrong lyrics” policy would:

- Score title/artist/duration with explicit thresholds.
- Prefer exact `/api/get` with duration.
- Use `/api/search` only when the confidence is high.
- Show a subtle status line when uncertain; hide on obvious non-music content.
- Optionally offer a tray action to search manually (scope creep; can defer).

This is a quality/trust differentiator, not a feature-count one. It directly addresses the pain point that made desktop-lyric add a manual song picker.

### 3. Correct multi-session arbitration

Windows can expose multiple sessions at once: paused Spotify, a playing YouTube tab, a paused local player. Many apps simply use the “current” session or hard-code Spotify. A robust arbiter should:

- Prefer sessions with `PlaybackStatus.PLAYING`.
- Prefer the most recently updated playing session.
- Fall back to the current session, then the first session.
- Re-evaluate on every poll so switching browser tabs or players is immediate.
- Optionally let the user pin a source from the tray.

This is a real reliability improvement and is easy to test with mocked sessions.

### 4. Multi-monitor and per-monitor DPI correctness

Small Qt overlays often break on mixed-DPI multi-monitor setups or restore off-screen. A minimal app that:

- Saves position in virtual-desktop coordinates.
- Validates the saved position against all current screens.
- Snaps to the bottom center of the monitor containing the saved position.
- Handles `screenChanged` / `geometryChanged`.
- Renders crisply at 125%, 150%, 200% scaling.

…is more useful than another settings panel.

### 5. Tiny, auditable, no-WebEngine Python core

viraj uses Qt WebEngine (155 MB installer). CLyrics uses a PowerShell subprocess. A single-process PySide6 app with direct `winsdk`, custom `QPainter` text rendering, no WebView, no album art, no database, and a small typed codebase would be easier to audit, modify, and embed. This is a legitimate niche even if it is not a mass-market product.

### 6. Local `.lrc` fallback and offline cache

- Check a local lyrics folder or sidecar `.lrc` before LRCLIB.
- Persist LRCLIB results to disk with a bounded cache.
- Negative-cache misses with a TTL.
- Respect `Retry-After` on 429/503.

This improves reliability and is friendly to LRCLIB. LyricsOnTheGo goes further with an optional local LRCLIB database; that is probably too heavy for this project.

### 7. Production-quality engineering

Most low-star clones lack tests, typing, graceful shutdown, rate-limit handling, and multi-monitor logic. A small codebase with:

- Unit tests for LRC parsing, title cleanup, matching, and timeline interpolation.
- A fake SMTC session layer for tests.
- Strict type hints and structured logging.
- Clean thread shutdown and single-instance handling.
- A documented privacy/rate-limit policy.

…can be better than its feature list suggests.

---

## Technical Constraints and Pitfalls

### `winsdk` is deprecated

- `winsdk` 1.0.0b10 was released 2023-08-11 and supports Python 3.8–3.12 (CPython 3.12 wheels exist).
- The `pywinrt/python-winsdk` repository was **archived on 2024-10-07**. Its README says the monolithic package is deprecated in favor of per-namespace `winrt-*` packages.
- Successor: `winrt-runtime` + `winrt-Windows.Media.Control` (PyWinRT 4.x), which supports Python 3.11+ and is actively maintained.
- The API surface is very similar; a compatibility import can prefer `winsdk` and fall back to `winrt`.
- If we strictly follow the original request, pin Python 3.12 and `winsdk==1.0.0b10`, and document the deprecation. If we want future-proofing, use the `winrt-*` packages and keep a `winsdk` compatibility path.

**Sources:** https://pypi.org/project/winsdk/ · https://github.com/pywinrt/python-winsdk · https://github.com/pywinrt/pywinrt

### SMTC timeline is a snapshot, not a clock

`session.get_timeline_properties().position` is a snapshot. It does not tick continuously. Use `last_updated_time` (UTC) to estimate staleness and extrapolate with a monotonic clock while playing. Clamp to duration and handle seeks. viraj's implementation is a good reference for the correction logic.

### Polling is usually better than COM events for this app

SMTC change events are useful for track changes but do not tick the position continuously, and keeping WinRT delegates alive across the COM boundary adds complexity. A 200–250 ms poll is cheap, simpler, and robust. Use events only if the added complexity is justified.

### LRCLIB API requirements

- Identify the client with a `User-Agent` naming the app and version plus a project URL or contact email. Without it, limits are lower.
- Approximate limits (per the docs): ~45 req/min generic, ~90 req/min identified, higher for registered apps. Exact limits may change.
- Honor `Retry-After` on 429/503.
- `/api/get` requires `track_name` and `artist_name`; `album_name` and `duration` improve matching. Duration matching is ±2 seconds.
- `/api/search` returns at most 20 results; use it as a fallback and rank by title/artist/duration.
- `/api/v2/search` is a beta lightweight search endpoint returning availability flags; fetch `/api/get/{id}` for lyrics.
- Cache successful and failed lookups to avoid hammering the API.
- Do not request lyrics on every poll. Request once per track identity, and debounce rapid skipping.
- Lyrics are copyrighted; the app should not bundle lyrics or redistribute a database. Use the API at runtime, cache locally for personal use, and make the privacy/legal behavior explicit.

**Sources:** https://lrclib.net/docs · https://lrclib.net/llms.txt

### Browser / YouTube metadata is messy

- The “artist” is often the channel name, not the artist.
- Titles contain `(Official Video)`, `[4K]`, `(Lyrics)`, `feat. ...`, `- Topic`, `VEVO`, etc.
- Search by cleaned title and optionally by artist; rank candidates by title containment, artist similarity, and duration proximity.
- Non-music videos (podcasts, interviews, tutorials, gameplay, trailers) often have no lyrics. Hide instead of showing a wrong match.
- A manual “choose another result” UI is valuable but can be deferred.

### Qt click-through and window flags

- `Qt.WindowTransparentForInput` works, but changing window flags can hide/recreate the native window. Re-show after toggling.
- `WA_TransparentForMouseEvents` is a useful belt-and-suspenders attribute, but it is not a substitute for OS-level click-through.
- On Windows, `WS_EX_TRANSPARENT` is an alternative and is what CLyrics uses.
- Keep the tray icon as the recovery path: if the overlay is click-through and off-screen, the user must still be able to unlock/reset it.
- Save coordinates only after a drag; validate on startup and reset if off-screen.

### Text rendering

- `QGraphicsDropShadowEffect` on a translucent frameless top-level window can be slow or visually inconsistent. Custom `QPainter` rendering with `QPainterPath` (stroke + fill) gives crisp outlines and shadows.
- Elide long lines or wrap to a maximum width. Do not let a long lyric line run off-screen.
- Test at 100%, 125%, 150%, and 200% DPI.
- For the active line, a subtle dark outline plus a small shadow is more readable than a solid background box.

### Threading and lifecycle

- Run `winsdk` async calls in a dedicated thread with its own asyncio event loop.
- Use Qt signals to marshal track/position updates to the UI thread.
- Run LRCLIB requests on a worker thread; use a “latest request wins” queue and ignore stale results.
- Stop threads and the asyncio loop cleanly on quit. Avoid daemon threads that keep COM objects alive after Qt exits.
- Handle rapid song skipping without spawning unbounded threads; a single worker with a replaceable pending request is sufficient.

### Packaging and licensing

- PyInstaller needs `--collect-all winsdk` (viraj's README notes this).
- PySide6 is LGPL-3.0/GPL/commercial. If distributing a closed-source app, comply with LGPL (dynamic linking, license notices, ability to replace Qt). For a personal/open-source app, this is simpler.
- `winsdk` is MIT. `requests` is Apache-2.0.
- If adding `PyYAML` for word-level lyrics, it is MIT.
- Do not copy code from GPL-3.0 (BetterLyrics, FrontLine) or AGPL-3.0 (AhPlace2) projects unless the new project is GPL/AGPL-compatible. MIT projects (viraj, CLyrics, LyricsOnTheGo, desktop-lyric) are safer references, but write original code and retain attribution where appropriate.

---

## Recommendation

### If the goal is learning, customization, or a personal tool

Proceed with the requested minimal build, but add the differentiators that do not bloat the UI:

1. **Pure-text two-line overlay** with custom outline/shadow rendering.
2. **Direct `winsdk` SMTC polling** with correct timeline interpolation and session arbitration.
3. **LRCLIB line sync**, with optional **true word-level fill** from `lyricsfile` when available.
4. **Conservative matching** and a clear “no lyrics / uncertain” state.
5. **Multi-monitor/DPI-safe position persistence.**
6. **Small, typed, tested, no-WebEngine codebase.**

This gives a reason to exist beyond “another clone” without turning it into Lyricify.

### If the goal is a product

Pick one sharp wedge:

- **“No fake timing”**: true word-level sync from LRCLIB's `lyricsfile`, line fallback.
- **“Never wrong lyrics”**: conservative matching and explicit uncertainty.
- **“The smallest auditable overlay”**: no Electron/WebEngine, one process, pure text, tiny dependency surface.
- **“Perfect on every monitor”**: per-monitor DPI and position handling.

Trying to compete on feature count with Lyricify/BetterLyrics is not realistic for a small Python app.

### If the goal is community impact

Contributing to viraj's MIT project or CLyrics may be more valuable than starting another clone — but their architectures (Qt WebEngine / PowerShell subprocess) differ from the requested direct-`winsdk` design. A clean minimal implementation can still serve as a reference or a base for others.

---

## Sources

- Lyricify App: https://github.com/WXRIW/Lyricify-App
- Lyricify Fusion docs: https://docs.lyricify.app/lyricify-fusion/
- Lyricify FAQ (song-switch lag, timeline): https://docs.lyricify.app/lyricify-4/faq/song-switch-lag/ · https://docs.lyricify.app/lyricify-4/faq/inaccurate-timeline/
- BetterLyrics: https://github.com/jayfunc/BetterLyrics
- viraj Spotify Lyrics Overlay: https://github.com/viraj-rgb/spotify-lyrics-overlay
- CLyrics: https://github.com/ScotsGamez/CLyrics
- LyricsOnTheGo: https://github.com/The-MuffinDev/LyricsOnTheGo
- desktop-lyric: https://github.com/Epi-Lo/desktop-lyric
- AhPlace2 Lyrics Overlay: https://github.com/AhPlace2/lyrics-overlay
- Lucent: https://github.com/Rex11929282/lucent-updates
- Lyrow: https://github.com/owergungor/Lyrow
- FrontLine Lyrics: https://github.com/juliocax/FrontLine-Lyrics-Desktop
- LyricParser: https://github.com/mikan9/LyricParser
- xsaardo Spotify-Lyrics-Overlay: https://github.com/xsaardo/Spotify-Lyrics-Overlay
- winsdk on PyPI: https://pypi.org/project/winsdk/
- winsdk archive/deprecation: https://github.com/pywinrt/python-winsdk
- PyWinRT successor: https://github.com/pywinrt/pywinrt
- LRCLIB API docs: https://lrclib.net/docs
- LRCLIB word-sync example: https://lrclib.net/api/get/3204715
