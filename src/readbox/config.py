"""Config directory and paths.

Everything sensitive — OAuth tokens, Read's per-user client credentials, and the
sync state file — lives in the OS-appropriate per-user config directory, never in
the repo. Later phases (4, 5, 7) build on the paths defined here.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_config_dir

APP_NAME = "readbox"


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
