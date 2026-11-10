"""Tool abstraction. A tool exposes named operations, each with a fixed risk tier and an arg schema.

Each operation has a fixed risk tier set here, so the agent's request cannot lower it. The broker
reads the risk straight off the tool spec when it stamps a proposed action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain import RiskTier, ToolResult


@dataclass(frozen=True)
class Operation:
    """One callable operation on a tool."""

    name: str
    risk: RiskTier
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()


class Tool:
    """Base class. A concrete tool sets `name`, fills `operations`, and implements `run`."""

    name: str = "tool"

    def __init__(self) -> None:
        self.operations: dict[str, Operation] = {}
        self._failing: set[str] = set()

    def run(self, operation: str, arguments: dict[str, Any]) -> ToolResult:
        """Execute one operation. Concrete tools override this."""
        raise NotImplementedError

    def inject_failure(self, operation: str) -> None:
        """Make an operation return an error, so the graceful-fallback path can be exercised."""
        self._failing.add(operation)

    def _maybe_fail(self, operation: str) -> ToolResult | None:
        """Return a simulated upstream error if this operation was told to fail, else None."""
        if operation in self._failing:
            return ToolResult(ok=False, error="simulated upstream failure")
        return None

    def unknown_operation(self, operation: str) -> ToolResult:
        """A clean, uniform error when an operation does not exist on this tool."""
        return ToolResult(ok=False, error=f"unknown operation {operation!r} on tool {self.name!r}")
