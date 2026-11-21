"""Persistent, human-editable settings for the overlay."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APP_NAME = "FloatingLyrics"

FONT_PRESETS: dict[str, int] = {
    "small": 16,
    "medium": 22,
    "large": 30,
}
DEFAULT_FONT_SIZE = "medium"


def config_dir() -> Path:
    """Return the per-user configuration directory, creating it if needed."""
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass(slots=True)
class Settings:
    """User-visible preferences persisted between runs."""

    font_size: str = DEFAULT_FONT_SIZE
    locked: bool = True
    x: int | None = None
    y: int | None = None
    screen: str | None = None

    @property
    def active_px(self) -> int:
        return FONT_PRESETS.get(self.font_size, FONT_PRESETS[DEFAULT_FONT_SIZE])

    @property
    def next_px(self) -> int:
        return max(10, round(self.active_px * 0.62))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> Settings:
    """Load settings, falling back to defaults on any malformed file."""
    path = _settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable settings file %s: %s", path, exc)
        return Settings()

    if not isinstance(raw, dict):
        log.warning("Ignoring settings file with unexpected shape: %s", path)
        return Settings()

    font_size = raw.get("font_size")
    if font_size not in FONT_PRESETS:
        font_size = DEFAULT_FONT_SIZE

    def _coord(key: str) -> int | None:
        value = raw.get(key)
        return value if isinstance(value, int) else None

    screen = raw.get("screen")
    return Settings(
        font_size=font_size,
        locked=bool(raw.get("locked", True)),
        x=_coord("x"),
        y=_coord("y"),
        screen=screen if isinstance(screen, str) else None,
    )


def save_settings(settings: Settings) -> None:
    """Atomically persist settings; failures are logged, never fatal."""
    path = _settings_path()
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(settings.to_dict(), indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        log.warning("Could not save settings to %s: %s", path, exc)
