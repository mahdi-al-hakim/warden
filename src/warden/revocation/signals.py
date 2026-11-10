"""A CAEP/SSF-style receiver. The fix: it turns a revocation into an immediate cache invalidation.

When enabled, a revocation pushes a session-revoked signal here, and the receiver clears the offline
validator's cached grant after a short delay. When disabled, no signal arrives and the offline
validator keeps honoring the grant until its TTL. Toggling this is the before and after in the demo.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .enforcement import OfflineValidator

Clock = Callable[[], float]


class SignalReceiver:
    """Consumes revocation signals and invalidates a cached grant at the offline validator."""

    def __init__(
        self,
        offline_validator: OfflineValidator,
        *,
        enabled: bool = True,
        delivery_latency_seconds: float = 0.2,
        clock: Clock = time.monotonic,
    ) -> None:
        self._offline = offline_validator
        self.enabled = enabled
        self.delivery_latency_seconds = delivery_latency_seconds
        self._clock = clock
        self.signals_received = 0

    def on_revoked(self, agent_id: str) -> None:
        """Called by the authority on revoke. Invalidates the cache after the delivery latency."""
        if not self.enabled:
            return
        self.signals_received += 1
        effective_at = self._clock() + self.delivery_latency_seconds
        self._offline.invalidate(agent_id, effective_at)
