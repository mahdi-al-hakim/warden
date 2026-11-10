"""Tests for the policy engine, including the edge cases that matter for the demo."""

from __future__ import annotations

from warden.domain import ActionRequest, Decision, RiskTier
from warden.policy import PolicyEngine


def action(tool, operation, risk=RiskTier.LOW, **arguments) -> ActionRequest:
    return ActionRequest(
        session_id="ses_test",
        tool=tool,
        operation=operation,
        risk_tier=risk,
        arguments=arguments,
    )


def test_read_only_lookup_is_allowed():
    d = PolicyEngine().evaluate(action("directory", "lookup_user", RiskTier.LOW))
    assert d.decision is Decision.ALLOW
    assert d.rule_id == "allow-read-only-lookups"


def test_standard_offboarding_write_is_allowed():
    d = PolicyEngine().evaluate(action("directory", "revoke_access", RiskTier.MEDIUM, app="crm"))
    assert d.decision is Decision.ALLOW
    assert d.rule_id == "allow-standard-offboarding-writes"


def test_full_account_disable_requires_approval():
    act = action("directory", "disable_account", RiskTier.HIGH, user="jordan")
    d = PolicyEngine().evaluate(act)
    assert d.decision is Decision.REQUIRE_APPROVAL
    assert d.rule_id == "approve-full-account-disable"


def test_write_to_privileged_account_is_denied():
    d = PolicyEngine().evaluate(
        action("directory", "revoke_access", RiskTier.MEDIUM, account_tier="privileged")
    )
    assert d.decision is Decision.DENY
    assert d.rule_id == "deny-writes-to-privileged-accounts"


def test_disable_of_privileged_account_is_denied_not_merely_approval():
    # Protection beats permission: a privileged-account write is denied before the approval rule.
    d = PolicyEngine().evaluate(
        action("directory", "disable_account", RiskTier.HIGH, account_tier="privileged")
    )
    assert d.decision is Decision.DENY
    assert d.rule_id == "deny-writes-to-privileged-accounts"


def test_read_of_privileged_account_is_still_allowed():
    # Reading a privileged account changes nothing, so the deny-writes rule must not catch it.
    d = PolicyEngine().evaluate(
        action("directory", "lookup_user", RiskTier.LOW, account_tier="privileged")
    )
    assert d.decision is Decision.ALLOW
    assert d.rule_id == "allow-read-only-lookups"


def test_unknown_high_risk_action_is_denied_by_default():
    d = PolicyEngine().evaluate(action("payroll", "export_all", RiskTier.HIGH))
    assert d.decision is Decision.DENY
    assert d.rule_id == "default-deny"


def test_empty_ruleset_denies_everything():
    engine = PolicyEngine(rules=())
    d = engine.evaluate(action("directory", "lookup_user", RiskTier.LOW))
    assert d.decision is Decision.DENY
    assert d.rule_id == "default-deny"


def test_engine_exposes_ruleset_version_and_rules():
    engine = PolicyEngine()
    assert engine.ruleset_version
    rules = engine.ruleset()
    assert isinstance(rules, list) and rules
    assert {r["rule_id"] for r in rules} >= {
        "deny-writes-to-privileged-accounts",
        "approve-full-account-disable",
    }


def test_decision_is_immutable():
    d = PolicyEngine().evaluate(action("directory", "lookup_user"))
    # PolicyDecision is frozen; a caller cannot quietly rewrite the verdict after the fact.
    try:
        d.decision = Decision.ALLOW
    except Exception as exc:  # pydantic raises on frozen mutation
        assert "frozen" in str(exc).lower() or exc.__class__.__name__ == "ValidationError"
    else:
        raise AssertionError("PolicyDecision should be frozen")
