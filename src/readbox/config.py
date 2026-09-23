"""Config directory, paths, and user-adjustable settings.

Everything sensitive — OAuth tokens, Read's per-user client credentials, and the
sync state file — lives in the OS-appropriate per-user config directory, never in
the repo.

Phase 7 adds :class:`Settings`: user-tunable output options (Dropbox root folder,
folder layout, filename, date format) persisted to ``settings.json``. Everything has
a default so the tool works with zero configuration; the defaults reproduce the exact
behavior from earlier phases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path

from platformdirs import user_config_dir

from .formatter import (
    DEFAULT_DATE_FORMAT,
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_FOLDER_TEMPLATE,
)

APP_NAME = "readbox"

#: Default top-level Dropbox folder (not a formatter concern, so it lives here).
DEFAULT_ROOT_FOLDER = "Read AI"


def config_dir() -> Path:
    """Return the per-user config directory, creating it (owner-only) if needed."""
    path = Path(user_config_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    # Owner-only: tokens and client secrets will live here.
    path.chmod(0o700)
    return path


def tokens_path() -> Path:
    """Path to the stored OAuth tokens / client credentials (Phase 4)."""
    return config_dir() / "tokens.json"


def state_path() -> Path:
    """Path to the incremental-sync state file (Phase 5)."""
    return config_dir() / "state.json"


def settings_path() -> Path:
    """Path to the user-adjustable settings file (Phase 7)."""
    return config_dir() / "settings.json"


# -- user settings -----------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """User-tunable output options. Defaults reproduce the pre-Phase-7 behavior.

    Template tokens available: ``{year}`` ``{month}`` ``{day}`` ``{date}``
    ``{title}``. ``{date}`` is rendered with :attr:`date_format`.
    """

    root_folder: str = DEFAULT_ROOT_FOLDER
    folder_template: str = DEFAULT_FOLDER_TEMPLATE
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    date_format: str = DEFAULT_DATE_FORMAT

    def as_dict(self) -> dict[str, str]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


#: The configurable field names, in display order.
SETTING_KEYS: tuple[str, ...] = tuple(f.name for f in fields(Settings))


def load_settings(path: Path | None = None) -> Settings:
    """Load settings, overlaying ``settings.json`` on the defaults.

    Tolerant by design: a missing file, corrupt JSON, unknown keys, or non-string
    values all fall back to defaults rather than crashing a sync.
    """
    path = path or settings_path()
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    overrides = {
        k: v for k, v in data.items() if k in SETTING_KEYS and isinstance(v, str) and v
    }
    return replace(Settings(), **overrides)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """Persist settings to ``settings.json`` (pretty-printed)."""
    path = path or settings_path()
    path.write_text(json.dumps(settings.as_dict(), indent=2, sort_keys=True) + "\n")
