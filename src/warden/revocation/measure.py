"""Measure how long an agent keeps working after it is revoked, at a given enforcement point.

The measurement carries a positive control: before the revoke it confirms the boundary would have
allowed the action, so the number reflects the boundary stopping the agent rather than an agent that
stopped on its own. Time comes from a monotonic clock. Runs repeat to give a distribution.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from statistics import median
from typing import Protocol

from pydantic import BaseModel

from ..domain import EnforcementPoint
from .authority import RevocationAuthority

Clock = Callable[[], float]
Sleep = Callable[[float], None]


class Enforcer(Protocol):
    """Anything that can say whether an agent may still act, tagged with where it sits."""

    point: EnforcementPoint

    def is_allowed(self, agent_id: str) -> bool: ...


class StopMeasurement(BaseModel):
    """One measured revocation: how long until the boundary first denied the agent."""

    enforcement_point: EnforcementPoint
    positive_control_passed: bool
    seconds_to_stop: float
    attempts: int
    gave_up: bool = False


class StopDistribution(BaseModel):
    """A summary over several trials at one enforcement point."""

    enforcement_point: EnforcementPoint
    trials: int
    median_seconds: float
    min_seconds: float
    max_seconds: float
    all_positive_controls_passed: bool


def measure_seconds_to_stop(
    *,
    enforcer: Enforcer,
    authority: RevocationAuthority,
    agent_id: str,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
    poll_interval: float = 0.05,
    max_wait: float = 30.0,
) -> StopMeasurement:
    """Revoke the agent, then keep attempting until the boundary denies it, timing the gap."""
    point = enforcer.point
    if not enforcer.is_allowed(agent_id):
        # The boundary denies before any revoke, so there is no real stop to measure.
        return StopMeasurement(
            enforcement_point=point,
            positive_control_passed=False,
            seconds_to_stop=-1.0,
            attempts=0,
        )
    start = clock()
    authority.revoke(agent_id, actor="operator")
    attempts = 0
    while True:
        attempts += 1
        if not enforcer.is_allowed(agent_id):
            return StopMeasurement(
                enforcement_point=point,
                positive_control_passed=True,
                seconds_to_stop=clock() - start,
                attempts=attempts,
            )
        if clock() - start >= max_wait:
            return StopMeasurement(
                enforcement_point=point,
                positive_control_passed=True,
                seconds_to_stop=clock() - start,
                attempts=attempts,
                gave_up=True,
            )
        sleep(poll_interval)


def summarize(measurements: list[StopMeasurement]) -> StopDistribution:
    """Fold several trials at one enforcement point into a median and a range.

    Only trials whose positive control passed are counted: a trial where the boundary would have
    denied the agent anyway produced no real stop (its seconds_to_stop is the -1.0 sentinel), and
    folding that into the statistics would understate the time to stop. A failed control is still
    reflected in all_positive_controls_passed so it is never silently dropped.
    """
    if not measurements:
        raise ValueError("need at least one measurement to summarize")
    counted = [m.seconds_to_stop for m in measurements if m.positive_control_passed]
    all_ok = all(m.positive_control_passed for m in measurements)
    if not counted:
        # No trial produced a real stop, so there is nothing to average. Report zeros and let
        # all_positive_controls_passed carry the failure rather than invent a number.
        return StopDistribution(
            enforcement_point=measurements[0].enforcement_point,
            trials=len(measurements),
            median_seconds=0.0,
            min_seconds=0.0,
            max_seconds=0.0,
            all_positive_controls_passed=all_ok,
        )
    return StopDistribution(
        enforcement_point=measurements[0].enforcement_point,
        trials=len(measurements),
        median_seconds=median(counted),
        min_seconds=min(counted),
        max_seconds=max(counted),
        all_positive_controls_passed=all_ok,
    )
