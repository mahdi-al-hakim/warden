"""Wire the whole control plane together for one run.

One function builds every piece and wires them: the tool registry, policy, audit log, revocation
authority, the two enforcement points, the signal receiver (the fix, toggleable), and the broker.
The API, the demo script, and the tests all build a control plane the same way.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .audit import AuditLog
from .domain import Session, StreamEvent
from .gateway import Broker
from .policy import PolicyEngine
from .revocation import (
    GatewayEnforcer,
    OfflineValidator,
    RevocationAuthority,
    SignalReceiver,
)
from .tools import ToolRegistry

Emit = Callable[[StreamEvent], None]
Clock = Callable[[], float]


@dataclass
class ControlPlane:
    """Every assembled piece, so a caller can run the agent and also read or drive the parts."""

    registry: ToolRegistry
    policy: PolicyEngine
    audit: AuditLog
    authority: RevocationAuthority
    gateway: GatewayEnforcer
    offline: OfflineValidator
    receiver: SignalReceiver
    broker: Broker
    session: Session
    agent_id: str


def build_control_plane(
    *,
    task: str = "",
    agent_id: str = "agent-offboarding",
    subject: str | None = "jordan.lee",
    mode: str = "replay",
    fix_enabled: bool = True,
    offline_ttl_seconds: float = 15.0,
    signal_latency_seconds: float = 0.3,
    approval_timeout_seconds: float = 120.0,
    emit: Emit | None = None,
    clock: Clock = time.monotonic,
) -> ControlPlane:
    """Build a ready-to-run control plane. `fix_enabled` toggles the CAEP/SSF receiver on or off."""
    registry = ToolRegistry()
    policy = PolicyEngine()
    audit = AuditLog()
    authority = RevocationAuthority(clock=clock)
    gateway = GatewayEnforcer(authority)
    offline = OfflineValidator(cache_ttl_seconds=offline_ttl_seconds, clock=clock)
    offline.grant(agent_id)
    receiver = SignalReceiver(
        offline,
        enabled=fix_enabled,
        delivery_latency_seconds=signal_latency_seconds,
        clock=clock,
    )
    authority.attach_receiver(receiver)
    broker = Broker(
        registry=registry,
        policy=policy,
        audit=audit,
        authority=authority,
        gateway=gateway,
        approval_timeout_seconds=approval_timeout_seconds,
        emit=emit,
    )
    # The whole demo is the Jordan Lee offboarding, so the run is scoped to that subject by default.
    session = Session(task=task, agent_id=agent_id, mode=mode, subject=subject)
    return ControlPlane(
        registry=registry,
        policy=policy,
        audit=audit,
        authority=authority,
        gateway=gateway,
        offline=offline,
        receiver=receiver,
        broker=broker,
        session=session,
        agent_id=agent_id,
    )
