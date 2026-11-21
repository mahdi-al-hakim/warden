"""Edge cases found in review: malformed arguments, writes with no target, approver spelling, and
truncation of the audit log."""

from __future__ import annotations

from warden.audit import AuditLog
from warden.domain import ActionOutcome
from warden.service import build_control_plane


async def test_non_object_arguments_are_denied_and_audited():
    cp = build_control_plane(synthetic=True)
    before = len(cp.broker.audit.events)
    result = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="vpn", operation="disable",
        arguments=["jordan.lee"], message="disable",  # type: ignore[arg-type]
    )
    assert result.outcome is not ActionOutcome.EXECUTED
    assert len(cp.broker.audit.events) > before
    assert not any(c["operation"] == "disable" for c in cp.registry.calls)


async def test_non_object_arguments_to_an_unknown_tool_are_denied_without_crashing():
    cp = build_control_plane(synthetic=True)
    result = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="nope", operation="x",
        arguments="not a dict", message="",  # type: ignore[arg-type]
    )
    assert result.outcome is not ActionOutcome.EXECUTED


async def test_a_write_with_an_empty_target_is_out_of_scope():
    cp = build_control_plane(synthetic=True)
    for tool, op, args in [("vpn", "disable", {"user_id": ""}),
                           ("repo", "remove_member", {"repo": "payments-api", "user_id": ""})]:
        result = await cp.broker.handle_action(
            session=cp.session, agent_id=cp.agent_id, tool=tool, operation=op,
            arguments=args, message="",
        )
        assert result.outcome is not ActionOutcome.EXECUTED, (tool, op)
    assert not any(c["operation"] in {"disable", "remove_member"} for c in cp.registry.calls)


async def test_the_agent_cannot_approve_under_a_respelling_of_its_id():
    import asyncio

    cp = build_control_plane(synthetic=True, approval_timeout_seconds=5.0)
    task = asyncio.create_task(cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="directory", operation="disable_account",
        arguments={"user_id": "jordan.lee"}, message="disable",
    ))
    for _ in range(100):
        if cp.broker._pending:
            break
        await asyncio.sleep(0.01)
    (action_id,) = list(cp.broker._pending)
    for spelling in (cp.agent_id.upper(), f" {cp.agent_id} ", cp.agent_id + " "):
        assert cp.broker.resolve_approval(action_id, approved=True, approver=spelling) is False
    assert cp.broker.resolve_approval(action_id, approved=False, approver="operator_a") is True
    result = await task
    assert result.outcome is not ActionOutcome.EXECUTED


def _three() -> AuditLog:
    log = AuditLog()
    for i in range(3):
        log.append(session_id="ses_1", kind="action.executed", actor="agent_x", data={"i": i})
    return log


def test_dropping_the_newest_entries_passes_a_plain_verify_but_not_a_recorded_head():
    log = _three()
    head = log.head()
    del log._events[-1]
    assert log.verify().ok is True  # a plain chain can't see truncation
    result = log.verify(expected_head=head)
    assert result.ok is False
    assert "shorter" in result.reason


def test_a_full_rewrite_is_caught_against_a_recorded_head():
    log = _three()
    head = log.head()
    rewritten = AuditLog()
    for i in range(3):
        rewritten.append(
            session_id="ses_1", kind="action.executed", actor="agent_x", data={"i": 10 + i}
        )
    assert rewritten.verify().ok is True
    assert rewritten.verify(expected_head=head).ok is False


def test_head_of_an_empty_log_verifies():
    log = AuditLog()
    assert log.verify(expected_head=log.head()).ok is True


async def test_an_undeclared_argument_naming_another_account_is_refused():
    cp = build_control_plane(synthetic=True)
    result = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="directory", operation="disable_account",
        arguments={"user_id": "jordan.lee", "target": "security.breakglass"}, message="",
    )
    assert result.outcome is not ActionOutcome.EXECUTED
    assert not any(c["operation"] == "disable_account" for c in cp.registry.calls)


async def test_a_session_without_a_subject_refuses_writes():
    cp = build_control_plane(synthetic=True, subject=None)
    result = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="vpn", operation="disable",
        arguments={"user_id": "dana.ops"}, message="",
    )
    assert result.outcome is not ActionOutcome.EXECUTED
    read = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="vpn", operation="status",
        arguments={"user_id": "dana.ops"}, message="",
    )
    assert read.outcome is ActionOutcome.EXECUTED
