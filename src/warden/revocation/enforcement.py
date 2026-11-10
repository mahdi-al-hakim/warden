"""The two enforcement points, and why they behave differently on a revocation.

GATEWAY asks the authority live on every call, so it stops a revoked agent at once.
OFFLINE_VALIDATOR trusts a cached grant until its TTL expires. Without a push signal it keeps
honoring a revoked credential for the rest of that lifetime. That window is the gap the fix closes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..domain import EnforcementPoint
from .authority import RevocationAuthority

Clock = Callable[[], float]


class GatewayEnforcer:
    """Checks the credential live on every call, so a revocation is honored at once."""

    point = EnforcementPoint.GATEWAY

    def __init__(self, authority: RevocationAuthority) -> None:
        self._authority = authority

    def is_allowed(self, agent_id: str) -> bool:
        return not self._authority.is_revoked(agent_id)


@dataclass
class _Grant:
    expires_at_mono: float
    invalidated_at_mono: float | None = None


class OfflineValidator:
    """Validates against a cached grant with a TTL, the way an offline JWT check would.

    It never calls the authority. It learns of a revocation only if a signal invalidates its
    cache. Until then it keeps saying yes, up to the full cache lifetime.
    """

    point = EnforcementPoint.OFFLINE_VALIDATOR

    def __init__(self, cache_ttl_seconds: float, clock: Clock = time.monotonic) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self._clock = clock
        self._grants: dict[str, _Grant] = {}

    def grant(self, agent_id: str) -> None:
        """Cache a fresh grant, as if the agent just presented a valid token."""
        self._grants[agent_id] = _Grant(expires_at_mono=self._clock() + self.cache_ttl_seconds)

    def invalidate(self, agent_id: str, effective_at_mono: float) -> None:
        """Mark the cached grant dead from a given moment. Called by the signal receiver."""
        grant = self._grants.get(agent_id)
        if grant is not None:
            grant.invalidated_at_mono = effective_at_mono

    def is_allowed(self, agent_id: str) -> bool:
        grant = self._grants.get(agent_id)
        if grant is None:
            return False
        now = self._clock()
        if now >= grant.expires_at_mono:
            return False
        if grant.invalidated_at_mono is not None and now >= grant.invalidated_at_mono:
            return False
        return True
