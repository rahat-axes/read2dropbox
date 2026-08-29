"""Dropbox API client — upload files and ensure folders exist.

Phase 4 of the build plan. A thin wrapper over the official ``dropbox`` SDK so the
sync engine (Phase 5) doesn't touch SDK details. Construction is injectable: pass a
ready ``dropbox.Dropbox`` (or any object with the same surface) for offline tests,
or build one from the stored refresh token via :meth:`from_store`.

The refresh token + App key are enough for the SDK to mint short-lived access
tokens itself, so nothing here deals with token expiry — that lives in ``auth.py``.
"""

from __future__ import annotations

from typing import Any, Optional

from .auth import AuthError, TokenStore, dropbox_app_key


class DropboxError(Exception):
    """A failed Dropbox operation (upload, folder creation, or auth)."""


class DropboxClient:
    """Upload Markdown/JSON exports into a user's Dropbox."""

    def __init__(self, dbx: Any) -> None:
        self._dbx = dbx

    @classmethod
    def from_store(
        cls,
        store: Optional[TokenStore] = None,
        *,
        dropbox_module: Any = None,
    ) -> "DropboxClient":
        """Build a client from the refresh token stored by ``readbox auth``."""
        store = store or TokenStore()
        creds = store.get("dropbox")
        refresh_token = creds.get("refresh_token")
        if not refresh_token:
            raise AuthError(
                "not authenticated with Dropbox — run `readbox auth` first"
            )
        app_key = creds.get("app_key") or dropbox_app_key()

        if dropbox_module is None:
            import dropbox as dropbox_module

        dbx = dropbox_module.Dropbox(
            oauth2_refresh_token=refresh_token,
            app_key=app_key,
        )
        return cls(dbx)

    # -- operations --------------------------------------------------------

    def upload(self, data: bytes, dropbox_path: str, *, overwrite: bool = True) -> None:
        """Upload ``data`` to ``dropbox_path`` (e.g. ``/Read AI/2026/08/x.md``).

        Idempotent by default: ``overwrite`` mode replaces any existing file at the
        path, so re-running a sync doesn't create ``file (1).md`` duplicates.
        Dropbox creates parent folders implicitly, so no pre-check is needed.
        """
        from dropbox.files import WriteMode
        from dropbox.exceptions import ApiError

        path = _normalize_path(dropbox_path)
        mode = WriteMode.overwrite if overwrite else WriteMode.add
        try:
            self._dbx.files_upload(data, path, mode=mode, mute=True)
        except ApiError as exc:
            raise DropboxError(f"upload to {path} failed: {exc}") from exc

    def ensure_folder(self, dropbox_path: str) -> None:
        """Create ``dropbox_path`` if absent (a no-op if it already exists).

        Uploads auto-create parents, so this is mainly for making an empty root
        folder. An existing-path conflict is treated as success.
        """
        from dropbox.exceptions import ApiError

        path = _normalize_path(dropbox_path)
        try:
            self._dbx.files_create_folder_v2(path)
        except ApiError as exc:
            if _is_folder_conflict(exc):
                return
            raise DropboxError(f"create folder {path} failed: {exc}") from exc


def _normalize_path(dropbox_path: str) -> str:
    """Dropbox paths must be absolute (leading ``/``) and use forward slashes."""
    path = dropbox_path.replace("\\", "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") if path != "/" else path


def _is_folder_conflict(exc: Any) -> bool:
    """True if an ApiError is 'folder already exists' (safe to ignore)."""
    err = getattr(exc, "error", None)
    try:
        return bool(err.is_path() and err.get_path().is_conflict())
    except AttributeError:
        return False
