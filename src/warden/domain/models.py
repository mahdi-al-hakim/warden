"""Core domain models. Pure data and enums, with no I/O and no dependency on other Warden modules.

Everything else speaks this vocabulary: an agent proposes an ActionRequest, policy returns a
PolicyDecision, the tool returns a ToolResult, and the broker records an AuditEvent.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    """Timezone-aware current time in UTC, so every timestamp is directly comparable."""
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """A short, readable, unique id with a type prefix, for example 'act_9f3a2b1c4d5e'."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Decision(StrEnum):
    """What the policy engine says to do with a proposed action."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class RiskTier(StrEnum):
    """How much a tool operation can hurt if it is wrong. Drives whether a human is asked."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EnforcementPoint(StrEnum):
    """Where a revocation is, or is not, honored. The revocation layer measures each one.

    GATEWAY checks the agent's credential live on every call, so it stops a revoked agent at once.
    OFFLINE_VALIDATOR validates against a cached grant set with a TTL, so without a push signal it
    keeps honoring a revoked credential until the cache expires. That gap is what the fix closes.
    """

    GATEWAY = "gateway"
    OFFLINE_VALIDATOR = "offline_validator"


class SessionStatus(StrEnum):
    """Lifecycle of one agent run."""

    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    REVOKED = "revoked"
    FAILED = "failed"


class ActionOutcome(StrEnum):
    """The final disposition of one proposed action after it passes through the broker."""

    EXECUTED = "executed"
    DENIED = "denied"
    BLOCKED_REVOKED = "blocked_revoked"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class ActionRequest(BaseModel):
    """One thing the agent wants to do: call a tool operation with some arguments.

    The agent never runs this itself. It hands the request to the broker, which decides and acts.
    """

    model_config = ConfigDict(frozen=True)

    action_id: str = Field(default_factory=lambda: new_id("act"))
    session_id: str
    tool: str
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_tier: RiskTier = RiskTier.LOW
    summary: str = ""
    proposed_at: datetime = Field(default_factory=utcnow)


class PolicyDecision(BaseModel):
    """The policy engine's answer for one action, with the rule and reason that produced it."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    rule_id: str
    reason: str


class ToolResult(BaseModel):
    """What a tool returns. `ok` is the mechanical ground truth an evaluation can check."""

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float = 0.0


class AgentCredential(BaseModel):
    """A short-lived credential the agent presents on every call. Revocation targets this."""

    credential_id: str = Field(default_factory=lambda: new_id("cred"))
    agent_id: str
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class RevocationRecord(BaseModel):
    """A single revocation event for an agent, and the signal id that carried it to receivers."""

    agent_id: str
    revoked_at: datetime = Field(default_factory=utcnow)
    reason: str = "operator_revoked"
    signal_id: str = Field(default_factory=lambda: new_id("sig"))


class Session(BaseModel):
    """One governed agent run."""

    session_id: str = Field(default_factory=lambda: new_id("ses"))
    task: str
    agent_id: str
    mode: str = "replay"
    status: SessionStatus = SessionStatus.RUNNING
    started_at: datetime = Field(default_factory=utcnow)
    # The identity this run is authorized to change (the offboarding target). A write to any other
    # user is out of scope and is denied, so a steered agent cannot touch a bystander's access.
    subject: str | None = None


class AuditEvent(BaseModel):
    """One entry in the append-only, hash-chained audit log.

    `hash` covers this event's content and `prev_hash`, so any edit, insert or delete breaks the
    chain. The log module computes and checks the hashes; the model only serializes its content.
    """

    schema_version: str = "1"
    seq: int
    ts: datetime = Field(default_factory=utcnow)
    session_id: str
    kind: str
    actor: str = "system"
    data: dict[str, Any] = Field(default_factory=dict)
    prev_hash: str = ""
    hash: str = ""

    def content_bytes(self) -> bytes:
        """Canonical bytes of everything except the hash field, for hashing and verification.

        Keys are sorted and the timestamp is ISO 8601, so an event always serializes the same way.
        schema_version is covered, so a format change cannot silently pass an old verification.
        """
        payload = {
            "schema_version": self.schema_version,
            "seq": self.seq,
            "ts": self.ts.isoformat(),
            "session_id": self.session_id,
            "kind": self.kind,
            "actor": self.actor,
            "data": self.data,
            "prev_hash": self.prev_hash,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
