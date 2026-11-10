"""Result types for the reliability evaluation. Plain data the API and the UI can render."""

from __future__ import annotations

from pydantic import BaseModel, Field


class InvariantResult(BaseModel):
    """One governance property checked against one run."""

    name: str
    passed: bool
    detail: str = ""


class ScenarioResult(BaseModel):
    """One scenario run and the invariants checked against it."""

    scenario: str
    description: str
    passed: bool
    final_status: str = ""
    steps_executed: int = 0
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    invariants: list[InvariantResult] = Field(default_factory=list)


class BenchmarkRow(BaseModel):
    """Seconds-to-stop at one enforcement point, summarized over several trials."""

    label: str
    enforcement_point: str
    trials: int
    median_seconds: float
    min_seconds: float
    max_seconds: float
    p95_seconds: float
    positive_controls_passed: bool


class BenchmarkReport(BaseModel):
    """The revocation benchmark: how fast each enforcement point stops a revoked agent."""

    cache_ttl_seconds: float
    signal_latency_seconds: float
    rows: list[BenchmarkRow] = Field(default_factory=list)


class EvalReport(BaseModel):
    """The whole evaluation: governance scenarios plus the revocation benchmark."""

    scenarios: list[ScenarioResult] = Field(default_factory=list)
    benchmark: BenchmarkReport
    scenarios_passed: int = 0
    scenarios_total: int = 0
    invariants_passed: int = 0
    invariants_total: int = 0
    all_passed: bool = False
    generated_at: str = ""
