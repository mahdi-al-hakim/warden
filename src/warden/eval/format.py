"""Render an evaluation report as readable text for the terminal and the demo notes."""

from __future__ import annotations

from .report import EvalReport


def format_report(report: EvalReport) -> str:
    """A compact, human-readable summary of the scenarios and the revocation benchmark."""
    lines: list[str] = []
    lines.append("Warden reliability evaluation")
    lines.append(f"generated {report.generated_at}")
    lines.append("")
    lines.append(
        f"Governance scenarios: {report.scenarios_passed}/{report.scenarios_total} passed, "
        f"invariants {report.invariants_passed}/{report.invariants_total} held"
    )
    lines.append("")
    for scenario in report.scenarios:
        mark = "PASS" if scenario.passed else "FAIL"
        lines.append(f"[{mark}] {scenario.scenario} ({scenario.final_status})")
        lines.append(f"       {scenario.description}")
        if scenario.outcome_counts:
            counts = ", ".join(f"{k}={v}" for k, v in sorted(scenario.outcome_counts.items()))
            lines.append(f"       actions: {counts}")
        for inv in scenario.invariants:
            if not inv.passed:
                lines.append(f"       - FAILED {inv.name}: {inv.detail}")
    lines.append("")
    lines.append("Revocation benchmark (seconds from revoke to first denied action)")
    bench = report.benchmark
    lines.append(
        f"cache TTL {bench.cache_ttl_seconds:g}s, signal latency {bench.signal_latency_seconds:g}s"
    )
    header = f"  {'enforcement point':34} {'median':>8} {'p95':>8} {'worst':>8}"
    lines.append(header)
    for row in bench.rows:
        lines.append(
            f"  {row.label:34} {row.median_seconds:>8.3f} "
            f"{row.p95_seconds:>8.3f} {row.max_seconds:>8.3f}"
        )
    lines.append("")
    verdict = "all checks passed" if report.all_passed else "SOME CHECKS FAILED"
    lines.append(verdict)
    return "\n".join(lines)
