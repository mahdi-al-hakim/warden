"""Events streamed to the UI as a run unfolds, one per step, so the screen shows what happened."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .models import utcnow


class EventType:
    """Names for every event the control plane emits. The UI switches on these."""

    SESSION_STARTED = "session.started"
    AGENT_MESSAGE = "agent.message"
    ACTION_PROPOSED = "action.proposed"
    POLICY_DECISION = "policy.decision"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_RESOLVED = "approval.resolved"
    ACTION_COMMITTED = "action.committed"
    ACTION_EXECUTED = "action.executed"
    ACTION_BLOCKED = "action.blocked"
    AUDIT_APPENDED = "audit.appended"
    REVOCATION_TRIGGERED = "revocation.triggered"
    ENFORCEMENT_MEASURED = "enforcement.measured"
    SESSION_COMPLETED = "session.completed"
    ERROR = "error"


class StreamEvent(BaseModel):
    """A single thing that happened, tagged with its type and the session it belongs to."""

    type: str
    session_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=utcnow)
