"""Offline tests for the Phase 5 sync engine.

Fully offline: a FakeRead serves list/get meeting responses (and can raise), a
FakeDropbox records uploads (and can raise), so full sync, incremental skipping,
timestamp-change re-upload, ``full`` override, and partial-failure isolation are
all exercised deterministically. The 'Done when' checkpoint — first run uploads
everything, second run uploads nothing — is asserted directly.
"""

from __future__ import annotations

import json

import pytest

from readbox.dropbox_client import DropboxError
from readbox.read_client import ReadAPIError
from readbox.sync import (
    dropbox_paths,
    load_state,
    save_state,
    sync,
)


# -- fakes -------------------------------------------------------------------


def _meeting(mid: str, *, start: int, end: int, title: str) -> dict:
    return {"id": mid, "start_time_ms": start, "end_time_ms": end, "title": title}


class FakeRead:
    def __init__(self, meetings, *, fail_ids=()):
        self._meetings = meetings
        self._fail_ids = set(fail_ids)
        self.get_calls: list[str] = []

    def list_meetings(self):
        return list(self._meetings)

    def get_meeting(self, meeting_id, **kw):
        self.get_calls.append(meeting_id)
        if meeting_id in self._fail_ids:
            raise ReadAPIError(500, "boom")
        for m in self._meetings:
            if m["id"] == meeting_id:
                return dict(m)
        raise ReadAPIError(404, "not found")


class FakeDropbox:
    def __init__(self, *, fail_paths=()):
        self.uploads: list[tuple[str, bytes]] = []
        self._fail_paths = set(fail_paths)

    def upload(self, data, dropbox_path, **kw):
        if dropbox_path in self._fail_paths:
            raise DropboxError(f"upload {dropbox_path} failed")
        self.uploads.append((dropbox_path, data))


# The two-meeting corpus most tests share.
MEETINGS = [
    _meeting("01A", start=1787328000000, end=1787330520000, title="Engineering Sync"),
    _meeting("01B", start=1787257800000, end=1787260500000, title="Q4 Roadmap"),
]


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "state.json"


# -- path building -----------------------------------------------------------


def test_dropbox_paths_uses_year_month_and_sanitized_title():
    md, js = dropbox_paths(MEETINGS[0], root="Read AI")
    assert md == "/Read AI/2026/08/2026-08-21 Engineering Sync.md"
    assert js == "/Read AI/2026/08/2026-08-21 Engineering Sync.json"


# -- full sync ---------------------------------------------------------------


def test_full_sync_uploads_md_and_json_for_every_meeting(state_file):
    read, dbx = FakeRead(MEETINGS), FakeDropbox()
    result = sync(read, dbx, state_path=state_file)

    assert result.counts == {"uploaded": 2, "skipped": 0, "failed": 0}
    # Two files (md + json) per meeting.
    assert len(dbx.uploads) == 4
    paths = [p for p, _ in dbx.uploads]
    assert any(p.endswith("Engineering Sync.md") for p in paths)
    assert any(p.endswith("Engineering Sync.json") for p in paths)
    # State persisted for both meetings.
    state = load_state(state_file)
    assert set(state["meetings"]) == {"01A", "01B"}
    assert state["meetings"]["01A"]["timestamp"] == 1787330520000


# -- incremental (the plan's 'Done when') ------------------------------------


def test_second_run_uploads_nothing_new(state_file):
    read1, dbx1 = FakeRead(MEETINGS), FakeDropbox()
    sync(read1, dbx1, state_path=state_file)

    read2, dbx2 = FakeRead(MEETINGS), FakeDropbox()
    result = sync(read2, dbx2, state_path=state_file)

    assert result.counts == {"uploaded": 0, "skipped": 2, "failed": 0}
    assert dbx2.uploads == []
    # Skipped meetings are never even fetched — the efficiency win.
    assert read2.get_calls == []


def test_changed_timestamp_triggers_reupload(state_file):
    sync(FakeRead(MEETINGS), FakeDropbox(), state_path=state_file)

    # Same id, later end time → treated as changed.
    changed = [
        _meeting("01A", start=1787328000000, end=1787330999999, title="Engineering Sync"),
        MEETINGS[1],
    ]
    read, dbx = FakeRead(changed), FakeDropbox()
    result = sync(read, dbx, state_path=state_file)

    assert result.counts == {"uploaded": 1, "skipped": 1, "failed": 0}
    assert result.uploaded == ["01A"]
    assert load_state(state_file)["meetings"]["01A"]["timestamp"] == 1787330999999


def test_full_flag_reuploads_unchanged(state_file):
    sync(FakeRead(MEETINGS), FakeDropbox(), state_path=state_file)

    read, dbx = FakeRead(MEETINGS), FakeDropbox()
    result = sync(read, dbx, state_path=state_file, full=True)

    assert result.counts == {"uploaded": 2, "skipped": 0, "failed": 0}
    assert len(dbx.uploads) == 4


# -- partial failure ---------------------------------------------------------


def test_read_failure_is_isolated(state_file):
    read = FakeRead(MEETINGS, fail_ids={"01A"})
    dbx = FakeDropbox()
    result = sync(read, dbx, state_path=state_file)

    assert result.counts == {"uploaded": 1, "skipped": 0, "failed": 1}
    assert result.uploaded == ["01B"]
    assert result.failed[0][0] == "01A"
    # The good meeting still landed and only it is recorded in state.
    assert set(load_state(state_file)["meetings"]) == {"01B"}


def test_dropbox_failure_is_isolated(state_file):
    md_a, _ = dropbox_paths(MEETINGS[0])
    read = FakeRead(MEETINGS)
    dbx = FakeDropbox(fail_paths={md_a})
    result = sync(read, dbx, state_path=state_file)

    assert result.counts["failed"] == 1
    assert result.failed[0][0] == "01A"
    assert set(load_state(state_file)["meetings"]) == {"01B"}


# -- state file --------------------------------------------------------------


def test_state_roundtrip_and_permissions(state_file):
    save_state(state_file, {"version": 1, "meetings": {"x": {"timestamp": 1}}})
    assert load_state(state_file)["meetings"]["x"]["timestamp"] == 1
    assert (state_file.stat().st_mode & 0o777) == 0o600


def test_corrupt_state_falls_back_to_empty(state_file):
    state_file.write_text("{ not json")
    state = load_state(state_file)
    assert state == {"version": 1, "meetings": {}}


def test_missing_state_starts_empty(state_file):
    assert load_state(state_file) == {"version": 1, "meetings": {}}
