"""The broker. Every action an agent proposes passes through here, and only here.

The agent never holds a tool. It hands the broker a proposed call, and the broker decides and acts:
check revocation, resolve the real risk and account tier, evaluate policy, pause for a human when
required, re-check revocation right before running, execute, and write the audit entry. Because of
the re-check after an approval, a revoke that lands while a step waits for a human still stops it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from ..audit import AuditLog
from ..domain import (
    ActionOutcome,
    ActionRequest,
    Decision,
    EventType,
    PolicyDecision,
    RiskTier,
    Session,
    SessionStatus,
    StreamEvent,
    ToolResult,
)
from ..policy import PolicyEngine
from ..revocation import GatewayEnforcer, RevocationAuthority
from ..tools import ToolRegistry

Emit = Callable[[StreamEvent], None]


class HandledAction(BaseModel):
    """What the broker did with one proposed action, returned to the runner and the caller."""

    action_id: str
    outcome: ActionOutcome
    decision: PolicyDecision | None = None
    result: ToolResult | None = None
    approver: str | None = None
    # Why the action did not run, in words the agent is shown as its tool result.
    reason: str | None = None


class Broker:
    """Mediates agent and tools under policy, approval, revocation, and audit."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        policy: PolicyEngine,
        audit: AuditLog,
        authority: RevocationAuthority,
        gateway: GatewayEnforcer,
        approval_timeout_seconds: float = 120.0,
        emit: Emit | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.authority = authority
        self.gateway = gateway
        self.approval_timeout_seconds = approval_timeout_seconds
        self._emit = emit or (lambda event: None)
        # Each pending entry keeps the agent id so the approval check knows whose action it is.
        self._pending: dict[str, tuple[asyncio.Future, ActionRequest, str]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def release_session(self, session_id: str) -> None:
        """Drop per-session state once a run is over, so locks do not pile up across many runs."""
        self._locks.pop(session_id, None)

    def _event(self, event_type: str, session_id: str, **data: Any) -> None:
        self._emit(StreamEvent(type=event_type, session_id=session_id, data=data))

    def pending_action_ids(self) -> list[str]:
        """Action ids currently waiting on a human. Used by the API and tests."""
        return list(self._pending)

    def pending_action(self, action_id: str) -> ActionRequest | None:
        """The action waiting on a human under this id, or None if nothing is waiting."""
        entry = self._pending.get(action_id)
        return entry[1] if entry else None

    async def handle_action(
        self,
        *,
        session: Session,
        agent_id: str,
        tool: str,
        operation: str,
        arguments: dict[str, Any],
        message: str = "",
    ) -> HandledAction:
        """Run one proposed action all the way through the control plane."""
        risk = self.registry.risk_for(tool, operation)
        # Arguments come from a model or a file, so check their shape before anything reads them.
        problem = self.registry.argument_problem(tool, operation, arguments)
        # Drop any agent-supplied tier unconditionally, then stamp the one the broker resolves. An
        # agent can never set its own account_tier, whether or not the target resolves to a user.
        resolved_args = {k: v for k, v in arguments.items() if k != "account_tier"}
        if problem is None:
            tier = self.registry.resolve_account_tier(resolved_args)
            if tier is not None:
                resolved_args["account_tier"] = tier
        action = ActionRequest(
            session_id=session.session_id,
            tool=tool,
            operation=operation,
            arguments=resolved_args,
            risk_tier=risk,
            summary=message,
        )
        self._event(
            EventType.ACTION_PROPOSED,
            session.session_id,
            action_id=action.action_id,
            tool=tool,
            operation=operation,
            arguments=resolved_args,
            risk=risk.value,
            summary=message,
        )

        if not self.gateway.is_allowed(agent_id):
            return self._blocked(session, agent_id, action)
        if problem is not None:
            return self._denied_rule(
                session, agent_id, action, "invalid-arguments", f"Invalid arguments: {problem}."
            )

        decision = self.policy.evaluate(action)
        self._audit_decision(session, agent_id, action, decision)
        self._event(
            EventType.POLICY_DECISION,
            session.session_id,
            action_id=action.action_id,
            decision=decision.decision.value,
            rule_id=decision.rule_id,
            reason=decision.reason,
        )
        if decision.decision is Decision.DENY:
            return self._denied(session, agent_id, action, decision)

        # Scope: even a write policy would allow may only touch this run's subject, so a steered
        # agent cannot cut a bystander's access. This runs after policy, so the break-glass
        # over-reach is still denied as a privileged-account violation rather than a scope one.
        if self._out_of_subject_scope(session, action):
            return self._denied_rule(
                session, agent_id, action, "scope-offboarding-subject",
                f"{action.arguments.get('user_id')!r} is outside this run's offboarding scope "
                f"(subject {session.subject!r}).",
            )

        approver: str | None = None
        if decision.decision is Decision.REQUIRE_APPROVAL:
            approved, approver, timed_out = await self._await_approval(session, action, agent_id)
            # A revoke ends the wait at once (see revoke), and whatever the human did, a revoked
            # agent's action is recorded as stopped by the revoke, not as a denial or a timeout.
            if not self.gateway.is_allowed(agent_id):
                return self._blocked(session, agent_id, action)
            if not approved:
                return self._denied(
                    session, agent_id, action, decision, approver=approver, timed_out=timed_out
                )

        async with self._lock_for(session.session_id):
            # Re-check after the wait: a revoke during approval must still stop the action here.
            if not self.gateway.is_allowed(agent_id):
                return self._blocked(session, agent_id, action)
            # Commit point. The action has passed the final revocation check under the lock and is
            # about to run. This is the moment the kill switch can no longer stop it: a revoke that
            # lands now cannot recall a call already in flight to an external system. The guarantee
            # the broker enforces is that no action *starts* after a revoke, and this marker is what
            # the invariant measures, so a slow external call completing after a concurrent revoke
            # is not mistaken for a new action started after it.
            self._event(
                EventType.ACTION_COMMITTED,
                session.session_id,
                action_id=action.action_id,
                tool=tool,
                operation=operation,
            )
            # Run the tool off the event loop so a real connector's network call (the GitHub tool)
            # does not block the server. Execution stays serialized by the per-session lock.
            result = await asyncio.to_thread(
                self.registry.execute, tool, operation, resolved_args
            )

        self.audit.append(
            session_id=session.session_id,
            kind=EventType.ACTION_EXECUTED,
            actor=agent_id,
            data={
                "action_id": action.action_id,
                "tool": tool,
                "operation": operation,
                "arguments": resolved_args,
                "approver": approver,
                "ok": result.ok,
                "result": result.data,
                "error": result.error,
            },
        )
        self._event(
            EventType.ACTION_EXECUTED,
            session.session_id,
            action_id=action.action_id,
            ok=result.ok,
            result=result.data,
            error=result.error,
        )
        outcome = ActionOutcome.EXECUTED if result.ok else ActionOutcome.FAILED
        return HandledAction(
            action_id=action.action_id, outcome=outcome, decision=decision, result=result,
            approver=approver,
        )

    async def _await_approval(
        self, session: Session, action: ActionRequest, agent_id: str
    ) -> tuple[bool, str | None, bool]:
        """Pause for a human. Returns (approved, approver, timed_out).

        A human decision resolves the future with (approved, approver), so a deny still records who
        denied. No decision within the timeout returns a timeout, which is a distinct outcome from a
        human saying no.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[action.action_id] = (future, action, agent_id)
        session.status = SessionStatus.WAITING_APPROVAL
        self._event(
            EventType.APPROVAL_REQUESTED,
            session.session_id,
            action_id=action.action_id,
            tool=action.tool,
            operation=action.operation,
            arguments=action.arguments,
            summary=action.summary,
        )
        try:
            approved, approver = await asyncio.wait_for(
                future, timeout=self.approval_timeout_seconds
            )
            return approved, approver, False
        except TimeoutError:
            return False, None, True
        finally:
            self._pending.pop(action.action_id, None)
            if session.status is SessionStatus.WAITING_APPROVAL:
                session.status = SessionStatus.RUNNING

    def resolve_approval(self, action_id: str, *, approved: bool, approver: str) -> bool:
        """Record a human's decision on a pending action. Idempotent; returns whether it applied."""
        entry = self._pending.get(action_id)
        if entry is None:
            return False
        future, action, agent_id = entry
        if future.done():
            return False
        if not approver or approver in {agent_id, action.session_id}:
            # Separation of duties: the agent, by its id or its session, must not approve its own
            # action, and an empty approver is not a decision.
            return False
        # Record the human decision in the tamper-evident log, including who approved or denied.
        self.audit.append(
            session_id=action.session_id,
            kind=EventType.APPROVAL_RESOLVED,
            actor=approver,
            data={
                "action_id": action_id,
                "approved": approved,
                "approver": approver,
                "tool": action.tool,
                "operation": action.operation,
            },
        )
        self._event(
            EventType.APPROVAL_RESOLVED,
            action.session_id,
            action_id=action_id,
            approved=approved,
            approver=approver,
        )
        future.set_result((approved, approver))
        return True

    def revoke(self, session: Session, agent_id: str, actor: str = "operator") -> None:
        """Operator kill switch: flip the revocation state and emit the event.

        The gateway enforcement point sees the new state immediately, so the next action is blocked.
        """
        self.authority.revoke(agent_id, actor=actor)
        self.audit.append(
            session_id=session.session_id,
            kind=EventType.REVOCATION_TRIGGERED,
            actor=actor,
            data={"agent_id": agent_id, "actor": actor},
        )
        self._event(
            EventType.REVOCATION_TRIGGERED,
            session.session_id,
            agent_id=agent_id,
            actor=actor,
        )
        # Release anything this agent has waiting on a human, so the run stops now rather than
        # after the approval timeout.
        for future, _action, pending_agent in list(self._pending.values()):
            if pending_agent == agent_id and not future.done():
                future.set_result((False, None))

    def _audit_decision(
        self, session: Session, agent_id: str, action: ActionRequest, decision: PolicyDecision
    ) -> None:
        self.audit.append(
            session_id=session.session_id,
            kind=EventType.POLICY_DECISION,
            actor=agent_id,
            data={
                "action_id": action.action_id,
                "tool": action.tool,
                "operation": action.operation,
                "arguments": action.arguments,
                "decision": decision.decision.value,
                "rule_id": decision.rule_id,
                "reason": decision.reason,
                "ruleset_version": self.policy.ruleset_version,
            },
        )

    def _denied(
        self,
        session: Session,
        agent_id: str,
        action: ActionRequest,
        decision: PolicyDecision,
        approver: str | None = None,
        timed_out: bool = False,
    ) -> HandledAction:
        # A timeout is its own outcome, distinct from a human or a policy saying no.
        outcome = ActionOutcome.TIMED_OUT if timed_out else ActionOutcome.DENIED
        if timed_out:
            reason = "No operator answered in time, so the action was denied by default."
        elif approver is not None:
            reason = f"Denied by {approver}."
        else:
            reason = decision.reason
        self.audit.append(
            session_id=session.session_id,
            kind=EventType.ACTION_BLOCKED,
            actor=agent_id,
            data={
                "action_id": action.action_id,
                "tool": action.tool,
                "operation": action.operation,
                "outcome": outcome.value,
                "rule_id": decision.rule_id,
                "approver": approver,
                "reason": reason,
            },
        )
        self._event(
            EventType.ACTION_BLOCKED,
            session.session_id,
            action_id=action.action_id,
            outcome=outcome.value,
            reason=reason,
        )
        return HandledAction(
            action_id=action.action_id, outcome=outcome, decision=decision, approver=approver,
            reason=reason,
        )

    def _blocked(self, session: Session, agent_id: str, action: ActionRequest) -> HandledAction:
        self.audit.append(
            session_id=session.session_id,
            kind=EventType.ACTION_BLOCKED,
            actor=agent_id,
            data={
                "action_id": action.action_id,
                "tool": action.tool,
                "operation": action.operation,
                "outcome": ActionOutcome.BLOCKED_REVOKED.value,
            },
        )
        self._event(
            EventType.ACTION_BLOCKED,
            session.session_id,
            action_id=action.action_id,
            outcome=ActionOutcome.BLOCKED_REVOKED.value,
            reason="the agent has been revoked",
        )
        return HandledAction(
            action_id=action.action_id, outcome=ActionOutcome.BLOCKED_REVOKED,
            reason="The agent has been revoked.",
        )

    def _out_of_subject_scope(self, session: Session, action: ActionRequest) -> bool:
        """True when a write targets anyone other than the run's subject. Reads (LOW risk) are
        always allowed, so the agent can still look anyone up; only changes are scoped. The
        subject is matched by every identifier the directory links to them, so removing their
        repository login counts as acting on the subject, and a bystander's login does not."""
        subject = session.subject
        if not subject or action.risk_tier is RiskTier.LOW:
            return False
        target = action.arguments.get("user_id")
        if target is None or target == "":
            return False
        return not isinstance(target, str) or target not in self.registry.identities_of(subject)

    def _denied_rule(
        self, session: Session, agent_id: str, action: ActionRequest, rule_id: str, reason: str
    ) -> HandledAction:
        """A denial the broker makes itself, outside the policy ruleset (scope, malformed input)."""
        self.audit.append(
            session_id=session.session_id,
            kind=EventType.ACTION_BLOCKED,
            actor=agent_id,
            data={
                "action_id": action.action_id,
                "tool": action.tool,
                "operation": action.operation,
                "outcome": ActionOutcome.DENIED.value,
                "rule_id": rule_id,
                "reason": reason,
            },
        )
        self._event(
            EventType.ACTION_BLOCKED,
            session.session_id,
            action_id=action.action_id,
            outcome=ActionOutcome.DENIED.value,
            reason=reason,
        )
        return HandledAction(
            action_id=action.action_id, outcome=ActionOutcome.DENIED, reason=reason
        )
