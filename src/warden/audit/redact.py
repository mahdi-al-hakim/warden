"""Mask secrets and personal data before anything is written to the audit log.

Secrets are removed. Emails and long numbers (phone, tax id) are partially masked, so the log still
says who an action touched without exposing the full detail. Keys are matched on fragments, not on
exact names, so access_token, client_secret, and private_key are caught alongside token and secret.
Values that look like credentials are removed even under a harmless key. The shipped demo data is
synthetic, and this runs anyway so the pattern is visible to anyone reading the code.
"""

from __future__ import annotations

import re
from typing import Any

# A key is treated as secret if its lowercased name contains any of these fragments.
SECRET_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "private_key",
    "privatekey",
    "bearer",
)
# Keys that contain a fragment above but are safe, so they are not redacted.
SECRET_ALLOWLIST = {
    "token_count",
    "tokens",
    "max_tokens",
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
}

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"\b\d{7,}\b")
# Credential-shaped values: provider keys (sk-..., ghp_..., xoxb-...), bearer tokens, and JWTs.
_TOKEN_VALUE = re.compile(
    r"(?i)\b(?:sk|pk|rk|sk-ant|ghp|gho|ghs|ghu|ghr|xox[baprs])[-_][A-Za-z0-9\-_]{6,}"
)
# GitHub fine-grained tokens and AWS access keys carry no separator after the prefix, so they need
# their own patterns rather than the separator-based one above.
_GITHUB_PAT = re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[0-9A-Z]{12,}\b")
_BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{8,}")
_JWT_VALUE = re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}")
_JWT_START = re.compile(r"\beyJ")

# Characters an address's local part, and a JWT segment, can be made of.
_LOCAL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-")
_SEGMENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SECRET_ALLOWLIST:
        return False
    return any(fragment in lowered for fragment in SECRET_FRAGMENTS)


def _mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    head = local[0] if local else "x"
    return f"{head}***@{domain}"


# Why the email and JWT patterns are not applied with re.sub: re.sub tries a match from every
# position, and inside a long run of address or token characters each try scans to the end of the
# run. That is quadratic. A 100 KB string with no spaces took about seven seconds, on the server's
# event loop, so it stalled every request, the kill switch included. The two functions below make
# one attempt where re.sub's leftmost search would first succeed, with the same pattern, so they
# return exactly what re.sub returns, in time linear in the input.


def _mask_emails(value: str) -> str:
    """Mask each address `_EMAIL` finds, in linear time.

    An address needs an "@", so each "@" gets one attempt, from the start of the run of local-part
    characters just before it. Starting any later in that run reaches the same "@" and the same
    domain, so it succeeds or fails the same way, and the leftmost start is the one re.sub reports.
    """
    out: list[str] = []
    last = 0
    for at in re.finditer("@", value):
        start = at.start()
        while start > last and value[start - 1] in _LOCAL_CHARS:
            start -= 1
        if start == at.start():
            continue
        match = _EMAIL.match(value, start)
        if match is None:
            continue
        out.append(value[last:start])
        out.append(_mask_email(match.group(0)))
        last = match.end()
    out.append(value[last:])
    return "".join(out)


def _mask_jwts(value: str) -> str:
    """Replace each JWT `_JWT_VALUE` finds, in linear time.

    When an attempt fails, every later "eyJ" in the same run of segment characters ends its first
    segment at the same place, so it fails the same way, and the scan skips to the end of that run.
    """
    out: list[str] = []
    last = 0
    skip_to = 0
    for start in _JWT_START.finditer(value):
        pos = start.start()
        if pos < last or pos < skip_to:
            continue
        match = _JWT_VALUE.match(value, pos)
        if match is None:
            end = pos
            while end < len(value) and value[end] in _SEGMENT_CHARS:
                end += 1
            skip_to = end
            continue
        out.append(value[last:pos])
        out.append("[redacted]")
        last = match.end()
    out.append(value[last:])
    return "".join(out)


def _mask_text(value: str) -> str:
    masked = _TOKEN_VALUE.sub("[redacted]", value)
    masked = _GITHUB_PAT.sub("[redacted]", masked)
    masked = _AWS_KEY.sub("[redacted]", masked)
    masked = _BEARER_VALUE.sub("[redacted]", masked)
    masked = _mask_jwts(masked)
    masked = _mask_emails(masked)
    masked = _LONG_DIGITS.sub(lambda m: "***" + m.group(0)[-4:], masked)
    return masked


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `data` with secrets removed and personal detail partially masked."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(key, str) and _is_secret_key(key):
            out[key] = "[redacted]"
        else:
            out[key] = _redact_value(value)
    return out


# Redaction runs on the event loop, so its cost has to be bounded by more than its input: masking
# is linear, but a 5 MB string still takes over a second. A value longer than this is replaced
# whole by a note of its size rather than cut, because a cut could split a secret into a piece
# the patterns no longer recognize. Nothing an agent legitimately sends or reads comes close.
MAX_AUDIT_TEXT = 65_536


def _redact_text(value: str) -> str:
    if len(value) > MAX_AUDIT_TEXT:
        return f"[{len(value)} characters not kept]"
    return _mask_text(value)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    return _redact_text(value) if isinstance(value, str) else value
