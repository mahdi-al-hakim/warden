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
from .verify import verify_offboarding

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

    def verify(self) -> dict:
        """The control plane's own check of the end state, for the run's subject."""
        return verify_offboarding(self.registry, self.session.subject or "")


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
    synthetic: bool = False,
) -> ControlPlane:
    """Build a ready-to-run control plane. `fix_enabled` toggles the CAEP/SSF receiver on or off."""
    # synthetic=True ignores any GitHub settings in the environment (the eval and its tests use it).
    registry = ToolRegistry.synthetic() if synthetic else ToolRegistry()
    registry.clock = clock  # the same clock the revocation authority uses
    _link_subject_login(registry, subject)
    policy = PolicyEngine()
    audit = AuditLog()
    authority = RevocationAuthority(clock=clock)
    authority.add_listener(registry.mark_revoke)
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


def _link_subject_login(registry: ToolRegistry, subject: str | None) -> None:
    """With the real GitHub connector, point the subject's directory record at the test login.

    The connector may only remove logins on its test allowlist, and the setup guide has you create
    one test account to play the departing contractor, so that login is the subject's identity on
    GitHub. Linking it lets the agent find it and lets the scope check accept it.
    """
    from .tools.github_ops import GitHubTool

    repo, directory = registry.get("repo"), registry.get("directory")
    if not subject or not isinstance(repo, GitHubTool) or directory is None:
        return
    logins = sorted(repo.test_logins)
    if logins and hasattr(directory, "link_github_login"):
        directory.link_github_login(subject, logins[0])
