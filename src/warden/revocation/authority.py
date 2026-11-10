"""The revocation authority. It issues short-lived agent credentials and records revocations.

Revoking takes effect here immediately. How quickly each enforcement point acts on it is what the
enforcement module and the seconds-to-stop measurement are about.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

from ..domain import AgentCredential, RevocationRecord, utcnow

Clock = Callable[[], float]


class RevocationAuthority:
    """Holds which agents are revoked, when, and by whom. Notifies a receiver if one is attached."""

    def __init__(self, clock: Clock = time.monotonic) -> None:
        self._clock = clock
        self._revoked: dict[str, RevocationRecord] = {}
        self._revoked_mono: dict[str, float] = {}
        self._receiver = None  # set by attach_receiver; avoids a hard import cycle

    def attach_receiver(self, receiver) -> None:
        """Wire in a CAEP/SSF receiver so a revocation pushes a signal to cached validators."""
        self._receiver = receiver

    def issue(self, agent_id: str, ttl_seconds: float = 3600.0) -> AgentCredential:
        """Mint a credential the agent presents on every call."""
        return AgentCredential(
            agent_id=agent_id,
            expires_at=utcnow() + timedelta(seconds=ttl_seconds),
        )

    def revoke(self, agent_id: str, actor: str = "operator") -> RevocationRecord:
        """Mark the agent revoked now, and push a signal to any attached receiver."""
        record = RevocationRecord(agent_id=agent_id, reason=f"revoked_by:{actor}")
        self._revoked[agent_id] = record
        self._revoked_mono[agent_id] = self._clock()
        if self._receiver is not None:
            self._receiver.on_revoked(agent_id)
        return record

    def is_revoked(self, agent_id: str) -> bool:
        """True once the agent has been revoked. The gateway reads this live on every call."""
        return agent_id in self._revoked

    def revoked_at_mono(self, agent_id: str) -> float | None:
        """The monotonic timestamp of the revocation, for seconds-to-stop math."""
        return self._revoked_mono.get(agent_id)
