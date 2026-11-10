"""Tests for the pure domain layer and configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from warden.config import Settings
from warden.domain import ActionRequest, AuditEvent, Decision, RiskTier, new_id


def test_new_id_has_prefix_and_is_unique():
    a = new_id("act")
    b = new_id("act")
    assert a.startswith("act_")
    assert a != b


def test_action_request_defaults_and_is_frozen():
    req = ActionRequest(session_id="ses_1", tool="directory", operation="revoke_access")
    assert req.action_id.startswith("act_")
    assert req.risk_tier is RiskTier.LOW
    assert req.arguments == {}
    with pytest.raises(ValidationError):
        req.operation = "something_else"  # frozen: requests cannot be edited after proposal


def test_audit_content_bytes_is_stable_and_excludes_hash():
    ev = AuditEvent(seq=1, session_id="ses_1", kind="action.executed", data={"b": 2, "a": 1})
    ev2 = ev.model_copy(update={"hash": "deadbeef"})
    # The hash field is not part of the signed content, so setting it changes nothing.
    assert ev.content_bytes() == ev2.content_bytes()
    # Keys are sorted, so field order in `data` does not change the bytes.
    same = AuditEvent(
        seq=1, ts=ev.ts, session_id="ses_1", kind="action.executed", data={"a": 1, "b": 2}
    )
    assert same.content_bytes() == ev.content_bytes()


def test_audit_content_bytes_changes_when_data_changes():
    ev = AuditEvent(seq=1, session_id="ses_1", kind="k", data={"x": 1})
    other = AuditEvent(seq=1, ts=ev.ts, session_id="ses_1", kind="k", data={"x": 2})
    assert ev.content_bytes() != other.content_bytes()


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("WARDEN_MODE", "live")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("WARDEN_APPROVAL_TIMEOUT_SECONDS", "5")
    s = Settings.from_env()
    assert s.is_live is True
    assert s.anthropic_api_key == "sk-test"
    assert s.approval_timeout_seconds == 5.0


def test_settings_defaults_replay(monkeypatch):
    monkeypatch.delenv("WARDEN_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings.from_env()
    assert s.mode == "replay"
    assert s.is_live is False
    assert s.anthropic_api_key is None


def test_decision_enum_is_json_friendly():
    assert Decision.REQUIRE_APPROVAL.value == "require_approval"
