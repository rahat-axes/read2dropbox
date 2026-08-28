"""Offline tests for read_client, driven entirely by Phase 1 fixtures.

No network is touched: a FakeSession stands in for requests.Session and returns
canned responses, so pagination, expand params, and retry/backoff are all
exercised deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

from readbox.read_client import (
    DEFAULT_EXPAND,
    ReadAPIError,
    ReadClient,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.reason = "FakeReason"
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Dispatches to a handler(method, path, params) -> FakeResponse and records calls."""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[dict] = []

    def request(self, method, url, headers=None, params=None, timeout=None):
        path = urlparse(url).path
        self.calls.append(
            {"method": method, "path": path, "params": params, "headers": headers}
        )
        return self._handler(method, path, params or {})


# -- get_meeting --------------------------------------------------------------


def test_get_meeting_parses_fixture():
    meeting_id = "01M0JGSW00XA19RMEX1TREZ76W"
    fixture = load(f"meeting_{meeting_id}")

    def handler(method, path, params):
        assert path == f"/v1/meetings/{meeting_id}"
        return FakeResponse(json_body=fixture)

    session = FakeSession(handler)
    client = ReadClient("tok", session=session)
    result = client.get_meeting(meeting_id)

    assert result["id"] == meeting_id
    assert "summary" in result and "transcript" in result


def test_get_meeting_sends_expand_and_auth():
    def handler(method, path, params):
        return FakeResponse(json_body={"id": "x"})

    session = FakeSession(handler)
    ReadClient("secret-token", session=session).get_meeting("x")

    call = session.calls[-1]
    assert call["params"]["expand[]"] == list(DEFAULT_EXPAND)
    assert call["headers"]["Authorization"] == "Bearer secret-token"


def test_get_meeting_expand_none_sends_no_params():
    def handler(method, path, params):
        return FakeResponse(json_body={"id": "x"})

    session = FakeSession(handler)
    ReadClient("tok", session=session).get_meeting("x", expand=None)
    assert session.calls[-1]["params"] is None


# -- list_meetings / pagination ----------------------------------------------


def test_list_meetings_single_page():
    def handler(method, path, params):
        return FakeResponse(json_body=load("list_meetings"))

    client = ReadClient("tok", session=FakeSession(handler))
    meetings = client.list_meetings()
    assert [m["id"] for m in meetings] == [
        "01M0JGSW00XA19RMEX1TREZ76W",
        "01M0GDVHA04AE3B6WN6ET4VF81",
        "01M0AP5VC00TRE5A5C4BGN7P7W",
    ]


def test_list_meetings_follows_cursor():
    page1 = load("list_meetings_page1")
    page2 = load("list_meetings_page2")

    def handler(method, path, params):
        if params.get("cursor") == page1["next"]:
            return FakeResponse(json_body=page2)
        assert "cursor" not in params  # first page: no cursor
        return FakeResponse(json_body=page1)

    session = FakeSession(handler)
    meetings = ReadClient("tok", session=session).list_meetings()

    # Both pages, in order, deduplicated across the cursor boundary.
    assert [m["id"] for m in meetings] == [
        "01M0JGSW00XA19RMEX1TREZ76W",
        "01M0GDVHA04AE3B6WN6ET4VF81",
        "01M0AP5VC00TRE5A5C4BGN7P7W",
    ]
    assert len(session.calls) == 2


# -- retry / backoff ----------------------------------------------------------


def test_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr("readbox.read_client.time.sleep", lambda s: None)
    responses = [
        FakeResponse(status_code=503, text="unavailable"),
        FakeResponse(status_code=429, headers={"Retry-After": "0"}),
        FakeResponse(json_body=load("list_meetings")),
    ]

    def handler(method, path, params):
        return responses.pop(0)

    session = FakeSession(handler)
    meetings = ReadClient("tok", session=session).list_meetings()
    assert len(meetings) == 3
    assert len(session.calls) == 3


def test_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr("readbox.read_client.time.sleep", lambda s: None)

    def handler(method, path, params):
        return FakeResponse(status_code=503, text="down")

    session = FakeSession(handler)
    client = ReadClient("tok", session=session, max_retries=2)
    with pytest.raises(ReadAPIError) as exc:
        client.list_meetings()
    assert exc.value.status_code == 503
    assert len(session.calls) == 3  # initial + 2 retries


def test_non_retryable_status_raises():
    def handler(method, path, params):
        return FakeResponse(
            status_code=404, json_body={"error": "meeting not found"}
        )

    client = ReadClient("tok", session=FakeSession(handler))
    with pytest.raises(ReadAPIError) as exc:
        client.get_meeting("missing")
    assert exc.value.status_code == 404
    assert "not found" in exc.value.message
