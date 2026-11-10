"""Drive an agent through the broker until it finishes, is revoked, or hits the step limit."""

from __future__ import annotations

from collections.abc import Callable

from ..domain import ActionOutcome, EventType, Session, SessionStatus, StreamEvent
from ..gateway import Broker
from .base import AgentAdapter

Emit = Callable[[StreamEvent], None]


async def run_session(
    *,
    agent: AgentAdapter,
    broker: Broker,
    session: Session,
    task: str,
    emit: Emit | None = None,
    max_steps: int = 25,
) -> Session:
    """Loop: ask the agent for a step, run it through the broker, feed the result back, repeat."""
    send = emit or (lambda event: None)
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
        step = await agent.propose(task, history)
        if step.kind == "finish":
            session.status = SessionStatus.COMPLETED
            send(
                StreamEvent(
                    type=EventType.SESSION_COMPLETED,
                    session_id=session.session_id,
                    data={"message": step.message},
                )
            )
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
            send(
                StreamEvent(
                    type=EventType.SESSION_COMPLETED,
                    session_id=session.session_id,
                    data={"message": "Run stopped: the agent was revoked.", "revoked": True},
                )
            )
            return session

    session.status = SessionStatus.COMPLETED
    send(
        StreamEvent(
            type=EventType.SESSION_COMPLETED,
            session_id=session.session_id,
            data={"message": "Reached the step limit."},
        )
    )
    return session
