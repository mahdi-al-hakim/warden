"""Declarative policy rules for the access-operations demo.

A rule is data, not code, so the whole ruleset can be shown in the UI and version-stamped. Rules are
ordered most specific first; the engine takes the first match and otherwise denies by default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domain import ActionRequest, Decision, RiskTier

# Bumped whenever the rules change. Stamped into every decision and audit event, so a reader can
# always tell which ruleset produced a verdict.
RULESET_VERSION = "2026.11.04"


@dataclass(frozen=True)
class PolicyRule:
    """One rule. It matches on tool, operation, risk tier, and optional argument equality checks."""

    rule_id: str
    decision: Decision
    reason: str
    tool: str = "*"
    operation: str = "*"
    risk_tiers: tuple[RiskTier, ...] = ()
    arg_match: tuple[tuple[str, Any], ...] = ()

    def matches(self, action: ActionRequest) -> bool:
        """True when every condition set on this rule holds for the action."""
        if self.tool != "*" and self.tool != action.tool:
            return False
        if self.operation != "*" and self.operation != action.operation:
            return False
        if self.risk_tiers and action.risk_tier not in self.risk_tiers:
            return False
        for key, expected in self.arg_match:
            if action.arguments.get(key) != expected:
                return False
        return True

    def describe(self) -> dict[str, Any]:
        """A plain dict for the API and UI to render the rule."""
        return {
            "rule_id": self.rule_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "tool": self.tool,
            "operation": self.operation,
            "risk_tiers": [t.value for t in self.risk_tiers],
            "arg_match": {k: v for k, v in self.arg_match},
        }


# The shipped ruleset for the offboarding demo. Order matters: protection comes before permission.
DEFAULT_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(
        rule_id="deny-writes-to-privileged-accounts",
        decision=Decision.DENY,
        reason="An agent may not change a privileged or break-glass account during offboarding.",
        risk_tiers=(RiskTier.MEDIUM, RiskTier.HIGH),
        arg_match=(("account_tier", "privileged"),),
    ),
    PolicyRule(
        rule_id="approve-full-account-disable",
        decision=Decision.REQUIRE_APPROVAL,
        reason="Disabling an account outright is high impact, so a human signs off first.",
        tool="directory",
        operation="disable_account",
    ),
    PolicyRule(
        rule_id="allow-standard-offboarding-writes",
        decision=Decision.ALLOW,
        reason="Revoking app access, VPN, and repository membership is routine offboarding.",
        risk_tiers=(RiskTier.MEDIUM,),
    ),
    PolicyRule(
        rule_id="allow-read-only-lookups",
        decision=Decision.ALLOW,
        reason="Read-only lookups change nothing and are allowed.",
        risk_tiers=(RiskTier.LOW,),
    ),
)
