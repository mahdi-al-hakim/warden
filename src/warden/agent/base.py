"""The agent adapter interface.

Given the task and what has happened so far, an adapter proposes the next step or says it is done.
The adapter only proposes; the broker is what decides whether a step runs. Replay and live modes
both implement this interface.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class AgentStep(BaseModel):
    """One move the agent wants to make: call a tool, or stop."""

    kind: Literal["tool", "finish"]
    message: str = ""
    tool: str | None = None
    operation: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """Anything that can drive the run. `source` labels where the steps came from."""

    source: str

    async def propose(self, task: str, history: list[dict[str, Any]]) -> AgentStep: ...
