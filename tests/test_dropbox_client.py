"""Offline tests for dropbox_client, driven by a fake Dropbox SDK object.

No network: FakeDbx records files_upload / files_create_folder_v2 calls and can be
told to raise the SDK's real ApiError, so path normalization, overwrite mode,
folder-conflict tolerance, and error wrapping are all exercised deterministically.
"""

from __future__ import annotations

import types

import pytest
from dropbox.exceptions import ApiError
from dropbox.files import WriteMode

from readbox.auth import AuthError, TokenStore
from readbox.dropbox_client import (
    DropboxClient,
    DropboxError,
    _is_folder_conflict,
    _normalize_path,
)


class FakeDbx:
    def __init__(self, *, upload_error=None, folder_error=None):
        self.uploads: list[dict] = []
        self.folders: list[str] = []
        self._upload_error = upload_error
        self._folder_error = folder_error

    def files_upload(self, f, path, mode=None, mute=None, **kw):
        if self._upload_error is not None:
            raise self._upload_error
        self.uploads.append({"data": f, "path": path, "mode": mode, "mute": mute})

    def files_create_folder_v2(self, path):
        if self._folder_error is not None:
            raise self._folder_error
        self.folders.append(path)


def api_error(err="boom") -> ApiError:
    return ApiError("req-id", err, None, None)


# -- path normalization -------------------------------------------------------


def test_normalize_path_adds_leading_slash_and_forward_slashes():
    assert _normalize_path("Read AI/2026/08/x.md") == "/Read AI/2026/08/x.md"
    assert _normalize_path("\\Read AI\\x.md") == "/Read AI/x.md"
    assert _normalize_path("/already/ok") == "/already/ok"
    assert _normalize_path("/trailing/") == "/trailing"
    assert _normalize_path("/") == "/"


# -- upload -------------------------------------------------------------------


def test_upload_uses_overwrite_mode_and_normalized_path():
    dbx = FakeDbx()
    DropboxClient(dbx).upload(b"hello", "Read AI/x.md")
    call = dbx.uploads[0]
    assert call["data"] == b"hello"
    assert call["path"] == "/Read AI/x.md"
    assert call["mode"] == WriteMode.overwrite
    assert call["mute"] is True


def test_upload_add_mode_when_not_overwriting():
    dbx = FakeDbx()
    DropboxClient(dbx).upload(b"x", "/p", overwrite=False)
    assert dbx.uploads[0]["mode"] == WriteMode.add


def test_upload_wraps_api_error():
    dbx = FakeDbx(upload_error=api_error())
    with pytest.raises(DropboxError):
        DropboxClient(dbx).upload(b"x", "/p")


# -- ensure_folder ------------------------------------------------------------


def test_ensure_folder_creates_path():
    dbx = FakeDbx()
    DropboxClient(dbx).ensure_folder("Read AI")
    assert dbx.folders == ["/Read AI"]


def test_ensure_folder_ignores_conflict():
    # A fake error object shaped like Dropbox's CreateFolderError path-conflict.
    conflict = types.SimpleNamespace(
        is_path=lambda: True,
        get_path=lambda: types.SimpleNamespace(is_conflict=lambda: True),
    )
    dbx = FakeDbx(folder_error=api_error(conflict))
    # Should not raise.
    DropboxClient(dbx).ensure_folder("/Read AI")


def test_ensure_folder_raises_on_other_error():
    dbx = FakeDbx(folder_error=api_error("other"))
    with pytest.raises(DropboxError):
        DropboxClient(dbx).ensure_folder("/Read AI")


def test_is_folder_conflict_false_for_plain_error():
    assert _is_folder_conflict(api_error("plain")) is False


# -- from_store ---------------------------------------------------------------


def test_from_store_builds_client_with_stored_credentials(tmp_path):
    store = TokenStore(path=tmp_path / "tokens.json")
    store.set("dropbox", {"app_key": "ak", "refresh_token": "rt"})

    built: dict = {}

    class FakeModule:
        @staticmethod
        def Dropbox(oauth2_refresh_token=None, app_key=None):
            built["refresh_token"] = oauth2_refresh_token
            built["app_key"] = app_key
            return FakeDbx()

    client = DropboxClient.from_store(store, dropbox_module=FakeModule)
    assert isinstance(client, DropboxClient)
    assert built == {"refresh_token": "rt", "app_key": "ak"}


def test_from_store_without_credentials_raises(tmp_path):
    store = TokenStore(path=tmp_path / "tokens.json")
    with pytest.raises(AuthError):
        DropboxClient.from_store(store, dropbox_module=object())
