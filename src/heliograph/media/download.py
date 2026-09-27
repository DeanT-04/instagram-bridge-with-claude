"""Allow-listed, size-capped, atomic downloads from Instagram's CDN.

Safety rules (see ``docs/ARCHITECTURE.md``):

* HTTPS only, default port only, no credentials in the URL.
* The host must be (a subdomain of) one of :data:`ALLOWED_HOST_SUFFIXES`. Redirects are
  followed manually so that **every hop** is checked against the same rules.
* The response ``Content-Type`` must be media-like (video/image/audio/octet-stream).
* The body is capped at ``max_bytes`` (checked against ``Content-Length`` and while
  streaming) and written to ``<dest>.part`` before an atomic rename.

Transient failures (transport errors, 429, 5xx) are retried with exponential backoff;
policy violations raise :class:`~heliograph.errors.DownloadBlockedError` immediately.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from heliograph.errors import DownloadBlockedError, HeliographError
from heliograph.eye import span

__all__ = [
    "ALLOWED_CONTENT_TYPES",
    "ALLOWED_HOST_SUFFIXES",
    "DEFAULT_MAX_BYTES",
    "DownloadError",
    "DownloadResult",
    "check_url",
    "download",
    "download_sync",
]

ALLOWED_HOST_SUFFIXES: tuple[str, ...] = ("cdninstagram.com", "fbcdn.net")
ALLOWED_CONTENT_TYPES: tuple[str, ...] = (
    "video/",
    "image/",
    "audio/",
    "application/octet-stream",
    "binary/octet-stream",
)
DEFAULT_MAX_BYTES = 500 * 1024 * 1024
MAX_REDIRECTS = 5
_CHUNK = 1024 * 256
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Heliograph/0.1"


class DownloadError(HeliographError):
    """A download failed after all retries (network error or unexpected HTTP status)."""


class _Retryable(Exception):
    """Internal marker for failures worth retrying."""


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a successful download."""

    path: Path
    sha256: str
    bytes: int
    content_type: str
    final_host: str
    attempts: int


def check_url(url: str, *, allowed_suffixes: tuple[str, ...] = ALLOWED_HOST_SUFFIXES) -> str:
    """Validate ``url`` against the download policy and return its (lower-cased) host.

    Raises:
        DownloadBlockedError: non-HTTPS scheme, credentials/odd port, or host not allowed.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise DownloadBlockedError(f"only https downloads are allowed (got {parts.scheme!r})")
    if parts.username or parts.password:
        raise DownloadBlockedError("credentials in download URLs are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise DownloadBlockedError("invalid port in download URL") from exc
    if port not in (None, 443):
        raise DownloadBlockedError(f"non-default port {port} is not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or not any(host == s or host.endswith("." + s) for s in allowed_suffixes):
        raise DownloadBlockedError(
            f"host {host or '<none>'!r} is not in the allow-list {list(allowed_suffixes)}"
        )
    return host


def _check_content_type(content_type: str) -> None:
    ctype = content_type.split(";", 1)[0].strip().lower()
    if not any(ctype.startswith(p) for p in ALLOWED_CONTENT_TYPES):
        raise DownloadBlockedError(f"unexpected content-type {ctype or '<none>'!r}")


async def _attempt(
    client: httpx.AsyncClient, url: str, part: Path, max_bytes: int
) -> tuple[str, int, str, str]:
    """One GET (following checked redirects) streamed into ``part``.

    Returns ``(sha256, size, content_type, final_host)``.
    """
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        host = check_url(current)
        async with client.stream("GET", current, follow_redirects=False) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise DownloadError(f"redirect without Location from {host}")
                current = urljoin(current, location)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _Retryable(f"HTTP {resp.status_code} from {host}")
            if resp.status_code != 200:
                raise DownloadError(f"HTTP {resp.status_code} from {host}")
            ctype = resp.headers.get("content-type", "")
            _check_content_type(ctype)
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise DownloadBlockedError(
                    f"content-length {int(declared)} exceeds the {max_bytes}-byte cap"
                )
            digest = hashlib.sha256()
            size = 0
            with part.open("wb") as fh:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    size += len(chunk)
                    if size > max_bytes:
                        raise DownloadBlockedError(f"body exceeds the {max_bytes}-byte cap")
                    digest.update(chunk)
                    fh.write(chunk)
            if declared and declared.isdigit() and size != int(declared):
                raise _Retryable(f"truncated body: got {size} of {declared} bytes")
            return digest.hexdigest(), size, ctype, host
    raise DownloadBlockedError(f"too many redirects (> {MAX_REDIRECTS})")


async def download(
    url: str,
    dest: Path,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    retries: int = 3,
    backoff: float = 0.5,
    timeout: float = 60.0,
) -> DownloadResult:
    """Download ``url`` to ``dest`` atomically and return its path and SHA-256.

    Args:
        url: Signed CDN URL (``https://*.cdninstagram.com/...`` or ``*.fbcdn.net``).
        dest: Final file path; parent directories are created.
        client: Optional shared client (tests inject one with ``httpx.MockTransport``).
        max_bytes: Hard size cap for the body.
        retries: Extra attempts after the first for transient failures.
        backoff: Base delay in seconds; attempt *n* waits ``backoff * 2**n``.
        timeout: Per-request timeout in seconds (used only for an internally created client).

    Raises:
        DownloadBlockedError: the URL, a redirect target, the content-type or size violates
            policy (never retried).
        DownloadError: the download still failed after all retries.
    """
    check_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    owns_client = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0),
        headers={"User-Agent": _USER_AGENT, "Accept": "*/*"},
    )
    t0 = time.perf_counter()
    async with span("media.download", host=urlsplit(url).hostname, dest=str(dest)) as s:
        try:
            last: Exception | None = None
            for attempt in range(retries + 1):
                try:
                    sha, size, ctype, host = await _attempt(http, url, part, max_bytes)
                except (_Retryable, httpx.TransportError) as exc:
                    last = exc
                    s.set(**{f"retry_{attempt}": repr(exc)[:200]})
                    if attempt < retries:
                        await asyncio.sleep(backoff * (2**attempt))
                    continue
                os.replace(part, dest)
                elapsed = time.perf_counter() - t0
                s.set(
                    bytes=size,
                    content_type=ctype,
                    attempts=attempt + 1,
                    mb_per_s=round(size / 1e6 / elapsed, 2) if elapsed > 0 else None,
                )
                return DownloadResult(dest, sha, size, ctype, host, attempt + 1)
            raise DownloadError(f"download failed after {retries + 1} attempts: {last}") from last
        finally:
            with contextlib.suppress(FileNotFoundError):
                part.unlink()
            if owns_client:
                await http.aclose()


def download_sync(
    url: str,
    dest: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    retries: int = 3,
    backoff: float = 0.5,
    timeout: float = 60.0,
) -> DownloadResult:
    """Blocking wrapper around :func:`download` (must not be called from a running loop)."""
    return asyncio.run(
        download(url, dest, max_bytes=max_bytes, retries=retries, backoff=backoff, timeout=timeout)
    )
