"""Tests for the revocation layer: the gateway stops at once, the offline validator has a gap, and
the signal receiver closes it. A fake clock drives time so the tests are instant and exact.
"""

from __future__ import annotations

from warden.revocation import (
    GatewayEnforcer,
    OfflineValidator,
    RevocationAuthority,
    SignalReceiver,
    measure_seconds_to_stop,
    summarize,
)


class FakeClock:
    """A controllable monotonic clock. sleep() advances it instead of waiting."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, dt: float) -> None:
        self.t += dt


def test_gateway_stops_immediately():
    clock = FakeClock()
    authority = RevocationAuthority(clock=clock)
    gateway = GatewayEnforcer(authority)
    m = measure_seconds_to_stop(
        enforcer=gateway, authority=authority, agent_id="agent_x",
        clock=clock, sleep=clock.sleep, poll_interval=0.1, max_wait=5.0,
    )
    assert m.positive_control_passed is True
    assert m.seconds_to_stop == 0.0
    assert m.attempts == 1
    assert m.gave_up is False


def test_offline_validator_keeps_working_until_ttl_without_a_signal():
    clock = FakeClock()
    authority = RevocationAuthority(clock=clock)
    offline = OfflineValidator(cache_ttl_seconds=2.0, clock=clock)
    offline.grant("agent_x")
    # No receiver attached, so a revoke never reaches the cache. The agent keeps working to the TTL.
    m = measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id="agent_x",
        clock=clock, sleep=clock.sleep, poll_interval=0.1, max_wait=10.0,
    )
    assert m.positive_control_passed is True
    assert 1.9 <= m.seconds_to_stop <= 2.1
    assert m.gave_up is False


def test_signal_receiver_closes_the_gap():
    clock = FakeClock()
    authority = RevocationAuthority(clock=clock)
    offline = OfflineValidator(cache_ttl_seconds=2.0, clock=clock)
    offline.grant("agent_x")
    receiver = SignalReceiver(offline, enabled=True, delivery_latency_seconds=0.2, clock=clock)
    authority.attach_receiver(receiver)
    m = measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id="agent_x",
        clock=clock, sleep=clock.sleep, poll_interval=0.1, max_wait=10.0,
    )
    assert m.positive_control_passed is True
    assert 0.15 <= m.seconds_to_stop <= 0.35
    assert receiver.signals_received == 1


def test_gap_is_much_larger_than_the_fixed_latency():
    # The point of the fix, stated as a test: the gap equals the TTL, the fix brings it near zero.
    clock = FakeClock()
    authority = RevocationAuthority(clock=clock)
    offline = OfflineValidator(cache_ttl_seconds=5.0, clock=clock)
    offline.grant("agent_x")
    gap = measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id="agent_x",
        clock=clock, sleep=clock.sleep, poll_interval=0.1, max_wait=20.0,
    )

    clock2 = FakeClock()
    authority2 = RevocationAuthority(clock=clock2)
    offline2 = OfflineValidator(cache_ttl_seconds=5.0, clock=clock2)
    offline2.grant("agent_y")
    receiver2 = SignalReceiver(offline2, enabled=True, delivery_latency_seconds=0.2, clock=clock2)
    authority2.attach_receiver(receiver2)
    fixed = measure_seconds_to_stop(
        enforcer=offline2, authority=authority2, agent_id="agent_y",
        clock=clock2, sleep=clock2.sleep, poll_interval=0.1, max_wait=20.0,
    )
    assert gap.seconds_to_stop > 10 * fixed.seconds_to_stop


def test_positive_control_fails_when_boundary_already_denies():
    clock = FakeClock()
    authority = RevocationAuthority(clock=clock)
    offline = OfflineValidator(cache_ttl_seconds=2.0, clock=clock)
    # No grant was cached, so the validator denies from the start. There is no real stop to measure.
    m = measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id="never_granted",
        clock=clock, sleep=clock.sleep,
    )
    assert m.positive_control_passed is False
    assert m.seconds_to_stop == -1.0


def test_disabled_receiver_leaves_the_gap_open():
    clock = FakeClock()
    authority = RevocationAuthority(clock=clock)
    offline = OfflineValidator(cache_ttl_seconds=2.0, clock=clock)
    offline.grant("agent_x")
    receiver = SignalReceiver(offline, enabled=False, clock=clock)
    authority.attach_receiver(receiver)
    m = measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id="agent_x",
        clock=clock, sleep=clock.sleep, poll_interval=0.1, max_wait=10.0,
    )
    assert 1.9 <= m.seconds_to_stop <= 2.1
    assert receiver.signals_received == 0


def test_summarize_reports_median_and_range():
    clock = FakeClock()
    measurements = []
    for _ in range(3):
        authority = RevocationAuthority(clock=clock)
        gw = GatewayEnforcer(authority)
        measurements.append(
            measure_seconds_to_stop(
                enforcer=gw, authority=authority, agent_id=f"a_{_}",
                clock=clock, sleep=clock.sleep,
            )
        )
    dist = summarize(measurements)
    assert dist.trials == 3
    assert dist.all_positive_controls_passed is True
    assert dist.min_seconds <= dist.median_seconds <= dist.max_seconds


def test_seconds_to_stop_holds_on_a_real_wall_clock():
    # The benchmark uses a fake clock for speed and determinism. This test runs the same enforcement
    # code on the real monotonic clock, at a small scale, so the numbers are observed, not computed.
    # It confirms the modeled shape matches real behavior: no signal stops near the TTL, a signal
    # stops near the delivery latency.
    # No signal: the offline validator keeps a granted credential until the cache lifetime ends.
    offline_ns = OfflineValidator(cache_ttl_seconds=0.25)
    offline_ns.grant("agent_real")
    no_signal = measure_seconds_to_stop(
        enforcer=offline_ns, authority=RevocationAuthority(), agent_id="agent_real",
        poll_interval=0.005, max_wait=3.0,
    )
    assert no_signal.positive_control_passed is True
    # It stops, well after zero and near the cache lifetime, not at the max_wait ceiling.
    assert 0.1 < no_signal.seconds_to_stop < 1.0
    assert no_signal.gave_up is False

    # With a signal: a receiver invalidates the cache on revoke, so it stops near the latency.
    authority = RevocationAuthority()
    offline = OfflineValidator(cache_ttl_seconds=2.0)
    offline.grant("agent_real")
    authority.attach_receiver(
        SignalReceiver(offline, enabled=True, delivery_latency_seconds=0.05)
    )
    with_signal = measure_seconds_to_stop(
        enforcer=offline, authority=authority, agent_id="agent_real",
        poll_interval=0.005, max_wait=3.0,
    )
    assert with_signal.positive_control_passed is True
    # The signal, not the two second lifetime, is what stopped it.
    assert with_signal.seconds_to_stop < 0.5
