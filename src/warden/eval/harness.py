"""Run the scenarios, check invariants against each, run the benchmark, fold it into a report."""

from __future__ import annotations

import asyncio

from ..domain import EventType, utcnow
from .benchmark import run_benchmark
from .invariants import (
    check_all,
    offboarding_completed,
    read_back_shows_work_left,
)
from .report import EvalReport, InvariantResult, ScenarioResult
from .scenarios import ALL_SCENARIOS, ScenarioRun


def _outcome_counts(run: ScenarioRun) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in run.events:
        if event.type == EventType.ACTION_EXECUTED:
            # A failed tool call still emits this event, so split it out by the ok flag.
            key = "executed" if event.data.get("ok", True) else "failed"
        elif event.type == EventType.ACTION_BLOCKED:
            key = str(event.data.get("outcome", "blocked"))
        else:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def evaluate_run(run: ScenarioRun) -> ScenarioResult:
    """Check every invariant against one finished scenario run."""
    invariants = check_all(run.events, run.cp)
    # Every run is read back from the systems. A run meant to finish the job must show it done; any
    # other run must still show work left, so a read-back that always said "done" would fail here.
    if run.expects_completion:
        invariants.append(offboarding_completed(run.events, run.cp))
    else:
        invariants.append(read_back_shows_work_left(run.events, run.cp))
    counts = _outcome_counts(run)
    if run.expected_outcomes is not None:
        invariants.append(InvariantResult(
            name="expected_outcomes",
            passed=counts == run.expected_outcomes,
            detail="" if counts == run.expected_outcomes
            else f"expected {run.expected_outcomes}, got {counts}",
        ))
    executed = sum(1 for e in run.events if e.type == EventType.ACTION_EXECUTED)
    return ScenarioResult(
        scenario=run.name,
        description=run.description,
        passed=all(i.passed for i in invariants),
        final_status=run.session.status.value,
        steps_executed=executed,
        outcome_counts=counts,
        invariants=invariants,
    )


async def run_scenarios() -> list[ScenarioResult]:
    """Run every scenario in order and evaluate each. Order matters only for readable output."""
    results: list[ScenarioResult] = []
    for scenario in ALL_SCENARIOS:
        results.append(evaluate_run(await scenario()))
    return results


async def build_report(*, benchmark_trials: int = 25) -> EvalReport:
    """The async core: run the governance scenarios and the revocation benchmark into one report."""
    scenarios = await run_scenarios()
    benchmark = run_benchmark(trials=benchmark_trials)
    invariants_total = sum(len(s.invariants) for s in scenarios)
    invariants_passed = sum(1 for s in scenarios for i in s.invariants if i.passed)
    passed = sum(1 for s in scenarios if s.passed)
    # The benchmark is part of the verdict: a row whose positive control failed is not evidence of
    # anything, so the whole eval fails if any row's control did not hold.
    benchmark_ok = all(row.positive_controls_passed for row in benchmark.rows)
    return EvalReport(
        scenarios=scenarios,
        benchmark=benchmark,
        scenarios_passed=passed,
        scenarios_total=len(scenarios),
        invariants_passed=invariants_passed,
        invariants_total=invariants_total,
        all_passed=passed == len(scenarios) and benchmark_ok,
        generated_at=utcnow().isoformat(),
    )


def run_evaluation(*, benchmark_trials: int = 25) -> EvalReport:
    """Sync wrapper for command-line use. Do not call from inside a running event loop."""
    return asyncio.run(build_report(benchmark_trials=benchmark_trials))
