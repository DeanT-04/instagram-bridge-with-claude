"""Download policy tests using httpx.MockTransport (no network)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest

from heliograph.errors import DownloadBlockedError
from heliograph.media.download import DownloadError, check_url, download

GOOD = "https://scontent-lhr8-1.cdninstagram.com/v/t50/video.mp4?sig=abc"
BODY = b"\x00\x00\x00\x18ftypmp42" + b"x" * 5000

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=BODY, headers={"content-type": "video/mp4"})


@pytest.mark.parametrize(
    "url",
    [
        GOOD,
        "https://video.fbcdn.net/x.mp4",
        "https://cdninstagram.com/x.mp4",
        "https://SCONTENT.CDNINSTAGRAM.COM./x.mp4",
    ],
)
def test_check_url_allows_cdn_hosts(url: str) -> None:
    assert check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://scontent.cdninstagram.com/x.mp4",  # not https
        "https://evil.com/x.mp4",
        "https://cdninstagram.com.evil.com/x.mp4",
        "https://evilcdninstagram.com/x.mp4",
        "https://user:pw@scontent.cdninstagram.com/x.mp4",
        "https://scontent.cdninstagram.com:8443/x.mp4",
        "file:///C:/Windows/win.ini",
        "https:///nohost",
    ],
)
def test_check_url_blocks(url: str) -> None:
    with pytest.raises(DownloadBlockedError):
        check_url(url)


async def test_download_writes_atomically_with_sha(tmp_path: Path) -> None:
    dest = tmp_path / "sub" / "video.mp4"
    async with _client(_ok) as client:
        res = await download(GOOD, dest, client=client)
    assert dest.read_bytes() == BODY
    assert res.sha256 == hashlib.sha256(BODY).hexdigest()
    assert res.bytes == len(BODY)
    assert not (tmp_path / "sub" / "video.mp4.part").exists()


async def test_redirect_to_disallowed_host_is_blocked(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.endswith("cdninstagram.com"):
            return httpx.Response(302, headers={"location": "https://evil.example/x.mp4"})
        return _ok(request)

    dest = tmp_path / "v.mp4"
    async with _client(handler) as client:
        with pytest.raises(DownloadBlockedError, match=r"evil\.example"):
            await download(GOOD, dest, client=client)
    assert not dest.exists()


async def test_redirect_within_allow_list_is_followed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host.startswith("scontent"):
            return httpx.Response(301, headers={"location": "https://video.fbcdn.net/y.mp4"})
        return _ok(request)

    async with _client(handler) as client:
        res = await download(GOOD, tmp_path / "v.mp4", client=client)
    assert res.final_host == "video.fbcdn.net"


async def test_redirect_loop_is_blocked(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": GOOD})

    async with _client(handler) as client:
        with pytest.raises(DownloadBlockedError, match="redirects"):
            await download(GOOD, tmp_path / "v.mp4", client=client)


async def test_bad_content_type_is_blocked(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    async with _client(handler) as client:
        with pytest.raises(DownloadBlockedError, match="content-type"):
            await download(GOOD, tmp_path / "v.mp4", client=client)


async def test_size_cap_via_content_length(tmp_path: Path) -> None:
    async with _client(_ok) as client:
        with pytest.raises(DownloadBlockedError, match="cap"):
            await download(GOOD, tmp_path / "v.mp4", client=client, max_bytes=100)


async def test_size_cap_while_streaming(tmp_path: Path) -> None:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"y" * 1000

    def no_length(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "video/mp4"})

    dest = tmp_path / "v.mp4"
    async with _client(no_length) as client:
        with pytest.raises(DownloadBlockedError, match="cap"):
            await download(GOOD, dest, client=client, max_bytes=2500)
    assert not dest.exists()
    assert not (tmp_path / "v.mp4.part").exists()


async def test_retries_transient_errors_then_succeeds(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        if calls["n"] == 2:
            return httpx.Response(503)
        return _ok(request)

    async with _client(handler) as client:
        res = await download(GOOD, tmp_path / "v.mp4", client=client, backoff=0)
    assert res.attempts == 3


async def test_gives_up_after_retries(tmp_path: Path) -> None:
    async with _client(lambda r: httpx.Response(500)) as client:
        with pytest.raises(DownloadError, match="3 attempts"):
            await download(GOOD, tmp_path / "v.mp4", client=client, retries=2, backoff=0)


async def test_does_not_retry_4xx(tmp_path: Path) -> None:
    calls = {"n": 0}

    def forbidden(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403)

    async with _client(forbidden) as client:
        with pytest.raises(DownloadError, match="403"):
            await download(GOOD, tmp_path / "v.mp4", client=client, backoff=0)
    assert calls["n"] == 1


async def test_blocked_url_makes_no_request(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request expected")

    async with _client(handler) as client:
        with pytest.raises(DownloadBlockedError):
            await download("https://example.com/a.mp4", tmp_path / "a.mp4", client=client)
