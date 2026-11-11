"""Owns live runs: builds a control plane per run, drives the agent, and fans events out to clients.

Each run keeps its full event log and a set of subscriber signals, so a client that connects late
still sees the run from the start and then follows along live. Everything is in memory and scoped to
one process, which is all the demo needs; the same shape would carry over to a durable store later.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from ..agent import OFFBOARDING_TASK, ReplayAgent, run_session
from ..config import Settings
from ..domain import EventType, StreamEvent
from ..service import ControlPlane, build_control_plane


@dataclass
class RunHandle:
    """One run: its control plane, its event log, and the signals that wake waiting SSE clients."""

    run_id: str
    source: str = ""
    cp: ControlPlane | None = None
    task: asyncio.Task | None = None
    events: list[StreamEvent] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    _waiters: list[asyncio.Event] = field(default_factory=list)

    def emit(self, event: StreamEvent) -> None:
        """Append an event and wake every subscriber. Called synchronously from the run loop."""
        self.events.append(event)
        for waiter in self._waiters:
            waiter.set()

    def add_waiter(self) -> asyncio.Event:
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        return waiter

    def remove_waiter(self, waiter: asyncio.Event) -> None:
        if waiter in self._waiters:
            self._waiters.remove(waiter)

    def mark_done(self) -> None:
        self.done.set()
        for waiter in self._waiters:
            waiter.set()


class TooManyRuns(RuntimeError):
    """More runs are in progress than the server allows at once."""


class SessionManager:
    """Creates and tracks runs. One per browser session in the demo; a few can run at once."""

    def __init__(self, settings: Settings, *, max_runs: int = 64, max_active: int = 4) -> None:
        self._settings = settings
        self._runs: dict[str, RunHandle] = {}
        self._client = None  # a shared AsyncAnthropic, created the first time live mode is used
        self._max_runs = max_runs
        # In live mode every run calls a paid model, so the number running at once is capped.
        self._max_active = max_active

    def active_runs(self) -> int:
        return sum(1 for h in self._runs.values() if not h.done.is_set())

    def get(self, run_id: str) -> RunHandle | None:
        return self._runs.get(run_id)

    def _evict_finished(self) -> None:
        """Keep the run store bounded. Drop the oldest finished runs once over the cap."""
        while len(self._runs) > self._max_runs:
            oldest_finished = next(
                (rid for rid, h in self._runs.items() if h.done.is_set()), None
            )
            if oldest_finished is None:
                return  # nothing finished to evict; leave running work alone
            handle = self._runs.pop(oldest_finished)
            if handle.cp is not None:
                handle.cp.broker.release_session(handle.cp.session.session_id)

    def _build_agent(self, cp: ControlPlane, live: bool):
        if live:
            from anthropic import AsyncAnthropic

            from ..agent.claude import ClaudeAgent

            if self._client is None:
                self._client = AsyncAnthropic(api_key=self._settings.anthropic_api_key)
            return ClaudeAgent(
                client=self._client,
                tool_schemas=cp.registry.anthropic_tool_schemas(),
                model=self._settings.model,
                effort=self._settings.effort,
            )
        if self._settings.trajectory_path:
            # Replay a recorded run (for example one captured from a real model), so the demo can
            # show a real trajectory, not only the built-in synthetic one. The UI shows the source.
            return ReplayAgent.from_file(
                self._settings.trajectory_path,
                step_delay_seconds=self._settings.demo_step_delay_seconds,
            )
        return ReplayAgent(step_delay_seconds=self._settings.demo_step_delay_seconds)

    def start_run(self, *, mode: str | None = None) -> RunHandle:
        """Build a control plane, launch the governed run as a task, and return its handle."""
        if self.active_runs() >= self._max_active:
            raise TooManyRuns(f"{self._max_active} runs are already in progress")
        requested = (mode or self._settings.mode).strip().lower()
        live = requested == "live" and self._settings.live_ready
        run_id = uuid4().hex[:12]
        handle = RunHandle(run_id=run_id)
        # A run is stopped by the live gateway enforcer, so the CAEP toggle does not affect it; it
        # only changes the offline-validator benchmark. The run always builds with the strong path.
        cp = build_control_plane(
            task=OFFBOARDING_TASK,
            mode="live" if live else "replay",
            approval_timeout_seconds=self._settings.approval_timeout_seconds,
            emit=handle.emit,
        )
        handle.cp = cp
        agent = self._build_agent(cp, live)
        handle.source = agent.source
        handle.task = asyncio.create_task(self._drive(handle, agent))
        self._runs[run_id] = handle
        self._evict_finished()
        return handle

    async def _drive(self, handle: RunHandle, agent) -> None:
        assert handle.cp is not None
        try:
            await run_session(
                agent=agent,
                broker=handle.cp.broker,
                session=handle.cp.session,
                task=OFFBOARDING_TASK,
                emit=handle.emit,
                verify=handle.cp.verify,
            )
        except Exception as exc:  # never let a run crash the server; surface it on the stream
            handle.emit(
                StreamEvent(
                    type=EventType.ERROR,
                    session_id=handle.cp.session.session_id,
                    data={"message": f"the run failed: {exc}"},
                )
            )
        finally:
            handle.mark_done()
            handle.cp.broker.release_session(handle.cp.session.session_id)
