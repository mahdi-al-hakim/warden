"""The control plane's own check of the offboarding, read from the systems rather than the agent."""

from __future__ import annotations

from warden.service import build_control_plane


def _status(result):
    return {c["id"]: c["status"] for c in result["checks"]}


def test_a_fresh_environment_is_not_offboarded():
    result = build_control_plane(task="offboard").verify()
    assert result["complete"] is False
    assert _status(result) == {"app_access": "not_done", "vpn": "not_done",
                               "repo_access": "not_done", "account": "not_done"}


def test_the_repository_check_names_where_access_remains():
    result = build_control_plane(task="offboard").verify()
    repo = next(c for c in result["checks"] if c["id"] == "repo_access")
    assert repo["detail"] == "still a member of: payments-api"


def test_an_unreadable_system_is_unknown_not_done_or_done():
    cp = build_control_plane(task="offboard")
    cp.registry.get("vpn").inject_failure("status")
    result = cp.verify()
    assert _status(result)["vpn"] == "unknown"
    assert result["complete"] is False


def test_a_finished_offboarding_verifies_complete():
    cp = build_control_plane(task="offboard")
    reg = cp.registry
    for app in ("crm", "wiki", "analytics"):
        reg.execute("directory", "revoke_access", {"user_id": "jordan.lee", "app": app})
    reg.execute("vpn", "disable", {"user_id": "jordan.lee"})
    reg.execute("repo", "remove_member", {"repo": "payments-api", "user_id": "jlee-contractor"})
    reg.execute("directory", "disable_account", {"user_id": "jordan.lee"})
    result = cp.verify()
    assert result["complete"] is True
    assert set(_status(result).values()) == {"done"}
