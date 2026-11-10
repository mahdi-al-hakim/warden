"""An append-only, hash-chained audit log.

Each entry's hash covers its content and the previous hash, so any edit, insertion, or deletion
that does not recompute the whole downstream chain is caught by verify(). A determined attacker who
rewrites every later hash would defeat a plain chain; closing that gap means anchoring the head hash
somewhere outside the log, which is out of scope here and is stated in the threat model.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from ..domain import AuditEvent
from .redact import redact

# A fixed anchor so the first entry has something to chain from.
GENESIS = hashlib.sha256(b"warden-audit-genesis-v1").hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    """The outcome of checking the chain. `first_bad_seq` points at the first entry that failed."""

    ok: bool
    checked: int
    first_bad_seq: int | None = None
    reason: str = ""


class AuditLog:
    """One run's audit trail. In memory here; the same interface would back durable storage."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(
        self,
        *,
        session_id: str,
        kind: str,
        actor: str = "system",
        data: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Record one event. Secrets and personal data are masked before anything is stored."""
        safe = redact(dict(data or {}))
        prev = self._events[-1].hash if self._events else GENESIS
        event = AuditEvent(
            seq=len(self._events),
            session_id=session_id,
            kind=kind,
            actor=actor,
            data=safe,
            prev_hash=prev,
        )
        event.hash = hashlib.sha256(event.content_bytes()).hexdigest()
        self._events.append(event)
        return event

    @property
    def events(self) -> list[AuditEvent]:
        """A copy of the entries, oldest first."""
        return list(self._events)

    def verify(self) -> VerifyResult:
        """Walk the chain and confirm nothing was changed, inserted, or removed."""
        prev = GENESIS
        for index, event in enumerate(self._events):
            if event.seq != index:
                return VerifyResult(False, index, event.seq, "sequence number is out of order")
            if event.prev_hash != prev:
                return VerifyResult(False, index, event.seq, "prev_hash breaks the chain")
            recomputed = hashlib.sha256(event.content_bytes()).hexdigest()
            if recomputed != event.hash:
                return VerifyResult(False, index, event.seq, "hash does not match the content")
            prev = event.hash
        return VerifyResult(True, len(self._events))
