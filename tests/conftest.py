"""Keep the test run hermetic: settings exported for a demo must not reach the tests."""

from __future__ import annotations

import pytest

_HOST_SETTINGS = (
    "ANTHROPIC_API_KEY",
    "WARDEN_MODE",
    "WARDEN_MODEL",
    "WARDEN_EFFORT",
    "WARDEN_TRAJECTORY",
    "WARDEN_GITHUB_TOKEN",
    "WARDEN_GITHUB_ORG",
    "WARDEN_GITHUB_REPOS",
    "WARDEN_GITHUB_TEST_LOGINS",
    "WARDEN_GITHUB_ALLOW_WRITES",
)


@pytest.fixture(autouse=True)
def _no_host_settings(monkeypatch):
    for name in _HOST_SETTINGS:
        monkeypatch.delenv(name, raising=False)
