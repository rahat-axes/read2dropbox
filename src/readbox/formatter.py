"""Render a Read AI meeting object to Markdown, JSON, and a filename.

Pure and offline — no network, no I/O. Everything is driven off the meeting dict
returned by ``read_client.get_meeting``.

Defensive by design: the real API and the synthetic fixtures disagree on a few
inner shapes (verified 2026-08-28). ``action_items`` / ``key_questions`` /
``topics`` come back as **plain strings** from the real API but as
``{"text": ...}`` objects in the synthetic fixtures; chapters use ``description``
(real) vs ``summary`` (synthetic). The helpers below accept either form so both
fixtures render cleanly.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

# Characters illegal in filenames on Windows (superset of what Unix dislikes),
# plus ASCII control chars, get replaced.
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


# -- small field extractors ---------------------------------------------------


def _text_of(item: Any) -> str:
    """Pull the human text out of a string-or-object list item."""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("text", "title", "question", "topic", "description"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return str(item).strip()


def _ms_to_utc(ms: Optional[int]) -> Optional[datetime]:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def _fmt_dt(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "unknown"


def _fmt_number(val: float) -> str:
    """Trim noisy float precision (e.g. 88.7733… -> 88.77), keep ints clean."""
    return f"{round(float(val), 2):g}"


# -- section renderers --------------------------------------------------------


def _header(meeting: dict) -> list[str]:
    title = (meeting.get("title") or "Untitled meeting").strip()
    lines = [f"# {title}", ""]

    start = _ms_to_utc(meeting.get("start_time_ms"))
    end = _ms_to_utc(meeting.get("end_time_ms"))
    if start and end:
        lines.append(f"**Date:** {_fmt_dt(start)} – {_fmt_dt(end)}")
    elif start:
        lines.append(f"**Date:** {_fmt_dt(start)}")

    if meeting.get("platform"):
        lines.append(f"**Platform:** {meeting['platform']}")
    if meeting.get("report_url"):
        lines.append(f"**Report:** {meeting['report_url']}")
    return lines


def _participants(meeting: dict) -> list[str]:
    people = meeting.get("participants") or []
    if not people:
        return []
    lines = ["## Participants", ""]
    for p in people:
        name = (p.get("name") or p.get("email") or "Unknown").strip()
        email = p.get("email")
        entry = f"- {name} <{email}>" if email else f"- {name}"
        if p.get("attended") is False:
            entry += " _(invited, did not attend)_"
        lines.append(entry)
    return lines


def _summary(meeting: dict) -> list[str]:
    summary = meeting.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return []
    return ["## Summary", "", summary.strip()]


def _action_items(meeting: dict) -> list[str]:
    items = meeting.get("action_items") or []
    if not items:
        return []
    lines = ["## Action Items", ""]
    for item in items:
        text = _text_of(item)
        if not text:
            continue
        assignee = item.get("assignee") if isinstance(item, dict) else None
        suffix = f" — **{assignee}**" if assignee else ""
        lines.append(f"- [ ] {text}{suffix}")
    return lines


def _bullet_section(meeting: dict, field: str, heading: str) -> list[str]:
    items = meeting.get(field) or []
    if not items:
        return []
    lines = [f"## {heading}", ""]
    for item in items:
        text = _text_of(item)
        if text:
            lines.append(f"- {text}")
    return lines


def _chapters(meeting: dict) -> list[str]:
    chapters = meeting.get("chapter_summaries") or []
    if not chapters:
        return []
    lines = ["## Chapters", ""]
    for ch in chapters:
        if not isinstance(ch, dict):
            lines.append(f"- {_text_of(ch)}")
            continue
        title = (ch.get("title") or "").strip()
        body = (ch.get("description") or ch.get("summary") or "").strip()
        if title and body:
            lines.append(f"### {title}")
            lines.append("")
            lines.append(body)
            lines.append("")
        elif title:
            lines.append(f"- **{title}**")
        elif body:
            lines.append(f"- {body}")
    return lines


def _metrics(meeting: dict) -> list[str]:
    metrics = meeting.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return []
    parts = []
    for label, key in (
        ("Read score", "read_score"),
        ("Sentiment", "sentiment"),
        ("Engagement", "engagement"),
    ):
        val = metrics.get(key)
        if isinstance(val, (int, float)):
            parts.append(f"{label}: {_fmt_number(val)}")
    if not parts:
        return []
    return ["## Metrics", "", "- " + "  |  ".join(parts)]


def _transcript(meeting: dict) -> list[str]:
    transcript = meeting.get("transcript")
    if not isinstance(transcript, dict):
        return []
    turns = transcript.get("turns") or []
    if not turns:
        text = transcript.get("text")
        if isinstance(text, str) and text.strip():
            return ["## Transcript", "", text.strip()]
        return []
    lines = ["## Transcript", ""]
    for turn in turns:
        speaker = ((turn.get("speaker") or {}).get("name") or "Unknown").strip()
        text = (turn.get("text") or "").strip()
        if text:
            lines.append(f"**{speaker}:** {text}")
            lines.append("")
    return lines


# -- public API ---------------------------------------------------------------


def to_markdown(meeting: dict) -> str:
    """Render a meeting to the Markdown report layout."""
    sections = [
        _header(meeting),
        _summary(meeting),
        _participants(meeting),
        _action_items(meeting),
        _bullet_section(meeting, "key_questions", "Key Questions"),
        _bullet_section(meeting, "topics", "Topics"),
        _chapters(meeting),
        _metrics(meeting),
        _transcript(meeting),
    ]
    blocks = ["\n".join(s).rstrip() for s in sections if s]
    return "\n\n".join(blocks).rstrip() + "\n"


def to_json(meeting: dict) -> str:
    """Pretty-print the raw meeting response (nothing is lost)."""
    return json.dumps(meeting, indent=2, ensure_ascii=False) + "\n"


def filename_for(meeting: dict) -> str:
    """Return a ``YYYY-MM-DD Title`` stem (no extension), sanitized for disk."""
    start = _ms_to_utc(meeting.get("start_time_ms"))
    date = start.strftime("%Y-%m-%d") if start else "0000-00-00"
    title = (meeting.get("title") or "Untitled").strip()
    return f"{date} {sanitize_filename(title)}"


def sanitize_filename(name: str) -> str:
    """Strip filename-illegal characters and tidy whitespace/dots."""
    cleaned = _ILLEGAL_FILENAME_CHARS.sub("", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Windows dislikes trailing dots/spaces on names.
    cleaned = cleaned.rstrip(" .")
    return cleaned or "Untitled"
