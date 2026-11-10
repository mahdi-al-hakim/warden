"""End-to-end tests of the governed run: policy, approval, denial, revocation, and the audit."""

from __future__ import annotations

import asyncio
import threading

from warden.agent import OFFBOARDING_TASK, ReplayAgent, run_session
from warden.domain import ActionOutcome, EventType, SessionStatus
from warden.eval.invariants import no_action_starts_after_revoke
from warden.service import build_control_plane


async def _wait_for_pending(broker, timeout: float = 2.0) -> str:
    waited = 0.0
    while not broker.pending_action_ids():
        await asyncio.sleep(0.01)
        waited += 0.01
        if waited > timeout:
            raise AssertionError("no approval request appeared in time")
    return broker.pending_action_ids()[0]


def _collector():
    events: list = []
    return events, events.append


async def test_full_offboarding_completes_with_one_approval_and_one_denial():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(
            agent=ReplayAgent(), broker=cp.broker, session=cp.session,
            task=OFFBOARDING_TASK, emit=emit,
        )
    )
    action_id = await _wait_for_pending(cp.broker)
    assert cp.broker.resolve_approval(action_id, approved=True, approver="operator_dana")
    session = await run

    assert session.status is SessionStatus.COMPLETED
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert jordan.data["enabled"] is False
    assert "crm" not in jordan.data["apps"]
    # The break-glass account was protected: the over-reach was denied, so it stays enabled.
    breakglass = cp.registry.execute("directory", "lookup_user", {"user_id": "security.breakglass"})
    assert breakglass.data["enabled"] is True

    kinds = [e.type for e in events]
    assert EventType.APPROVAL_REQUESTED in kinds
    denied = [
        e for e in events
        if e.type == EventType.ACTION_BLOCKED and e.data.get("outcome") == "denied"
    ]
    assert denied, "expected the privileged over-reach to be denied"
    assert cp.audit.verify().ok is True


async def test_revocation_before_the_run_stops_everything():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    cp.broker.revoke(cp.session, cp.agent_id, actor="operator_dana")
    session = await run_session(
        agent=ReplayAgent(), broker=cp.broker, session=cp.session,
        task=OFFBOARDING_TASK, emit=emit,
    )
    assert session.status is SessionStatus.REVOKED
    # Governance invariant: nothing executes once revoked, not even a read.
    assert not any(e.type == EventType.ACTION_EXECUTED for e in events)
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert "crm" in jordan.data["apps"]
    assert cp.audit.verify().ok is True


async def test_revoke_during_approval_still_blocks_the_action():
    # The reviewer's key case: a revoke that lands while a high-risk action waits for a human must
    # block the action even if a human then approves it. The re-check after approval enforces this.
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(
            agent=ReplayAgent(), broker=cp.broker, session=cp.session,
            task=OFFBOARDING_TASK, emit=emit,
        )
    )
    action_id = await _wait_for_pending(cp.broker)
    cp.broker.revoke(cp.session, cp.agent_id, actor="operator_dana")
    cp.broker.resolve_approval(action_id, approved=True, approver="operator_dana")
    session = await run

    assert session.status is SessionStatus.REVOKED
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert jordan.data["enabled"] is True  # the disable was blocked despite the approval
    assert cp.audit.verify().ok is True


async def test_revoke_during_an_in_flight_call_is_not_a_new_start():
    # The subtle race a reviewer raised: a revoke that lands while a tool call is already in flight
    # cannot recall it. The completed call must not be counted as a new action started after the
    # revoke. The broker emits the commit marker before running, so the invariant keys off the
    # commit (which came first), and the audit records the true order.
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    directory = cp.registry.get("directory")
    gate = threading.Event()
    original = directory.run

    def blocking_run(operation, arguments):
        if operation == "revoke_access":
            gate.wait(5)  # hold the call open inside the worker thread
        return original(operation, arguments)

    directory.run = blocking_run
    handle = asyncio.create_task(
        cp.broker.handle_action(
            session=cp.session, agent_id=cp.agent_id, tool="directory",
            operation="revoke_access", arguments={"user_id": "jordan.lee", "app": "crm"},
            message="revoke crm access",
        )
    )
    # Wait until the action has committed and is in flight, then revoke mid-call and release it.
    waited = 0.0
    while not any(e.type == EventType.ACTION_COMMITTED for e in events):
        await asyncio.sleep(0.01)
        waited += 0.01
        assert waited < 2.0, "the action never committed"
    cp.broker.revoke(cp.session, cp.agent_id, actor="operator_dana")
    gate.set()
    result = await handle

    kinds = [e.type for e in events]
    # The commit came before the revoke; the execution completed after it.
    assert kinds.index(EventType.ACTION_COMMITTED) < kinds.index(EventType.REVOCATION_TRIGGERED)
    assert kinds.index(EventType.REVOCATION_TRIGGERED) < kinds.index(EventType.ACTION_EXECUTED)
    # Nothing new started after the revoke, so the guarantee holds and the record still verifies.
    assert no_action_starts_after_revoke(events, cp).passed is True
    assert result.outcome is ActionOutcome.EXECUTED
    assert cp.audit.verify().ok is True


async def test_every_executed_action_is_in_the_audit():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(
            agent=ReplayAgent(), broker=cp.broker, session=cp.session,
            task=OFFBOARDING_TASK, emit=emit,
        )
    )
    action_id = await _wait_for_pending(cp.broker)
    cp.broker.resolve_approval(action_id, approved=True, approver="operator_dana")
    await run

    executed_events = [e for e in events if e.type == EventType.ACTION_EXECUTED]
    executed_audit = [e for e in cp.audit.events if e.kind == EventType.ACTION_EXECUTED]
    assert len(executed_events) == len(executed_audit)
    assert len(executed_events) >= 1


async def test_human_denial_records_the_approver_and_is_not_a_timeout():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(
            agent=ReplayAgent(), broker=cp.broker, session=cp.session,
            task=OFFBOARDING_TASK, emit=emit,
        )
    )
    action_id = await _wait_for_pending(cp.broker)
    assert cp.broker.resolve_approval(action_id, approved=False, approver="operator_dana")
    session = await run

    assert session.status is SessionStatus.COMPLETED
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert jordan.data["enabled"] is True  # the disable was denied, so the account stays on
    # The audit names who denied it, and records a denial rather than a timeout.
    human_denied = [
        e for e in cp.audit.events
        if e.kind == "action.blocked" and e.data.get("approver") == "operator_dana"
    ]
    assert human_denied
    assert human_denied[0].data["outcome"] == "denied"


async def test_agent_supplied_account_tier_is_always_stripped():
    cp = build_control_plane(task=OFFBOARDING_TASK, approval_timeout_seconds=5.0)
    # No resolvable user on this call, so a forged tier must simply be dropped, not trusted.
    await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="repo", operation="list_members",
        arguments={"repo": "payments-api", "account_tier": "privileged"}, message="listing",
    )
    executed = [e for e in cp.audit.events if e.kind == "action.executed"]
    assert executed
    assert "account_tier" not in executed[-1].data["arguments"]

    # With a resolvable user, the broker's resolved tier overrides the agent's forged one.
    await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="directory", operation="revoke_access",
        arguments={"user_id": "jordan.lee", "app": "crm", "account_tier": "privileged"},
        message="revoke",
    )
    latest = [e for e in cp.audit.events if e.kind == "action.executed"][-1]
    assert latest.data["arguments"]["account_tier"] == "standard"


async def test_agent_cannot_approve_its_own_action():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(
            agent=ReplayAgent(), broker=cp.broker, session=cp.session,
            task=OFFBOARDING_TASK, emit=emit,
        )
    )
    action_id = await _wait_for_pending(cp.broker)
    # The agent, named by its agent id or its session id, must not approve its own action, and an
    # empty approver is not a decision. Each of these must be refused.
    def approve_as(who):
        return cp.broker.resolve_approval(action_id, approved=True, approver=who)

    assert approve_as(cp.agent_id) is False
    assert approve_as(cp.session.session_id) is False
    assert approve_as("") is False
    # None of those slipped through: the account is still enabled because the disable never ran.
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    assert jordan.data["enabled"] is True
    # A real operator can still approve it, and the run completes.
    assert cp.broker.resolve_approval(action_id, approved=True, approver="operator_dana")
    session = await run
    assert session.status is SessionStatus.COMPLETED


async def test_approval_and_revocation_are_audited():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(agent=ReplayAgent(), broker=cp.broker, session=cp.session,
                    task=OFFBOARDING_TASK, emit=emit)
    )
    action_id = await _wait_for_pending(cp.broker)
    cp.broker.resolve_approval(action_id, approved=True, approver="operator_dana")
    await run
    # The human approval is in the tamper-evident log, naming who approved.
    resolved = [e for e in cp.audit.events if e.kind == "approval.resolved"]
    assert resolved
    assert resolved[0].data["approver"] == "operator_dana"
    assert resolved[0].data["approved"] is True
    # The executed account disable records the approver, not just that it ran.
    disable = [
        e for e in cp.audit.events
        if e.kind == "action.executed" and e.data.get("operation") == "disable_account"
    ]
    assert disable and disable[0].data["approver"] == "operator_dana"
    assert cp.audit.verify().ok


async def test_revocation_is_audited():
    cp = build_control_plane(task=OFFBOARDING_TASK, approval_timeout_seconds=5.0)
    cp.broker.revoke(cp.session, cp.agent_id, actor="operator_dana")
    rev = [e for e in cp.audit.events if e.kind == "revocation.triggered"]
    assert rev and rev[0].data["actor"] == "operator_dana"
    assert cp.audit.verify().ok


async def test_approval_timeout_denies_by_default():
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=0.1)
    # Never resolve the approval. It should time out into a denial and the run should finish.
    session = await run_session(
        agent=ReplayAgent(), broker=cp.broker, session=cp.session,
        task=OFFBOARDING_TASK, emit=emit,
    )
    assert session.status is SessionStatus.COMPLETED
    jordan = cp.registry.execute("directory", "lookup_user", {"user_id": "jordan.lee"})
    # The disable needed approval, which timed out, so the account stays enabled.
    assert jordan.data["enabled"] is True
    assert cp.audit.verify().ok is True


async def test_writes_are_scoped_to_the_offboarding_subject():
    # The run is scoped to jordan.lee. A steered agent that tries to cut a bystander's access is
    # denied and the bystander keeps it; the subject's own writes and reads of anyone still work.
    cp = build_control_plane(
        task=OFFBOARDING_TASK, subject="jordan.lee", approval_timeout_seconds=5.0
    )
    off = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="directory", operation="revoke_access",
        arguments={"user_id": "dana.ops", "app": "wiki"}, message="revoke a bystander",
    )
    assert off.outcome is ActionOutcome.DENIED
    dana = cp.registry.execute("directory", "lookup_user", {"user_id": "dana.ops"})
    assert "wiki" in dana.data["apps"]  # untouched

    on_subject = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="directory", operation="revoke_access",
        arguments={"user_id": "jordan.lee", "app": "crm"}, message="revoke the subject",
    )
    assert on_subject.outcome is ActionOutcome.EXECUTED

    read_bystander = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="directory", operation="lookup_user",
        arguments={"user_id": "dana.ops"}, message="read a bystander",
    )
    assert read_bystander.outcome is ActionOutcome.EXECUTED  # reads are not scoped


async def test_finish_message_reflects_the_real_outcome_not_a_script():
    # When the disable is denied, the closing line must not claim the account was disabled.
    events, emit = _collector()
    cp = build_control_plane(task=OFFBOARDING_TASK, emit=emit, approval_timeout_seconds=5.0)
    run = asyncio.create_task(
        run_session(agent=ReplayAgent(), broker=cp.broker, session=cp.session,
                    task=OFFBOARDING_TASK, emit=emit)
    )
    action_id = await _wait_for_pending(cp.broker)
    cp.broker.resolve_approval(action_id, approved=False, approver="operator_dana")
    await run
    completed = [e for e in events if e.type == EventType.SESSION_COMPLETED]
    msg = completed[-1].data["message"]
    assert "left enabled" in msg and "disable was denied" in msg
    assert "the account was disabled after approval" not in msg
