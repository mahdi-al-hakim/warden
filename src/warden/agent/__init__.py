"""Agent adapters and the governed runner. The live adapter loads from .claude on demand."""

from .base import AgentAdapter, AgentError, AgentStep
from .replay import (
    OFFBOARDING_TASK,
    OFFBOARDING_TRAJECTORY,
    ReplayAgent,
    load_trajectory,
    save_trajectory,
)
from .runner import run_session

__all__ = [
    "OFFBOARDING_TASK",
    "OFFBOARDING_TRAJECTORY",
    "AgentAdapter",
    "AgentError",
    "AgentStep",
    "ReplayAgent",
    "load_trajectory",
    "run_session",
    "save_trajectory",
]
