"""Phase 5 — the sync engine: export Read AI meetings into Dropbox.

Design (per ``claude/plan.md`` Phase 5):

* **Full sync first, correct before clever.** List every meeting, fetch its full
  report, render Markdown + JSON, and upload to ``/{root}/YYYY/MM/<name>.{md,json}``.
  Uploads are idempotent (Dropbox overwrite mode keys off the path), so re-running
  never creates ``file (1).md`` duplicates.
* **Then incremental.** A JSON state file (``config.state_path()``) records each
  synced meeting id and its last-seen timestamp. On a normal run we skip meetings
  whose timestamp is unchanged; ``full=True`` re-uploads everything. Read has no
  reliable "updated since" filter for completed reports, so the design is the plan's
  fallback: page all meetings and diff against local state.
* **Partial failures don't abort the run.** One meeting that fails to fetch/upload is
  recorded and skipped; the rest still sync.

The engine takes injected ``ReadClient`` / ``DropboxClient`` instances so it can be
tested fully offline. :func:`run_sync` is the thin wrapper that wires real auth.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import config
from .auth import TokenStore, read_access_token
from .config import Settings
from .dropbox_client import DropboxClient, DropboxError
from .formatter import filename_for, folder_for, to_json, to_markdown
from .read_client import ReadAPIError, ReadClient

#: Current on-disk state schema version, so future changes can migrate cleanly.
STATE_VERSION = 1


# -- results -----------------------------------------------------------------


@dataclass
class SyncResult:
    """Outcome of a sync run — the CLI (Phase 6) renders this."""

    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "uploaded": len(self.uploaded),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
        }

    def summary_line(self) -> str:
        c = self.counts
        return (
            f"{c['uploaded']} uploaded, {c['skipped']} unchanged, "
            f"{c['failed']} failed"
        )


# -- state file --------------------------------------------------------------


def load_state(path: Path) -> dict[str, Any]:
    """Load the sync state file, returning a fresh skeleton if it's absent."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {"version": STATE_VERSION, "meetings": {}}
    except json.JSONDecodeError:
        # A corrupt state file must never wedge sync — fall back to a full resync.
        return {"version": STATE_VERSION, "meetings": {}}
    data.setdefault("version", STATE_VERSION)
    data.setdefault("meetings", {})
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomically write the state file (owner-only), never leaving it half-written."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, path)
    os.chmod(path, 0o600)


# -- helpers -----------------------------------------------------------------


def _meeting_timestamp(meeting: dict[str, Any]) -> Optional[int]:
    """The change signal for a meeting: prefer end time, fall back to start."""
    for key in ("end_time_ms", "start_time_ms"):
        val = meeting.get(key)
        if isinstance(val, int):
            return val
    return None


def dropbox_paths(
    meeting: dict[str, Any], settings: Optional[Settings] = None
) -> tuple[str, str]:
    """Return the ``(markdown, json)`` Dropbox paths for a meeting.

    Only ``start_time_ms`` + ``title`` are needed, both present on the list
    summary, so the destination can be computed without fetching the full report.
    Folder layout, filename, and date format come from ``settings`` (defaults
    reproduce ``/Read AI/YYYY/MM/YYYY-MM-DD Title``).
    """
    settings = settings or Settings()
    folder = folder_for(
        meeting, template=settings.folder_template, date_format=settings.date_format
    )
    stem = filename_for(
        meeting,
        template=settings.filename_template,
        date_format=settings.date_format,
    )
    parts = [p.strip("/") for p in (settings.root_folder, folder, stem) if p.strip("/")]
    base = "/" + "/".join(parts)
    return f"{base}.md", f"{base}.json"


# -- the engine --------------------------------------------------------------


def sync(
    read: ReadClient,
    dropbox: DropboxClient,
    *,
    settings: Optional[Settings] = None,
    state_path: Optional[Path] = None,
    full: bool = False,
    start_time_ms: Optional[int] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> SyncResult:
    """Export meetings to Dropbox, incrementally by default.

    Args:
        read: source client (already holding a valid token).
        dropbox: destination client.
        settings: output options (root folder, templates); defaults to
            ``config.load_settings()``.
        state_path: where the incremental state lives; defaults to
            ``config.state_path()``.
        full: re-upload every meeting, ignoring stored timestamps.
        start_time_ms: if set, only consider meetings at/after this epoch-ms
            cutoff (server-side filter — powers the CLI's ``--days``).
        logger: optional sink for per-meeting progress lines.
    """
    settings = settings or config.load_settings()
    state_path = state_path or config.state_path()
    state = load_state(state_path)
    meetings_state: dict[str, Any] = state["meetings"]
    result = SyncResult()

    def log(msg: str) -> None:
        if logger is not None:
            logger(msg)

    for summary in read.list_meetings(start_time_ms=start_time_ms):
        meeting_id = summary.get("id")
        if not meeting_id:
            continue
        timestamp = _meeting_timestamp(summary)
        prior = meetings_state.get(meeting_id)

        # Incremental skip: known meeting whose timestamp hasn't moved.
        if not full and prior and prior.get("timestamp") == timestamp:
            result.skipped.append(meeting_id)
            log(f"= unchanged {meeting_id}")
            continue

        try:
            full_meeting = read.get_meeting(meeting_id)
            md_path, json_path = dropbox_paths(summary, settings)
            dropbox.upload(to_markdown(full_meeting).encode("utf-8"), md_path)
            dropbox.upload(to_json(full_meeting).encode("utf-8"), json_path)
        except (ReadAPIError, DropboxError) as exc:
            # Partial failure: log and keep going so one bad meeting can't kill the run.
            result.failed.append((meeting_id, str(exc)))
            log(f"! failed {meeting_id}: {exc}")
            continue

        meetings_state[meeting_id] = {
            "timestamp": timestamp,
            "title": summary.get("title"),
            "paths": [md_path, json_path],
            "synced_at": time.time(),
        }
        result.uploaded.append(meeting_id)
        log(f"+ synced {meeting_id} -> {md_path}")

    save_state(state_path, state)
    return result


def run_sync(
    *,
    store: Optional[TokenStore] = None,
    settings: Optional[Settings] = None,
    state_path: Optional[Path] = None,
    full: bool = False,
    start_time_ms: Optional[int] = None,
    logger: Optional[Callable[[str], None]] = None,
) -> SyncResult:
    """Convenience wrapper: build real clients from stored auth, then :func:`sync`."""
    store = store or TokenStore()
    token = read_access_token(store)
    read = ReadClient(token)
    dropbox = DropboxClient.from_store(store)
    return sync(
        read,
        dropbox,
        settings=settings,
        state_path=state_path,
        full=full,
        start_time_ms=start_time_ms,
        logger=logger,
    )
