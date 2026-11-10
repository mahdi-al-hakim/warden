"""Record a real Claude run through the control plane and save it as a replay trajectory.

The demo ships with an authored synthetic trajectory so it always runs without a key. If you want
the replay to show what a real model actually did, run this once with your own key. It drives a live
run through the same broker, policy, approval, and audit path, captures the steps the model
proposed, and writes them to a file the ReplayAgent can load.

    ANTHROPIC_API_KEY=sk-... python scripts/record_trajectory.py --out web_trajectory.json

The run needs an approval for the high-risk step; this script auto-approves it so the capture is
complete. The saved file records the source as the model name, and the UI shows that source, so a
replay is always labeled for what it is.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow running this file directly from the repo without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from warden.agent import OFFBOARDING_TASK, AgentStep, save_trajectory  # noqa: E402
from warden.agent.claude import ClaudeAgent  # noqa: E402
from warden.config import Settings  # noqa: E402
from warden.domain import EventType  # noqa: E402
from warden.service import build_control_plane  # noqa: E402


async def _record(settings: Settings, out: Path) -> int:
    from anthropic import AsyncAnthropic

    events: list = []
    cp = build_control_plane(
        task=OFFBOARDING_TASK, mode="live", emit=events.append, approval_timeout_seconds=120.0
    )
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    agent = ClaudeAgent(
        client=client, tool_schemas=cp.registry.anthropic_tool_schemas(), model=settings.model
    )

    from warden.agent import run_session

    async def _auto_approve() -> None:
        while not any(e.type == EventType.SESSION_COMPLETED for e in events):
            for action_id in list(cp.broker.pending_action_ids()):
                cp.broker.resolve_approval(action_id, approved=True, approver="recorder")
            await asyncio.sleep(0.05)

    approver = asyncio.create_task(_auto_approve())
    session = await run_session(
        agent=agent, broker=cp.broker, session=cp.session, task=OFFBOARDING_TASK,
        emit=events.append,
    )
    approver.cancel()

    steps = _steps_from_events(events)
    save_trajectory(steps, out, source=f"recorded:{settings.model}", task=OFFBOARDING_TASK)
    print(f"Saved {len(steps)} steps to {out} (session {session.status.value}).")
    print("Load it with ReplayAgent.from_file(path), or point the server at it.")
    return 0


def _steps_from_events(events: list) -> list[AgentStep]:
    steps: list[AgentStep] = []
    for event in events:
        if event.type == EventType.ACTION_PROPOSED:
            d = event.data
            args = {k: v for k, v in (d.get("arguments") or {}).items() if k != "account_tier"}
            steps.append(
                AgentStep(
                    kind="tool", tool=d.get("tool"), operation=d.get("operation"),
                    arguments=args, message=d.get("summary", ""),
                )
            )
        elif event.type == EventType.SESSION_COMPLETED:
            steps.append(AgentStep(kind="finish", message=event.data.get("message", "Done.")))
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a live Claude trajectory for replay")
    parser.add_argument("--out", default="web_trajectory.json", help="file to write")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    settings = settings.model_copy(update={"mode": "live"})
    if not settings.anthropic_api_key:
        print("Set ANTHROPIC_API_KEY to record a live trajectory.", file=sys.stderr)
        return 2
    return asyncio.run(_record(settings, Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
