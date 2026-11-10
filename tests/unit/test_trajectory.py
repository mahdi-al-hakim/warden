"""Save a trajectory, load it back, and confirm the loaded run behaves like the authored one.

This backs the claim in replay.py: a recorded run can stand in for the authored one, and the
governed replay of a loaded trajectory is identical. No key is needed; the authored one is input.
"""

from __future__ import annotations

import asyncio

from warden.agent import (
    OFFBOARDING_TASK,
    OFFBOARDING_TRAJECTORY,
    ReplayAgent,
    load_trajectory,
    run_session,
    save_trajectory,
)
from warden.domain import SessionStatus
from warden.service import build_control_plane


def test_save_and_load_round_trips(tmp_path):
    path = tmp_path / "traj.json"
    save_trajectory(
        OFFBOARDING_TRAJECTORY, path, source="recorded:test-model", task=OFFBOARDING_TASK
    )
    steps, meta = load_trajectory(path)

    assert meta["source"] == "recorded:test-model"
    assert meta["task"] == OFFBOARDING_TASK
    assert len(steps) == len(OFFBOARDING_TRAJECTORY)
    for loaded, original in zip(steps, OFFBOARDING_TRAJECTORY, strict=True):
        assert loaded.kind == original.kind
        assert loaded.tool == original.tool
        assert loaded.operation == original.operation
        assert loaded.arguments == original.arguments
        assert loaded.message == original.message


async def test_loaded_trajectory_replays_under_governance(tmp_path):
    path = tmp_path / "traj.json"
    save_trajectory(OFFBOARDING_TRAJECTORY, path, source="recorded:test-model")
    agent = ReplayAgent.from_file(path)
    assert agent.source == "recorded:test-model"

    cp = build_control_plane(task=OFFBOARDING_TASK, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(agent=agent, broker=cp.broker, session=cp.session, task=OFFBOARDING_TASK)
    )
    for _ in range(200):
        if cp.broker.pending_action_ids():
            break
        await asyncio.sleep(0.01)
    cp.broker.resolve_approval(
        cp.broker.pending_action_ids()[0], approved=True, approver="operator"
    )
    session = await run

    assert session.status is SessionStatus.COMPLETED
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert jordan.data["enabled"] is False
    breakglass = cp.registry.execute("directory", "lookup_user", {"user_id": "security.breakglass"})
    assert breakglass.data["enabled"] is True
    assert cp.audit.verify().ok is True


def test_manager_replays_the_configured_trajectory_file(tmp_path):
    # D1 wiring: when WARDEN_TRAJECTORY points at a recorded run, the demo replays that run, with
    # its own source, instead of the built-in synthetic trajectory. Without it, the synthetic run
    # is used. handle.source is set synchronously in start_run, so the run is cancelled right after.
    from warden.api.manager import SessionManager
    from warden.config import Settings

    path = tmp_path / "traj.json"
    save_trajectory(
        OFFBOARDING_TRAJECTORY, path, source="recorded:test-model", task=OFFBOARDING_TASK
    )

    async def _source_for(settings: Settings) -> str:
        manager = SessionManager(settings)
        handle = manager.start_run()
        try:
            return handle.source
        finally:
            if handle.task is not None:
                handle.task.cancel()
                await asyncio.gather(handle.task, return_exceptions=True)

    configured = asyncio.run(
        _source_for(Settings(mode="replay", demo_step_delay_seconds=0.0, trajectory_path=str(path)))
    )
    assert configured == "recorded:test-model"

    default = asyncio.run(_source_for(Settings(mode="replay", demo_step_delay_seconds=0.0)))
    assert default == "synthetic"
