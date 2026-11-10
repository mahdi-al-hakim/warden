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
SECRET_ALLOWLIST = {"token_count", "tokens", "max_tokens"}

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


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in SECRET_ALLOWLIST:
        return False
    return any(fragment in lowered for fragment in SECRET_FRAGMENTS)


def _mask_email(address: str) -> str:
    local, _, domain = address.partition("@")
    head = local[0] if local else "x"
    return f"{head}***@{domain}"


def _mask_text(value: str) -> str:
    masked = _TOKEN_VALUE.sub("[redacted]", value)
    masked = _GITHUB_PAT.sub("[redacted]", masked)
    masked = _AWS_KEY.sub("[redacted]", masked)
    masked = _BEARER_VALUE.sub("[redacted]", masked)
    masked = _JWT_VALUE.sub("[redacted]", masked)
    masked = _EMAIL.sub(lambda m: _mask_email(m.group(0)), masked)
    masked = _LONG_DIGITS.sub(lambda m: "***" + m.group(0)[-4:], masked)
    return masked


def redact(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `data` with secrets removed and personal detail partially masked."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if _is_secret_key(key):
            out[key] = "[redacted]"
        elif isinstance(value, dict):
            out[key] = redact(value)
        elif isinstance(value, list):
            out[key] = [redact(v) if isinstance(v, dict) else _mask_item(v) for v in value]
        else:
            out[key] = _mask_item(value)
    return out


def _mask_item(value: Any) -> Any:
    return _mask_text(value) if isinstance(value, str) else value
