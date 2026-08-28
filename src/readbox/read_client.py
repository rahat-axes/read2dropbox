"""Read AI REST API client — list and fetch meetings.

Phase 2 of the build plan: clean, testable functions over the Read AI API. Auth
is intentionally *not* handled here — the caller passes in an access token
(real OAuth + refresh arrives in Phase 4). Everything is built against the saved
Phase 1 fixtures so it can be exercised offline.

API facts (verified against Read's API Reference, Aug 2026):

* Base URL: ``https://api.read.ai``; ``Authorization: Bearer <token>``.
* ``GET /v1/meetings`` returns a list envelope
  ``{object, url, has_more, next, data[]}`` in reverse-chronological order.
  Pagination is cursor-based: pass ``?cursor=<token>`` to get the next page. The
  ``next`` field holds that token; per the docs it equals the id of the last
  meeting on the page, so we fall back to that if ``next`` is absent.
* ``GET /v1/meetings/{id}`` returns the meeting. Rich fields only come back when
  requested via repeated ``expand[]=<field>`` query params — the list endpoint
  omits them.
* Access tokens expire ~10 minutes after issue (handled by refresh in Phase 4).
"""

from __future__ import annotations

import time
from typing import Any, Iterator, Optional, Sequence

import requests

BASE_URL = "https://api.read.ai"

#: Expandable fields on GET /v1/meetings/{id}. Requesting all of them yields the
#: full report the formatter (Phase 3) renders.
DEFAULT_EXPAND: tuple[str, ...] = (
    "summary",
    "action_items",
    "key_questions",
    "topics",
    "chapter_summaries",
    "transcript",
    "metrics",
    "recording_download",
)

#: HTTP statuses worth retrying: rate limiting + transient server errors.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class ReadAPIError(Exception):
    """A non-retryable error response from the Read AI API."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Read API error {status_code}: {message}")
        self.status_code = status_code
        self.message = message


class ReadClient:
    """Thin, session-backed client for the Read AI API.

    The ``requests.Session`` is injectable so tests can supply a fake transport
    and no network is touched.
    """

    def __init__(
        self,
        token: str,
        *,
        session: Optional[requests.Session] = None,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 5,
        backoff_base: float = 0.5,
        backoff_cap: float = 30.0,
    ) -> None:
        self.token = token
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap

    # -- internals ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    def _backoff_seconds(self, attempt: int, retry_after: Optional[str]) -> float:
        """Honor a Retry-After header when present, else exponential backoff."""
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass  # Non-numeric (HTTP-date) Retry-After: fall through.
        return min(self.backoff_base * (2 ** attempt), self.backoff_cap)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self._backoff_seconds(attempt, None))
                    continue
                raise ReadAPIError(0, f"request failed: {exc}") from exc

            if resp.status_code in RETRYABLE_STATUS and attempt < self.max_retries:
                time.sleep(
                    self._backoff_seconds(attempt, resp.headers.get("Retry-After"))
                )
                continue

            if resp.status_code >= 400:
                raise ReadAPIError(resp.status_code, _error_message(resp))

            return resp.json()

        # Exhausted retries on a retryable status/exception.
        if last_exc is not None:
            raise ReadAPIError(0, f"request failed: {last_exc}") from last_exc
        raise ReadAPIError(
            resp.status_code, _error_message(resp)  # type: ignore[possibly-undefined]
        )

    # -- public API --------------------------------------------------------

    def iter_meetings(
        self,
        *,
        start_time_ms: Optional[int] = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield every meeting across all pages, following the cursor."""
        base_params: dict[str, Any] = {}
        if start_time_ms is not None:
            base_params["start_time_ms"] = start_time_ms

        cursor: Optional[str] = None
        while True:
            params = dict(base_params)
            if cursor:
                params["cursor"] = cursor
            payload = self._request("GET", "/v1/meetings", params=params or None)

            data = payload.get("data", [])
            yield from data

            if not payload.get("has_more"):
                break
            cursor = payload.get("next") or (data[-1]["id"] if data else None)
            if not cursor:
                break

    def list_meetings(
        self,
        *,
        start_time_ms: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Return all meetings across all pages as a list."""
        return list(self.iter_meetings(start_time_ms=start_time_ms))

    def get_meeting(
        self,
        meeting_id: str,
        *,
        expand: Optional[Sequence[str]] = DEFAULT_EXPAND,
    ) -> dict[str, Any]:
        """Fetch one meeting, expanding rich fields by default."""
        params = {"expand[]": list(expand)} if expand else None
        return self._request("GET", f"/v1/meetings/{meeting_id}", params=params)


def _error_message(resp: requests.Response) -> str:
    """Best-effort human-readable message from an error response."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:200] or resp.reason or "unknown error"
    if isinstance(body, dict):
        for key in ("error_description", "error", "message", "detail"):
            val = body.get(key)
            if isinstance(val, str) and val:
                return val
    return str(body)[:200]


# -- module-level convenience wrappers (the plain-function interface) --------


def list_meetings(
    token: str,
    *,
    start_time_ms: Optional[int] = None,
    session: Optional[requests.Session] = None,
    base_url: str = BASE_URL,
) -> list[dict[str, Any]]:
    """List all meetings for ``token`` (handles pagination)."""
    return ReadClient(token, session=session, base_url=base_url).list_meetings(
        start_time_ms=start_time_ms
    )


def get_meeting(
    token: str,
    meeting_id: str,
    *,
    expand: Optional[Sequence[str]] = DEFAULT_EXPAND,
    session: Optional[requests.Session] = None,
    base_url: str = BASE_URL,
) -> dict[str, Any]:
    """Fetch one full meeting report for ``token``."""
    return ReadClient(token, session=session, base_url=base_url).get_meeting(
        meeting_id, expand=expand
    )
