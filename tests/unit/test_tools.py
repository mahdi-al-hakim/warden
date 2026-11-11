"""Tests for the access-ops tools and the registry."""

from __future__ import annotations

from warden.domain import RiskTier
from warden.tools import ToolRegistry


def test_lookup_is_read_only_and_returns_profile():
    reg = ToolRegistry()
    result = reg.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert result.ok is True
    assert result.data["name"] == "Jordan Lee"
    assert "crm" in result.data["apps"]


def test_revoke_access_changes_state():
    reg = ToolRegistry()
    reg.execute("directory", "revoke_access", {"user_id": "jordan.lee", "app": "crm"})
    after = reg.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert "crm" not in after.data["apps"]


def test_disable_account_sets_enabled_false():
    reg = ToolRegistry()
    reg.execute("directory", "disable_account", {"user_id": "jordan.lee"})
    after = reg.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert after.data["enabled"] is False


def test_risk_is_read_from_the_spec_and_unknown_is_high():
    reg = ToolRegistry()
    assert reg.risk_for("directory", "lookup_user") is RiskTier.LOW
    assert reg.risk_for("directory", "revoke_access") is RiskTier.MEDIUM
    assert reg.risk_for("directory", "disable_account") is RiskTier.HIGH
    assert reg.risk_for("directory", "made_up_op") is RiskTier.HIGH
    assert reg.risk_for("made_up_tool", "whatever") is RiskTier.HIGH


def test_resolve_account_tier_from_directory():
    reg = ToolRegistry()
    assert reg.resolve_account_tier({"user_id": "jordan.lee"}) == "standard"
    assert reg.resolve_account_tier({"user_id": "security.breakglass"}) == "privileged"
    assert reg.resolve_account_tier({"user_id": "ghost"}) == "unknown"
    assert reg.resolve_account_tier({}) is None


def test_unknown_tool_errors_cleanly():
    result = ToolRegistry().execute("nope", "whatever", {})
    assert result.ok is False
    assert "unknown tool" in result.error


def test_injected_failure_is_a_clean_error():
    reg = ToolRegistry()
    reg.get("directory").inject_failure("revoke_access")
    result = reg.execute("directory", "revoke_access", {"user_id": "jordan.lee", "app": "crm"})
    assert result.ok is False
    assert "simulated upstream failure" in result.error


def test_anthropic_tool_schemas_cover_every_operation():
    schemas = ToolRegistry().anthropic_tool_schemas()
    names = {s["name"] for s in schemas}
    assert names == {
        "directory__lookup_user",
        "directory__revoke_access",
        "directory__disable_account",
        "vpn__status",
        "vpn__disable",
        "repo__list_repos",
        "repo__list_members",
        "repo__remove_member",
    }
    for schema in schemas:
        assert schema["input_schema"]["type"] == "object"


def test_a_tool_that_raises_becomes_a_clean_failed_result():
    # A tool bug must not crash the run: execute() turns an exception into a failed ToolResult so
    # the broker can audit it as a failed action and the agent can continue.
    reg = ToolRegistry()

    def boom(operation, arguments):
        raise RuntimeError("kaboom")

    reg.get("directory").run = boom
    result = reg.execute("directory", "revoke_access", {"user_id": "jordan.lee", "app": "crm"})
    assert result.ok is False
    assert "raised" in result.error and "kaboom" in result.error
