"""The revocation benchmark: how long each enforcement point keeps honoring a revoked agent.

Time is simulated, so the benchmark is deterministic and runs instantly. A revoke can land at any
point in a cached credential's lifetime, so each trial ages the cache by a different amount before
revoking. The gateway checks live and stops on the next call. The offline validator keeps honoring
the cached grant until its TTL, unless a CAEP/SSF signal invalidates the cache first. That gap, and
how the signal closes it, is the point of the measurement.
"""

from __future__ import annotations

from ..domain import EnforcementPoint
from ..revocation import (
    GatewayEnforcer,
    OfflineValidator,
    RevocationAuthority,
    SignalReceiver,
    StopMeasurement,
    measure_seconds_to_stop,
)
from .report import BenchmarkReport, BenchmarkRow


class _FakeClock:
    """A clock that moves only when sleep advances it, so each measurement is exact and instant.

    It counts whole microseconds, so stepping it in 0.01 s increments does not accumulate the float
    rounding that would otherwise register a stop one step late.
    """

    def __init__(self) -> None:
        self._us = 0

    def __call__(self) -> float:
        return self._us / 1_000_000

    def advance(self, dt: float) -> None:
        self._us += round(dt * 1_000_000)


def _percentile(ordered: list[float], pct: float) -> float:
    if not ordered:
        return 0.0
    k = (len(ordered) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def _summarize_row(
    label: str, point: EnforcementPoint, runs: list[StopMeasurement]
) -> BenchmarkRow:
    # Only count trials whose positive control passed: a trial where the boundary would have
    # denied anyway carries the -1.0 sentinel, and averaging that in would understate the stop
    # time. A failed control still shows up in positive_controls_passed.
    ordered = sorted(m.seconds_to_stop for m in runs if m.positive_control_passed)
    all_ok = all(m.positive_control_passed for m in runs)
    if not ordered:
        return BenchmarkRow(
            label=label,
            enforcement_point=point.value,
            trials=len(runs),
            median_seconds=0.0,
            min_seconds=0.0,
            max_seconds=0.0,
            p95_seconds=0.0,
            positive_controls_passed=all_ok,
        )
    return BenchmarkRow(
        label=label,
        enforcement_point=point.value,
        trials=len(runs),
        median_seconds=round(_percentile(ordered, 0.5), 4),
        min_seconds=round(ordered[0], 4),
        max_seconds=round(ordered[-1], 4),
        p95_seconds=round(_percentile(ordered, 0.95), 4),
        positive_controls_passed=all_ok,
    )


def _gateway_trial(agent_id: str, age: float) -> StopMeasurement:
    clock = _FakeClock()
    authority = RevocationAuthority(clock=clock)
    gateway = GatewayEnforcer(authority)
    clock.advance(age)
    return measure_seconds_to_stop(
        enforcer=gateway, authority=authority, agent_id=agent_id,
        clock=clock, sleep=clock.advance, poll_interval=0.01, max_wait=60.0,
    )


def _offline_trial(
    agent_id: str, age: float, ttl: float, *, fix: bool, latency: float
) -> StopMeasurement:
    clock = _FakeClock()
    authority = RevocationAuthority(clock=clock)
    offline = OfflineValidator(cache_ttl_seconds=ttl, clock=clock)
    offline.grant(agent_id)
    receiver = SignalReceiver(offline, enabled=fix, delivery_latency_seconds=latency, clock=clock)
    authority.attach_receiver(receiver)
    clock.advance(age)
    return measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id=agent_id,
        clock=clock, sleep=clock.advance, poll_interval=0.01, max_wait=ttl * 2 + 5,
    )


def run_benchmark(
    *, trials: int = 25, cache_ttl_seconds: float = 15.0, signal_latency_seconds: float = 0.3
) -> BenchmarkReport:
    """Run the three enforcement points across evenly spaced revoke moments and summarize each."""
    agent_id = "agent-offboarding"
    # Spread the revoke moment evenly across the cached lifetime, one per trial.
    ages = [cache_ttl_seconds * (i + 0.5) / trials for i in range(trials)]
    gateway = [_gateway_trial(agent_id, age) for age in ages]
    offline_nofix = [
        _offline_trial(agent_id, age, cache_ttl_seconds, fix=False, latency=signal_latency_seconds)
        for age in ages
    ]
    offline_fix = [
        _offline_trial(agent_id, age, cache_ttl_seconds, fix=True, latency=signal_latency_seconds)
        for age in ages
    ]
    return BenchmarkReport(
        cache_ttl_seconds=cache_ttl_seconds,
        signal_latency_seconds=signal_latency_seconds,
        rows=[
            _summarize_row("Gateway, live check", EnforcementPoint.GATEWAY, gateway),
            _summarize_row(
                "Offline validator, no signal", EnforcementPoint.OFFLINE_VALIDATOR, offline_nofix
            ),
            _summarize_row(
                "Offline validator, CAEP-style signal",
                EnforcementPoint.OFFLINE_VALIDATOR,
                offline_fix,
            ),
        ],
    )
