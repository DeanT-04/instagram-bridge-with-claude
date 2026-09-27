from __future__ import annotations

import pytest
from pydantic import BaseModel

from heliograph.eye.redact import REDACTED, is_sensitive_key, redact, redact_text


@pytest.mark.parametrize(
    "key",
    [
        "cookie", "Cookie", "Set-Cookie", "sessionid", "csrftoken", "X-CSRFToken",
        "Authorization", "authorization", "Proxy-Authorization", "password", "PASSWORD",
        "user_password", "passwd", "api_key", "apiKey", "X-API-Key", "access_token",
        "refreshToken", "client_secret", "secret", "ds_user_id", "x-ig-www-claim",
        "cookies", "private_key",
    ],
)
def test_sensitive_keys(key: str) -> None:
    assert is_sensitive_key(key)


@pytest.mark.parametrize(
    "key", ["username", "url", "caption", "max_tokens", "like_count", "media_id", "", "name"]
)
def test_non_sensitive_keys(key: str) -> None:
    assert not is_sensitive_key(key)


def test_redacts_nested_structures_recursively() -> None:
    data = {
        "headers": {"Cookie": "sessionid=abc; csrftoken=def", "Accept": "application/json"},
        "items": [{"password": "hunter2", "user": "bob"}, ("x", {"token": "t0k"})],
        "count": 3,
        "ok": True,
        "none": None,
    }
    out = redact(data)
    assert out["headers"]["Cookie"] == REDACTED
    assert out["headers"]["Accept"] == "application/json"
    assert out["items"][0] == {"password": REDACTED, "user": "bob"}
    assert out["items"][1][1]["token"] == REDACTED
    assert out["count"] == 3 and out["ok"] is True and out["none"] is None
    # input untouched
    assert data["items"][0]["password"] == "hunter2"


def test_empty_sensitive_values_are_left_alone() -> None:
    assert redact({"password": None, "token": ""}) == {"password": None, "token": ""}


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer IGT:2:eyJkc191c2VyX2lkIjoiMTIzIn0=", "IGT:2:eyJ"),
        ("header bearer abcdef123456 trailing", "abcdef123456"),
        ("Cookie: sessionid=123%3Aabc%3A12; csrftoken=zzz", "123%3Aabc"),
        ("url?x=1&sessionid=987654321%3AAbCdEf&y=2", "987654321"),
        ("csrftoken=Q2xpZW50U2VjcmV0 rest", "Q2xpZW50U2VjcmV0"),
        ('{"password": "hunter2", "a": 1}', "hunter2"),
        ("password=hunter2&next=/", "hunter2"),
        ("api_key: sk-verysecretvalue1234567890", "sk-verysecret"),
        ("access_token=EAAB1234abcd", "EAAB1234abcd"),
        ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N", "eyJhbGci"),
        ("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv", "sk-ant-api03"),
        ("gh ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "ghp_AbCd"),
        ("random Xy7Kq9Lm2Np4Rs6Tv8Wx0Za1Bc3De5Fg7Hi9 end", "Xy7Kq9Lm2Np4"),
    ],
)
def test_redact_text_masks_secrets(text: str, secret: str) -> None:
    out = redact_text(text)
    assert secret not in out
    assert REDACTED in out


@pytest.mark.parametrize(
    "text",
    [
        "https://www.instagram.com/reel/C8abcDEF123/",
        "sha256 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "media 3412345678901234567_123456789",
        r"C:\Users\alice\.heliograph\eye\artifacts\screenshot.png",
        "a normal sentence about tokens and cookies",
    ],
)
def test_redact_text_leaves_benign_text(text: str) -> None:
    assert redact_text(text) == text


def test_redacts_pydantic_models_and_bytes() -> None:
    class Creds(BaseModel):
        username: str
        password: str

    out = redact({"creds": Creds(username="u", password="p"), "blob": b"\x00" * 10})
    assert out["creds"] == {"username": "u", "password": REDACTED}
    assert out["blob"] == "<10 bytes>"


def test_deep_nesting_does_not_explode() -> None:
    deep: dict[str, object] = {}
    cur = deep
    for _ in range(100):
        nxt: dict[str, object] = {}
        cur["x"] = nxt
        cur = nxt
    assert redact(deep) is not None


# --- Security review (M1): gaps found in the redactor. -------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        # Signed CDN query parameters.
        ("https://scontent.cdninstagram.com/v/x.mp4?_nc_ohc=AbCdEf12345&oh=00_AYBcd99&oe=66A1B2C3",
         "00_AYBcd99"),
        ("https://scontent.cdninstagram.com/v/x.mp4?_nc_ohc=AbCdEf12345&oe=66A1B2C3",
         "AbCdEf12345"),
        ("https://x.fbcdn.net/y.jpg?efg=eyJ2ZW5jb2RlX3RhZyI6&oe=66A1B2C3", "66A1B2C3"),
        # Header/cookie keys inside reprs and JSON dumps.
        ("{'Cookie': 'abc123def'}", "abc123def"),
        ("{'authorization': 'abc123def456'}", "abc123def456"),
        ('{"ds_user_id": "123456789"}', "123456789"),
        ("headers={'X-IG-WWW-Claim': 'hmac.AR3abcdef'}", "hmac.AR3abcdef"),
        # Quoted and URL-encoded cookie pairs.
        ("Cookie(sessionid='12345%3Aabc')", "12345%3Aabc"),
        ("sessionid%3D12345%3AabcDEF", "12345%3AabcDEF"),
        # Instagram auth headers/tokens.
        ("ig-set-authorization: IGT:2:eyJxxxxxxxx", "eyJxxxxxxxx"),
        ("token is IGT:2:eyJkc191c2VyX2lk", "eyJkc191c2VyX2lk"),
        # Prefixed password fields.
        ("enc_password=#PWD_INSTAGRAM_BROWSER:10:1700000000:AbCd", "#PWD_INSTAGRAM"),
        ("pwd='s3cr3t'", "s3cr3t"),
    ],
)
def test_redact_text_security_review_gaps(text: str, secret: str) -> None:
    out = redact_text(text)
    assert secret not in out
    assert REDACTED in out


def test_signed_url_keeps_path() -> None:
    out = redact_text("https://scontent.cdninstagram.com/v/t51/clip.mp4?oh=00_AYB123&oe=66A1")
    assert out.startswith("https://scontent.cdninstagram.com/v/t51/clip.mp4?oh=")


@pytest.mark.parametrize(
    "key", ["IG-Set-Authorization", "IG-U-DS-USER-ID", "ig-u-rur", "X-MID", "enc_password"]
)
def test_instagram_header_keys_sensitive(key: str) -> None:
    assert is_sensitive_key(key)


def test_cookie_name_value_records_are_redacted() -> None:
    cookies = [
        {"name": "sessionid", "value": "123%3Aabc", "domain": ".instagram.com"},
        {"name": "ds_user_id", "value": "42"},
        {"name": "ig_lang", "value": "en"},
    ]
    out = redact({"items": cookies})
    assert out["items"][0] == {"name": "sessionid", "value": REDACTED,
                                      "domain": ".instagram.com"}
    assert out["items"][1]["value"] == REDACTED
    assert out["items"][2]["value"] == "en"


def test_exception_message_and_traceback_are_redacted(isolated_home: object) -> None:
    import json

    from heliograph import eye

    with pytest.raises(RuntimeError), eye.span("leaky"):
        raise RuntimeError("GET https://i.instagram.com/api?x=1 Cookie sessionid=999%3Asecret")
    line = (eye.get_eye().directory / "events.jsonl").read_text(encoding="utf-8")
    assert "999%3Asecret" not in line
    err = json.loads(line.splitlines()[-1])["error"]
    assert REDACTED in err["message"] and "999%3Asecret" not in err["traceback"]
