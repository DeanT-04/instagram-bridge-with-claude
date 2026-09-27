"""Scrubbing of secrets before anything is written by the eye.

:func:`redact` walks arbitrarily nested data (dicts, lists, tuples, sets, pydantic models)
and returns a copy in which

* values under sensitive keys (cookies, ``sessionid``, ``csrftoken``, ``Authorization``,
  passwords, API keys, tokens, secrets) are replaced by :data:`REDACTED`, and
* sensitive substrings inside free text (``Bearer ...`` headers, ``sessionid=...`` cookie
  pairs, ``password=...`` assignments, JWTs, long random-looking tokens) are masked.

It never raises and never mutates its input.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

__all__ = ["REDACTED", "is_sensitive_key", "redact", "redact_text"]

REDACTED = "[REDACTED]"
_MAX_DEPTH = 32

_SENSITIVE_EXACT = frozenset(
    {
        "cookie", "cookies", "setcookie", "sessionid", "csrftoken", "csrf", "xcsrftoken",
        "authorization", "proxyauthorization", "auth", "password", "passwd", "pwd", "pass",
        "secret", "token", "apikey", "xapikey", "privatekey", "dsuserid", "igdid", "mid",
        "datr", "rur", "shbid", "shbts", "xigwwwclaim", "xigsetauthorization", "credentials",
        "credential", "otp", "twofactorcode", "verificationcode", "igsetauthorization",
        "igudsuserid", "igurur", "iguigdid", "igushbid", "igushbts", "igintendeduserid",
        "xmid", "wwwclaim", "encpassword",
    }
)
_SENSITIVE_SUFFIXES = ("token", "secret", "password", "passwd", "apikey", "cookie", "sessionid")
_SENSITIVE_PREFIXES = ("password", "secret", "cookie")

_COOKIE_NAMES = (
    "sessionid|csrftoken|ds_user_id|ig_did|mid|datr|rur|shbid|shbts|fr|xs|c_user|sb|ig_nrcb"
)
# Keys whose value is a secret when they appear as ``key: value`` / ``key=value`` /
# ``"key": "value"`` inside free text (reprs, JSON dumps, exception messages).
_SECRET_TEXT_KEYS = (
    r"[\w-]*?(?:password|passwd|secret|api[_-]?key|token)|pwd|"
    r"(?:set-)?cookies?|(?:proxy-|x-ig-set-|ig-set-)?authorization|"
    r"x-ig-www-claim|ig-u-[\w-]+|ig-intended-user-id|x-mid|sessionid|ds_user_id"
)
_QUOTED_OR_BARE = r"(?:\"[^\"]*\"|'[^']*'|[^\s\"'&;,}]+)"
# Signed query parameters on Instagram/Facebook CDN (and S3-style) URLs.
_SIGNED_QUERY = (
    r"oh|oe|efg|ccb|edm|_nc_[a-z0-9_]+|sig|signature|"
    r"x-amz-(?:signature|credential|security-token)"
)


def _mask_kv(match: re.Match[str]) -> str:
    value = match.group(2)
    quote = value[0] if value[:1] in ("'", '"') and len(value) >= 2 else ""
    return f"{match.group(1)}{quote}{REDACTED}{quote}"


_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], Any], ...] = (
    # Authorization schemes.
    (re.compile(r"(?i)\b(bearer|basic|token|igt:\d+:?)\s+[A-Za-z0-9._~+/=:\-]{6,}"),
     rf"\1 {REDACTED}"),
    # Bare Instagram auth tokens (IGT:2:<base64>).
    (re.compile(r"(?i)\bIGT:\d+:[A-Za-z0-9+/=_\-]{6,}"), REDACTED),
    # Header-style lines: "Cookie: ...", "Authorization: ...", "IG-Set-Authorization: ...".
    (re.compile(r"(?im)^(\s*(?:set-)?cookie\s*:\s*).+$"), rf"\1{REDACTED}"),
    (re.compile(r"(?im)^(\s*[\w-]*authorization\s*:\s*).+$"), rf"\1{REDACTED}"),
    # Cookie pairs anywhere in text (also URL-encoded ``name%3Dvalue`` and quoted values).
    (re.compile(rf"(?i)\b({_COOKIE_NAMES})(=|%3D)([\"']?)[^;\s&\"',]+"),
     rf"\1\2\3{REDACTED}"),
    # key=value / key: value / "key": "value" assignments for secret-ish keys.
    (
        re.compile(
            rf"(?i)([\"']?\b(?:{_SECRET_TEXT_KEYS})[\"']?\s*[:=]\s*)({_QUOTED_OR_BARE})"
        ),
        _mask_kv,
    ),
    # Signed CDN URL query parameters (oh=, oe=, _nc_ohc=, ...): the URL path is kept.
    (re.compile(rf"(?i)([?&](?:{_SIGNED_QUERY})=)[^&#\s\"'<>]+"), rf"\1{REDACTED}"),
    # JSON Web Tokens.
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}"), REDACTED),
    # Common API key shapes (sk-..., pk-lf-..., ghp_..., xox?-...).
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"), REDACTED),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), REDACTED),
)
# Long random-looking tokens: >= 32 chars of [A-Za-z0-9_-/+=%] mixing upper, lower and digits.
_LONG_TOKEN = re.compile(r"[A-Za-z0-9_\-+/=%]{32,}")


def _normalize_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: object) -> bool:
    """Return True if a mapping key names a secret (case/punctuation-insensitive)."""
    k = _normalize_key(key)
    if not k:
        return False
    return (
        k in _SENSITIVE_EXACT
        or k.endswith(_SENSITIVE_SUFFIXES)
        or k.startswith(_SENSITIVE_PREFIXES)
    )


_COOKIE_NAME_SET = frozenset(_COOKIE_NAMES.split("|"))


def _is_secret_name_value_pair(value: Mapping[Any, Any]) -> bool:
    name = value.get("name")
    if not isinstance(name, str) or "value" not in value:
        return False
    return name.lower() in _COOKIE_NAME_SET or is_sensitive_key(name)


def _mask_long_token(match: re.Match[str]) -> str:
    s = match.group(0)
    core = s.strip("/")
    if "/" in core:  # looks like a path, not a token
        return s
    has_upper = any(c.isupper() for c in core)
    has_lower = any(c.islower() for c in core)
    has_digit = any(c.isdigit() for c in core)
    return REDACTED if (has_upper and has_lower and has_digit) else s


def redact_text(text: str) -> str:
    """Mask secrets inside a free-text string."""
    for pattern, repl in _TEXT_PATTERNS:
        text = pattern.sub(repl, text)
    return _LONG_TOKEN.sub(_mask_long_token, text)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a deep copy of ``value`` with secrets removed. Never raises."""
    try:
        if _depth > _MAX_DEPTH:
            return "[MAX_DEPTH]"
        if isinstance(value, str):
            return redact_text(value)
        if value is None or isinstance(value, bool | int | float):
            return value
        if isinstance(value, bytes | bytearray | memoryview):
            return f"<{len(value)} bytes>"
        if isinstance(value, Mapping):
            if _is_secret_name_value_pair(value):
                # e.g. Playwright/CDP cookies: {"name": "sessionid", "value": "..."}
                return {
                    str(k): REDACTED if k == "value" else redact(v, _depth=_depth + 1)
                    for k, v in value.items()
                }
            return {
                (k if isinstance(k, str) else str(k)): (
                    REDACTED if is_sensitive_key(k) and v not in (None, "")
                    else redact(v, _depth=_depth + 1)
                )
                for k, v in value.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [redact(v, _depth=_depth + 1) for v in value]
        dump = getattr(value, "model_dump", None)
        if callable(dump):  # pydantic models
            return redact(dump(mode="json"), _depth=_depth + 1)
        return value
    except Exception:  # pragma: no cover - defensive: redaction must never break callers
        return "[UNREDACTABLE]"
