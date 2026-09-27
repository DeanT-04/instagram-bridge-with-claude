"""Instagram web API client: same-origin ``fetch`` executed inside the logged-in page.

Running the request in the page means the browser attaches the session cookies itself;
Heliograph never reads or stores them (only the ``csrftoken`` is echoed as a header,
from inside the page). Every call is an eye span and passes a client-side rate limiter.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Protocol
from urllib.parse import unquote, urlencode

from heliograph.drivers.cdp.ratelimit import RateLimiter, limiter_for
from heliograph.errors import HeliographError, NotLoggedInError, RateLimitedError
from heliograph.eye import span

__all__ = ["IG_APP_ID", "Evaluator", "InstagramApiError", "WebApiClient", "build_url"]

IG_APP_ID = "936619743392459"
ASBD_ID = "129477"
_ALLOWED_PREFIXES = ("/api/v1/", "/graphql/", "/api/graphql")
# Control chars/whitespace, backslash, fragment, and percent-encoded "/", "\" or NUL.
_BAD_PATH_CHARS = re.compile(r"[\x00-\x20\x7f\\#]|%(?:2f|5c|00)", re.IGNORECASE)
IG_ORIGIN = "https://www.instagram.com"
_LOGIN_MARKERS = ("login_required", "/accounts/login", "checkpoint_required", "not-logged-in")
_RATE_MARKERS = ("please wait a few minutes", "feedback_required", "rate_limit_error", "spam")

FETCH_JS = """async ([path, method, body, appId, asbd]) => {
  if (location.origin !== 'https://www.instagram.com') {
    return {status: 0, wrongOrigin: location.origin, url: '', body: ''};
  }
  const csrf = (document.cookie.match(/(?:^|; )csrftoken=([^;]+)/) || [])[1] || '';
  const headers = {'x-ig-app-id': appId, 'x-csrftoken': csrf,
                   'x-requested-with': 'XMLHttpRequest', 'x-asbd-id': asbd};
  const init = {method, headers, credentials: 'include'};
  if (method === 'POST') {
    headers['content-type'] = 'application/x-www-form-urlencoded';
    init.body = body;
  }
  const r = await fetch(path, init);
  return {status: r.status, url: r.url, redirected: r.redirected,
          retryAfter: r.headers.get('retry-after'), body: await r.text()};
}"""


class Evaluator(Protocol):
    """Anything with Playwright's ``Page.evaluate`` signature."""

    async def evaluate(self, expression: str, arg: Any = None) -> Any: ...


class InstagramApiError(HeliographError):
    """Instagram answered with an unexpected status or a non-JSON body.

    Attributes:
        status: HTTP status code (0 if unknown).
        path: API path requested.
    """

    def __init__(self, message: str, *, status: int = 0, path: str = "",
                 hint: str | None = None) -> None:
        super().__init__(message, hint=hint)
        self.status = status
        self.path = path


def _check_path(path: str) -> None:
    """Refuse anything that could leave the API prefixes once the browser resolves it.

    The browser's URL parser strips tabs/newlines, treats ``\\`` as ``/`` and resolves
    ``..``/``%2e%2e`` dot-segments, so ``/api/v1/../../accounts/...`` would otherwise escape
    the allow-list (still same-origin, but outside the API surface).
    """
    if (
        not isinstance(path, str)
        or not path.startswith(_ALLOWED_PREFIXES)
        or not path.isascii()
        or "//" in path
        or _BAD_PATH_CHARS.search(path)
    ):
        raise ValueError(f"Refusing non-API path {path!r}")
    segments = unquote(path.split("?", 1)[0]).split("/")
    if any(seg in (".", "..") for seg in segments):
        raise ValueError(f"Refusing non-API path {path!r}")


def build_url(path: str, params: dict[str, Any] | None = None) -> str:
    """Validate an API path and append URL-encoded ``params`` (None values dropped)."""
    _check_path(path)
    clean = {k: _scalar(v) for k, v in (params or {}).items() if v is not None}
    if not clean:
        return path
    return f"{path}{'&' if '?' in path else '?'}{urlencode(clean)}"


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


class WebApiClient:
    """GET/POST against ``https://www.instagram.com`` from inside the page.

    Args:
        page: The Instagram page (or any :class:`Evaluator`).
        account: Account key used to pick the shared rate limiters.
        read_limiter/write_limiter: Override the settings-derived limiters (tests).
    """

    def __init__(
        self,
        page: Evaluator,
        *,
        account: str = "default",
        read_limiter: RateLimiter | None = None,
        write_limiter: RateLimiter | None = None,
    ) -> None:
        self.page = page
        self.read_limiter = read_limiter or limiter_for("read", account)
        self.write_limiter = write_limiter or limiter_for("write", account)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET ``path`` (a read); returns the decoded JSON object."""
        return await self.request("GET", path, params=params)

    async def post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        *,
        params: dict[str, Any] | None = None,
        write: bool = True,
    ) -> dict[str, Any]:
        """POST form-encoded ``data``. ``write=False`` for POST endpoints that only read."""
        return await self.request("POST", path, params=params, data=data, write=write)

    async def request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        write: bool = False,
    ) -> dict[str, Any]:
        """Perform one request and map failures to Heliograph errors.

        Raises:
            NotLoggedInError: 401, login redirect, or ``login_required``.
            RateLimitedError: 429, "Please wait a few minutes", ``feedback_required``.
            InstagramApiError: any other non-2xx status or non-JSON body.
        """
        url = build_url(path, params)
        body = urlencode({k: _scalar(v) for k, v in (data or {}).items() if v is not None})
        kind = "write" if write else "read"
        async with span("cdp.api", method=method, path=url, kind=kind,
                        data_keys=sorted(data or {})) as s:
            waited = await (self.write_limiter if write else self.read_limiter).acquire()
            raw = await self.page.evaluate(FETCH_JS, [url, method, body, IG_APP_ID, ASBD_ID])
            if raw.get("wrongOrigin") is not None:
                # The page navigated away from Instagram: never run API calls (and never
                # trust responses) from a foreign origin.
                raise InstagramApiError(
                    f"Refusing {url}: the page is on {raw['wrongOrigin']!r}, not {IG_ORIGIN}",
                    path=url, hint="Navigate the Heliograph window back to instagram.com.")
            status = int(raw.get("status", 0))
            text = str(raw.get("body") or "")
            s.set(status=status, bytes=len(text), throttle_wait=round(waited, 3))
            return _decode(status, text, raw, url)


def _decode(status: int, text: str, raw: dict[str, Any], path: str) -> dict[str, Any]:
    final_url = str(raw.get("url") or "")
    lowered = text[:4000].lower()
    if "/accounts/login" in final_url or status == 401:
        raise NotLoggedInError(f"Instagram requires login ({status} on {path})")
    if status == 429 or any(m in lowered for m in _RATE_MARKERS[:1]):
        retry = raw.get("retryAfter")
        raise RateLimitedError(
            f"Instagram rate-limited {path} (HTTP {status})",
            retry_after=float(retry) if retry and str(retry).isdigit() else None,
            hint="Wait a few minutes before retrying; Heliograph already spaces requests.",
        )
    try:
        payload = json.loads(text)
    except ValueError:
        where = f" (redirected to {final_url})" if raw.get("redirected") else ""
        raise InstagramApiError(f"Non-JSON response from {path}: HTTP {status}{where}",
                                status=status, path=path) from None
    if not isinstance(payload, dict):
        raise InstagramApiError(f"Unexpected JSON from {path}", status=status, path=path)
    message = str(payload.get("message") or "").lower()
    if any(m in message for m in _LOGIN_MARKERS) or payload.get("require_login"):
        raise NotLoggedInError(f"Instagram requires login ({message or status})")
    if any(m in message for m in _RATE_MARKERS):
        raise RateLimitedError(f"Instagram refused {path}: {message}")
    if status >= 400 or payload.get("status") == "fail":
        raise InstagramApiError(f"{path} failed: HTTP {status} {message}".rstrip(),
                                status=status, path=path)
    return payload
