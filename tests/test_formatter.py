"""Offline tests for formatter, run against both synthetic and REAL fixtures.

The real fixture matters most: its inner field shapes (plain-string action_items,
chapter `description`) differ from the synthetic ones, and the formatter must
handle both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from readbox.formatter import (
    filename_for,
    sanitize_filename,
    to_json,
    to_markdown,
)

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "meeting_01M0JGSW00XA19RMEX1TREZ76W.json"
REAL = FIXTURES / "real" / "meeting_01KDRNJD1KAG9RTPDJWED2V1DH.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text())


@pytest.fixture
def synthetic() -> dict:
    return load(SYNTHETIC)


@pytest.fixture
def real() -> dict:
    return load(REAL)


# -- to_markdown --------------------------------------------------------------


def test_markdown_synthetic_has_all_sections(synthetic):
    md = to_markdown(synthetic)
    for heading in (
        "# Engineering Sync",
        "## Summary",
        "## Participants",
        "## Action Items",
        "## Key Questions",
        "## Topics",
        "## Metrics",
        "## Transcript",
    ):
        assert heading in md
    # Synthetic action items are now plain strings (matching the real API shape).
    assert "- [ ] Dana Whitfield will quarantine the three flakiest" in md
    # Non-attendee marked.
    assert "_(invited, did not attend)_" in md


def test_markdown_action_item_object_form_renders_assignee():
    # Some sources (older/other payloads) may send objects; keep that path working.
    md = to_markdown(
        {
            "title": "T",
            "action_items": [{"text": "Ship the thing", "assignee": "Ada"}],
        }
    )
    assert "- [ ] Ship the thing — **Ada**" in md


def test_markdown_real_string_lists_render(real):
    md = to_markdown(real)
    assert md.startswith("# Onboarding to Read AI - Sample Report")
    assert "## Action Items" in md
    # Real action_items are plain strings -> checkbox line, no crash, no assignee.
    assert "- [ ] Karen Holt will send documentation" in md
    assert "## Key Questions" in md
    assert "## Topics" in md
    # Real chapters use `description`.
    assert "## Chapters" in md
    assert "### Read AI Onboarding Essentials" in md


def test_markdown_transcript_speaker_lines(real):
    md = to_markdown(real)
    assert "**Evan Shaw:** Bye, everyone." in md


def test_markdown_ends_with_single_newline(real, synthetic):
    for m in (real, synthetic):
        md = to_markdown(m)
        assert md.endswith("\n")
        assert not md.endswith("\n\n")


def test_markdown_omits_empty_sections():
    md = to_markdown({"title": "Bare", "start_time_ms": 1767301200000})
    assert "# Bare" in md
    assert "## Summary" not in md
    assert "## Transcript" not in md


# -- to_json ------------------------------------------------------------------


def test_to_json_roundtrips(real):
    assert json.loads(to_json(real)) == real


# -- filename_for / sanitize --------------------------------------------------


def test_filename_for_real(real):
    assert filename_for(real) == "2026-01-01 Onboarding to Read AI - Sample Report"


def test_sanitize_strips_illegal_chars():
    assert sanitize_filename('a/b:c*d?"e<f>g|h') == "abcdefgh"


def test_sanitize_collapses_whitespace_and_trailing_dots():
    assert sanitize_filename("  Weekly   Sync ... ") == "Weekly Sync"


def test_sanitize_empty_falls_back():
    assert sanitize_filename("///") == "Untitled"
