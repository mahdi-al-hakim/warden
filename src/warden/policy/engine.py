"""The policy engine. Deterministic, first-match over an ordered ruleset, default deny."""

from __future__ import annotations

from ..domain import ActionRequest, Decision, PolicyDecision
from .rules import DEFAULT_RULES, RULESET_VERSION, PolicyRule


class PolicyEngine:
    """Decides ALLOW, DENY, or REQUIRE_APPROVAL for a proposed action.

    The engine never calls out to anything. The same action and ruleset always give the same answer,
    which is what lets the audit log name the exact ruleset version behind every decision.
    """

    def __init__(
        self,
        rules: tuple[PolicyRule, ...] = DEFAULT_RULES,
        ruleset_version: str = RULESET_VERSION,
    ) -> None:
        self.rules = rules
        self.ruleset_version = ruleset_version

    def evaluate(self, action: ActionRequest) -> PolicyDecision:
        """Return the first matching rule's decision, or deny by default when nothing matches."""
        for rule in self.rules:
            if rule.matches(action):
                return PolicyDecision(
                    decision=rule.decision,
                    rule_id=rule.rule_id,
                    reason=rule.reason,
                )
        return PolicyDecision(
            decision=Decision.DENY,
            rule_id="default-deny",
            reason="No rule permitted this action, so it is denied by default.",
        )

    def ruleset(self) -> list[dict]:
        """The full ruleset as plain data, for the API and UI to display."""
        return [rule.describe() for rule in self.rules]
