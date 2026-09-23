"""OAuth token storage and refresh for Read AI and Dropbox.

Phase 4 of the build plan. Two providers with two different security models
(see ``claude/plan.md`` §2c and Phase 1 verification):

* **Dropbox** is genuinely secret-free. It uses PKCE with a ship-safe *App key*
  (not a secret). The official ``dropbox`` SDK drives the flow and auto-refreshes
  the access token from a stored ``refresh_token``.
* **Read** is *not* secret-free. Its OAuth 2.1 dynamic client registration mints a
  per-user ``client_secret`` (auth method ``client_secret_basic``) that is required
  on every refresh. So each user's install registers *its own* client at ``auth``
  time and stores ``client_id`` + ``client_secret`` locally — nothing secret ships
  in the package. Read's refresh tokens are **single-use and rotate on every
  refresh**, so we persist the new one immediately and discard the old.

Everything sensitive lands in ``config.tokens_path()`` (``tokens.json``) with
owner-only permissions. The interactive/browser and network pieces are injectable
so the storage + refresh logic can be exercised offline (see the tests).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from . import config

# -- Read AI OAuth endpoints (verified by hand, Aug 2026 — see plan Phase 1) ----

READ_REGISTER_URL = "https://api.read.ai/oauth/register"
READ_AUTHORIZE_URL = "https://api.read.ai/oauth/ui"
READ_TOKEN_URL = "https://authn.read.ai/oauth2/token"
READ_SCOPE = "openid email offline_access profile meeting:read mcp:execute"

# Registration MUST include a redirect_uri to get the ``authorization_code`` grant
# enabled — omitting it makes Read register a refresh_token-only client (verified
# 2026-08-29: a body without redirect_uris 500s, or silently drops auth-code).
# The oauth/ui console flow appears to authorize against its own URL as the
# redirect target, so we register that first (localhost kept as a fallback). The
# exchange must echo back whichever redirect_uri the flow actually used.
READ_REDIRECT_URIS = ["https://api.read.ai/oauth/ui", "http://localhost"]

# Access tokens live ~10 minutes; refresh this many seconds early to avoid using
# a token that expires mid-request.
_EXPIRY_SKEW_SECONDS = 60
_DEFAULT_EXPIRES_IN = 600  # 10 minutes, per Read's documented lifetime.

# Dropbox scopes the tool needs (offline access → a refresh token).
DROPBOX_SCOPES = [
    "files.content.write",
    "files.metadata.read",
    "account_info.read",
]


class AuthError(Exception):
    """Authentication / token error (registration, exchange, or refresh)."""


# -- token storage ------------------------------------------------------------


class TokenStore:
    """Owner-only JSON store for per-provider OAuth credentials.

    Layout of ``tokens.json``::

        {
          "read":    {"client_id", "client_secret", "access_token",
                      "refresh_token", "expires_at"},
          "dropbox": {"app_key", "refresh_token"}
        }
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = path or config.tokens_path()

    def _read_all(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text())
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise AuthError(f"corrupt tokens file {self.path}: {exc}") from exc

    def _write_all(self, data: dict[str, Any]) -> None:
        # Write via a private temp file, then atomically replace, so the tokens
        # file is never left half-written and never briefly world-readable.
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def get(self, provider: str) -> dict[str, Any]:
        """Return the stored credentials for ``provider`` (empty dict if none)."""
        return dict(self._read_all().get(provider, {}))

    def set(self, provider: str, creds: dict[str, Any]) -> None:
        """Persist ``creds`` for ``provider``, merging with the rest of the file."""
        data = self._read_all()
        data[provider] = creds
        self._write_all(data)

    def clear(self, provider: str) -> None:
        """Remove a provider's stored credentials (used by ``readbox auth --reset``)."""
        data = self._read_all()
        if data.pop(provider, None) is not None:
            self._write_all(data)


# -- Read AI: dynamic registration + auth-code exchange + refresh --------------


def register_read_client(
    client_name: str = "readbox",
    *,
    session: Optional[requests.Session] = None,
) -> dict[str, str]:
    """Register a per-user OAuth client with Read; returns client_id/client_secret.

    Per Phase 1, only ``client_name`` is ours to choose — the rest of the RFC 7591
    body is fixed boilerplate. The returned ``client_secret`` cannot be retrieved
    again, so the caller must persist it immediately.
    """
    session = session or requests.Session()
    body = {
        "client_name": client_name,
        # redirect_uris is required for the authorization_code grant to be enabled
        # (see READ_REDIRECT_URIS) — without it Read registers a refresh-only client.
        "redirect_uris": list(READ_REDIRECT_URIS),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
        "scope": READ_SCOPE,
    }
    resp = session.post(READ_REGISTER_URL, json=body, timeout=30)
    data = _json_or_error(resp, "client registration")
    client_id = data.get("client_id")
    client_secret = data.get("client_secret")
    if not client_id or not client_secret:
        # Report only the keys returned, never their values — the body may carry
        # a client_secret we must not leak into logs or stderr.
        raise AuthError(
            f"registration response missing credentials (got keys: "
            f"{sorted(data)})"
        )
    return {"client_id": client_id, "client_secret": client_secret}


def exchange_read_code(
    creds: dict[str, str],
    code: str,
    *,
    redirect_uri: Optional[str] = None,
    code_verifier: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """Exchange an authorization code for Read access + refresh tokens.

    Uses ``client_secret_basic`` (HTTP Basic ``client_id:client_secret``), the auth
    method Read's dynamic registration assigns. Returns the credential dict to store.
    """
    session = session or requests.Session()
    form: dict[str, str] = {"grant_type": "authorization_code", "code": code}
    if redirect_uri:
        form["redirect_uri"] = redirect_uri
    if code_verifier:
        form["code_verifier"] = code_verifier

    resp = session.post(
        READ_TOKEN_URL,
        data=form,
        auth=(creds["client_id"], creds["client_secret"]),
        timeout=30,
    )
    data = _json_or_error(resp, "token exchange")
    return _merge_token_response(creds, data)


def refresh_read_token(
    store: TokenStore,
    *,
    session: Optional[requests.Session] = None,
) -> dict[str, Any]:
    """Refresh Read's access token, rotating and persisting the refresh token.

    Read's refresh tokens are single-use: the response carries a *new* one that
    replaces the old. We persist the whole updated credential set before returning.
    """
    session = session or requests.Session()
    creds = store.get("read")
    if not creds.get("refresh_token"):
        raise AuthError("no Read refresh token stored — run `readbox auth` first")

    resp = session.post(
        READ_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
        },
        auth=(creds["client_id"], creds["client_secret"]),
        timeout=30,
    )
    data = _json_or_error(resp, "token refresh")
    updated = _merge_token_response(creds, data)
    store.set("read", updated)
    return updated


def read_access_token(
    store: TokenStore,
    *,
    session: Optional[requests.Session] = None,
    now: Optional[float] = None,
) -> str:
    """Return a currently-valid Read access token, refreshing if it has expired."""
    creds = store.get("read")
    if not creds:
        raise AuthError("not authenticated with Read — run `readbox auth` first")

    now = time.time() if now is None else now
    expires_at = creds.get("expires_at", 0)
    if creds.get("access_token") and now < expires_at:
        return creds["access_token"]

    return refresh_read_token(store, session=session)["access_token"]


def _merge_token_response(
    creds: dict[str, Any], data: dict[str, Any]
) -> dict[str, Any]:
    """Fold an OAuth token response into the stored credential dict."""
    access_token = data.get("access_token")
    if not access_token:
        # Report only the keys returned, never their values — the body may carry
        # a rotated refresh_token we must not leak into logs or stderr.
        raise AuthError(
            f"token response missing access_token (got keys: {sorted(data)})"
        )

    updated = dict(creds)
    updated["access_token"] = access_token
    # Refresh tokens rotate — keep the new one, fall back to the existing one only
    # if the response omits it.
    if data.get("refresh_token"):
        updated["refresh_token"] = data["refresh_token"]
    expires_in = data.get("expires_in", _DEFAULT_EXPIRES_IN)
    updated["expires_at"] = time.time() + float(expires_in) - _EXPIRY_SKEW_SECONDS
    return updated


# -- Dropbox: PKCE console flow (official SDK) ---------------------------------


def dropbox_app_key(explicit: Optional[str] = None) -> str:
    """Resolve the Dropbox App key (arg → env). Not a secret; safe to ship."""
    app_key = explicit or os.environ.get("READBOX_DROPBOX_APP_KEY")
    if not app_key:
        raise AuthError(
            "no Dropbox App key — set READBOX_DROPBOX_APP_KEY or pass --app-key"
        )
    return app_key


def authorize_dropbox(
    store: TokenStore,
    *,
    app_key: Optional[str] = None,
    flow: Any = None,
    prompt: Callable[[str], str] = input,
    printer: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run Dropbox's PKCE console flow and persist the resulting refresh token.

    ``flow`` (a ``DropboxOAuth2FlowNoRedirect``) is injectable for testing; by
    default one is built from the App key. Offline access yields a refresh token,
    which is all we store — the SDK mints short-lived access tokens from it.
    """
    key = dropbox_app_key(app_key)
    if flow is None:
        from dropbox import DropboxOAuth2FlowNoRedirect

        flow = DropboxOAuth2FlowNoRedirect(
            key,
            use_pkce=True,
            token_access_type="offline",
            scope=DROPBOX_SCOPES,
        )

    authorize_url = flow.start()
    printer(
        "1. Open this URL in your browser and click Allow:\n"
        f"\n   {authorize_url}\n\n"
        "2. Copy the authorization code Dropbox shows you."
    )
    code = prompt("Paste the authorization code here: ").strip()
    if not code:
        raise AuthError("no authorization code entered")

    try:
        result = flow.finish(code)
    except Exception as exc:  # SDK raises provider-specific errors
        raise AuthError(f"Dropbox authorization failed: {exc}") from exc

    refresh_token = getattr(result, "refresh_token", None)
    if not refresh_token:
        raise AuthError(
            "Dropbox did not return a refresh token — ensure the app requests "
            "offline access (token_access_type='offline')"
        )

    creds = {"app_key": key, "refresh_token": refresh_token}
    store.set("dropbox", creds)
    return creds


# -- shared HTTP helpers ------------------------------------------------------


def _json_or_error(resp: requests.Response, what: str) -> dict[str, Any]:
    """Parse a token-endpoint JSON body, raising AuthError on any failure."""
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code >= 400:
        detail = ""
        if isinstance(data, dict):
            detail = data.get("error_description") or data.get("error") or ""
        detail = detail or (resp.text or "").strip()[:200] or resp.reason
        raise AuthError(f"{what} failed ({resp.status_code}): {detail}")
    if not isinstance(data, dict):
        raise AuthError(f"{what} returned a non-JSON body")
    return data
