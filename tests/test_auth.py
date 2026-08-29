"""Offline tests for auth: token storage, Read refresh/rotation, Dropbox flow.

No network and no browser: a FakeSession stands in for requests.Session and a
fake OAuth flow stands in for the Dropbox SDK, so registration, code exchange,
refresh-token rotation, expiry logic, and credential persistence are all exercised
deterministically.
"""

from __future__ import annotations

import json
import stat
import types

import pytest

from readbox import auth
from readbox.auth import AuthError, TokenStore


# -- fakes --------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text="", reason="Reason"):
        self.status_code = status_code
        self._json = json_body
        self.text = text
        self.reason = reason
        self.headers: dict = {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Records .post(...) calls and returns queued responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, data=None, json=None, auth=None, timeout=None):
        self.calls.append(
            {"url": url, "data": data, "json": json, "auth": auth}
        )
        return self._responses.pop(0)


def store_at(tmp_path) -> TokenStore:
    return TokenStore(path=tmp_path / "tokens.json")


# -- TokenStore ---------------------------------------------------------------


def test_store_roundtrip_and_merges_providers(tmp_path):
    store = store_at(tmp_path)
    store.set("read", {"client_id": "cid", "refresh_token": "r1"})
    store.set("dropbox", {"app_key": "ak", "refresh_token": "d1"})

    assert store.get("read")["client_id"] == "cid"
    assert store.get("dropbox")["refresh_token"] == "d1"
    # Both providers coexist in one file.
    on_disk = json.loads((tmp_path / "tokens.json").read_text())
    assert set(on_disk) == {"read", "dropbox"}


def test_store_file_is_owner_only(tmp_path):
    store = store_at(tmp_path)
    store.set("read", {"x": 1})
    mode = stat.S_IMODE((tmp_path / "tokens.json").stat().st_mode)
    assert mode == 0o600


def test_store_missing_file_returns_empty(tmp_path):
    assert store_at(tmp_path).get("read") == {}


def test_store_clear_removes_provider(tmp_path):
    store = store_at(tmp_path)
    store.set("read", {"x": 1})
    store.set("dropbox", {"y": 2})
    store.clear("read")
    assert store.get("read") == {}
    assert store.get("dropbox") == {"y": 2}


def test_store_corrupt_file_raises(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text("{not json")
    with pytest.raises(AuthError):
        store_at(tmp_path).get("read")


# -- Read: registration -------------------------------------------------------


def test_register_read_client_returns_credentials():
    session = FakeSession(
        [FakeResponse(json_body={"client_id": "cid", "client_secret": "sec"})]
    )
    creds = auth.register_read_client("readbox", session=session)
    assert creds == {"client_id": "cid", "client_secret": "sec"}
    body = session.calls[0]["json"]
    assert session.calls[0]["url"] == auth.READ_REGISTER_URL
    assert body["client_name"] == "readbox"
    # redirect_uris is required for the authorization_code grant to be enabled.
    assert "authorization_code" in body["grant_types"]
    assert body["redirect_uris"] == list(auth.READ_REDIRECT_URIS)
    assert auth.READ_AUTHORIZE_URL in body["redirect_uris"]


def test_register_read_client_missing_secret_raises():
    session = FakeSession([FakeResponse(json_body={"client_id": "cid"})])
    with pytest.raises(AuthError):
        auth.register_read_client(session=session)


# -- Read: code exchange ------------------------------------------------------


def test_exchange_code_uses_basic_auth_and_sets_expiry():
    creds = {"client_id": "cid", "client_secret": "sec"}
    session = FakeSession(
        [
            FakeResponse(
                json_body={
                    "access_token": "at",
                    "refresh_token": "rt",
                    "expires_in": 600,
                }
            )
        ]
    )
    result = auth.exchange_read_code(
        creds, "the-code", redirect_uri="urn:x", session=session
    )

    call = session.calls[0]
    assert call["url"] == auth.READ_TOKEN_URL
    assert call["auth"] == ("cid", "sec")  # client_secret_basic
    assert call["data"]["grant_type"] == "authorization_code"
    assert call["data"]["code"] == "the-code"
    assert call["data"]["redirect_uri"] == "urn:x"

    assert result["access_token"] == "at"
    assert result["refresh_token"] == "rt"
    assert result["expires_at"] > 0
    # Client credentials are preserved through the merge.
    assert result["client_id"] == "cid"


# -- Read: refresh + rotation -------------------------------------------------


def test_refresh_rotates_and_persists_refresh_token(tmp_path):
    store = store_at(tmp_path)
    store.set(
        "read",
        {
            "client_id": "cid",
            "client_secret": "sec",
            "access_token": "old-at",
            "refresh_token": "old-rt",
            "expires_at": 0,
        },
    )
    session = FakeSession(
        [
            FakeResponse(
                json_body={
                    "access_token": "new-at",
                    "refresh_token": "new-rt",
                    "expires_in": 600,
                }
            )
        ]
    )

    updated = auth.refresh_read_token(store, session=session)

    call = session.calls[0]
    assert call["auth"] == ("cid", "sec")
    assert call["data"] == {"grant_type": "refresh_token", "refresh_token": "old-rt"}
    # New tokens returned and durably written (single-use rotation).
    assert updated["access_token"] == "new-at"
    assert updated["refresh_token"] == "new-rt"
    assert store.get("read")["refresh_token"] == "new-rt"


def test_refresh_keeps_old_refresh_token_if_omitted(tmp_path):
    store = store_at(tmp_path)
    store.set(
        "read",
        {"client_id": "c", "client_secret": "s", "refresh_token": "keep"},
    )
    session = FakeSession([FakeResponse(json_body={"access_token": "at"})])
    updated = auth.refresh_read_token(store, session=session)
    assert updated["refresh_token"] == "keep"


def test_refresh_without_stored_token_raises(tmp_path):
    store = store_at(tmp_path)
    store.set("read", {"client_id": "c", "client_secret": "s"})
    with pytest.raises(AuthError):
        auth.refresh_read_token(store, session=FakeSession([]))


def test_refresh_error_response_raises(tmp_path):
    store = store_at(tmp_path)
    store.set(
        "read", {"client_id": "c", "client_secret": "s", "refresh_token": "r"}
    )
    session = FakeSession(
        [FakeResponse(status_code=400, json_body={"error": "invalid_grant"})]
    )
    with pytest.raises(AuthError) as exc:
        auth.refresh_read_token(store, session=session)
    assert "invalid_grant" in str(exc.value)


# -- Read: valid-token accessor ----------------------------------------------


def test_access_token_returns_cached_when_fresh(tmp_path):
    store = store_at(tmp_path)
    store.set(
        "read",
        {"access_token": "cached", "refresh_token": "r", "expires_at": 1000},
    )
    # A session with no queued responses would blow up if a refresh were attempted.
    token = auth.read_access_token(store, session=FakeSession([]), now=500)
    assert token == "cached"


def test_access_token_refreshes_when_expired(tmp_path):
    store = store_at(tmp_path)
    store.set(
        "read",
        {
            "client_id": "c",
            "client_secret": "s",
            "access_token": "stale",
            "refresh_token": "r",
            "expires_at": 100,
        },
    )
    session = FakeSession([FakeResponse(json_body={"access_token": "fresh"})])
    token = auth.read_access_token(store, session=session, now=200)
    assert token == "fresh"


def test_access_token_without_creds_raises(tmp_path):
    with pytest.raises(AuthError):
        auth.read_access_token(store_at(tmp_path))


# -- Dropbox: app key + PKCE console flow -------------------------------------


def test_dropbox_app_key_from_env(monkeypatch):
    monkeypatch.setenv("READBOX_DROPBOX_APP_KEY", "envkey")
    assert auth.dropbox_app_key() == "envkey"
    assert auth.dropbox_app_key("explicit") == "explicit"


def test_dropbox_app_key_missing_raises(monkeypatch):
    monkeypatch.delenv("READBOX_DROPBOX_APP_KEY", raising=False)
    with pytest.raises(AuthError):
        auth.dropbox_app_key()


class FakeFlow:
    def __init__(self, refresh_token="dbx-refresh"):
        self._refresh_token = refresh_token
        self.finished_with = None

    def start(self):
        return "https://dropbox/authorize?x=1"

    def finish(self, code):
        self.finished_with = code
        return types.SimpleNamespace(refresh_token=self._refresh_token)


def test_authorize_dropbox_persists_refresh_token(tmp_path):
    store = store_at(tmp_path)
    flow = FakeFlow(refresh_token="dbx-refresh")
    printed: list[str] = []

    creds = auth.authorize_dropbox(
        store,
        app_key="ak",
        flow=flow,
        prompt=lambda _: "  paste-code  ",
        printer=printed.append,
    )

    assert flow.finished_with == "paste-code"  # stripped
    assert creds == {"app_key": "ak", "refresh_token": "dbx-refresh"}
    assert store.get("dropbox")["refresh_token"] == "dbx-refresh"
    assert any("https://dropbox/authorize" in line for line in printed)


def test_authorize_dropbox_no_refresh_token_raises(tmp_path):
    store = store_at(tmp_path)
    flow = FakeFlow(refresh_token=None)
    with pytest.raises(AuthError):
        auth.authorize_dropbox(
            store, app_key="ak", flow=flow, prompt=lambda _: "code",
            printer=lambda _: None,
        )


def test_authorize_dropbox_empty_code_raises(tmp_path):
    store = store_at(tmp_path)
    with pytest.raises(AuthError):
        auth.authorize_dropbox(
            store, app_key="ak", flow=FakeFlow(), prompt=lambda _: "   ",
            printer=lambda _: None,
        )
