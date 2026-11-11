"""Drive an agent through the broker until it finishes, is revoked, fails, or hits the step limit.

Every way a run can end emits one session-completed event. When a verifier is supplied, that event
also carries the control plane's own check of the end state, so what the agent says it did and what
the systems show it did arrive side by side.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..domain import ActionOutcome, EventType, Session, SessionStatus, StreamEvent
from ..gateway import Broker
from .base import AgentAdapter, AgentError

Emit = Callable[[StreamEvent], None]
Verify = Callable[[], dict[str, Any]]


async def run_session(
    *,
    agent: AgentAdapter,
    broker: Broker,
    session: Session,
    task: str,
    emit: Emit | None = None,
    max_steps: int = 25,
    verify: Verify | None = None,
) -> Session:
    """Loop: ask the agent for a step, run it through the broker, feed the result back, repeat."""
    send = emit or (lambda event: None)

    async def finish(message: str, **extra: Any) -> None:
        data: dict[str, Any] = {"message": message, **extra}
        if verify is not None:
            data["verified"] = await asyncio.to_thread(verify)
        send(
            StreamEvent(
                type=EventType.SESSION_COMPLETED, session_id=session.session_id, data=data
            )
        )

    send(
        StreamEvent(
            type=EventType.SESSION_STARTED,
            session_id=session.session_id,
            data={
                "task": task,
                "agent_id": session.agent_id,
                "mode": session.mode,
                "source": getattr(agent, "source", "unknown"),
            },
        )
    )
    history: list[dict] = []
    for _ in range(max_steps):
        try:
            step = await agent.propose(task, history)
        except AgentError as exc:
            session.status = SessionStatus.FAILED
            await finish(f"Run stopped: {exc}", failed=True)
            return session
        if step.kind == "finish":
            session.status = SessionStatus.COMPLETED
            await finish(step.message)
            return session

        send(
            StreamEvent(
                type=EventType.AGENT_MESSAGE,
                session_id=session.session_id,
                data={"message": step.message, "tool": step.tool, "operation": step.operation},
            )
        )
        handled = await broker.handle_action(
            session=session,
            agent_id=session.agent_id,
            tool=step.tool or "",
            operation=step.operation or "",
            arguments=step.arguments,
            message=step.message,
        )
        history.append(
            {
                "tool": step.tool,
                "operation": step.operation,
                "arguments": step.arguments,
                "outcome": handled.outcome.value,
                "result": handled.result.data if handled.result else None,
                "error": handled.result.error if handled.result else None,
            }
        )
        if handled.outcome is ActionOutcome.BLOCKED_REVOKED:
            session.status = SessionStatus.REVOKED
            await finish("Run stopped: the agent was revoked.", revoked=True)
            return session

    # The agent never said it was done, so this is a failure to finish, not a completion.
    session.status = SessionStatus.FAILED
    await finish(f"Run stopped: the agent reached the {max_steps}-step limit.", failed=True)
    return session
