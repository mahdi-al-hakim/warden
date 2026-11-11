"""Tests for the real GitHub connector, driven by a mocked transport so no network is touched.

The point of these tests is the safety guardrails: scope, the test-account allowlist, and dry-run by
default. Each is shown refusing or simulating rather than acting, and the real write path is shown
issuing exactly one DELETE against the configured org.
"""

from __future__ import annotations

import httpx

from warden.domain import ActionOutcome
from warden.service import build_control_plane
from warden.tools.github_ops import GitHubConfig, GitHubTool


def _tool(handler, **overrides) -> tuple[GitHubTool, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    config = GitHubConfig(
        token="test-token",
        org="warden-demo-org",
        repos=frozenset({"payments-api"}),
        test_logins=frozenset({"contractor-test"}),
        **overrides,
    )
    client = httpx.Client(transport=httpx.MockTransport(_record), base_url=config.api_base)
    return GitHubTool(config, client=client), seen


def test_list_members_returns_sorted_logins():
    def handler(request):
        assert request.url.path == "/repos/warden-demo-org/payments-api/collaborators"
        return httpx.Response(200, json=[{"login": "dana"}, {"login": "contractor-test"}])

    tool, seen = _tool(handler)
    result = tool.run("list_members", {"repo": "payments-api"})
    assert result.ok is True
    assert result.data["members"] == ["contractor-test", "dana"]
    assert len(seen) == 1


def test_out_of_scope_repo_is_refused_without_a_request():
    tool, seen = _tool(lambda r: httpx.Response(200, json=[]))
    result = tool.run("list_members", {"repo": "some-other-repo"})
    assert result.ok is False
    assert "scope" in result.error
    assert seen == []  # nothing was sent


def test_remove_member_dry_run_by_default_makes_no_delete():
    tool, seen = _tool(lambda r: httpx.Response(204))
    result = tool.run("remove_member", {"repo": "payments-api", "user_id": "contractor-test"})
    assert result.ok is True
    assert result.data["dry_run"] is True
    assert result.data["removed"] == "contractor-test"
    assert seen == []  # dry run never calls the destructive endpoint


def test_remove_member_refuses_a_login_not_on_the_allowlist():
    tool, seen = _tool(lambda r: httpx.Response(204), allow_writes=True)
    result = tool.run("remove_member", {"repo": "payments-api", "user_id": "real-employee"})
    assert result.ok is False
    assert "allowlist" in result.error
    assert seen == []  # a non-test login never reaches GitHub, even with writes enabled


def test_remove_member_writes_exactly_one_delete_when_enabled():
    expected_path = "/repos/warden-demo-org/payments-api/collaborators/contractor-test"

    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path == expected_path
        return httpx.Response(204)

    tool, seen = _tool(handler, allow_writes=True)
    result = tool.run("remove_member", {"repo": "payments-api", "user_id": "contractor-test"})
    assert result.ok is True
    assert result.data["dry_run"] is False
    assert len(seen) == 1


def test_remove_member_treats_404_as_already_gone():
    tool, seen = _tool(lambda r: httpx.Response(404), allow_writes=True)
    result = tool.run("remove_member", {"repo": "payments-api", "user_id": "contractor-test"})
    assert result.ok is True


def test_from_env_is_none_without_a_token(monkeypatch):
    monkeypatch.delenv("WARDEN_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("WARDEN_GITHUB_ORG", raising=False)
    assert GitHubConfig.from_env() is None


def test_list_repos_reports_the_configured_scope_without_calling_github():
    tool, seen = _tool(lambda request: httpx.Response(500))
    result = tool.run("list_repos", {})
    assert result.ok is True
    assert result.data == {"repos": ["payments-api"]}
    assert seen == []


async def test_the_subjects_test_login_is_linked_and_in_scope(monkeypatch):
    # With the connector configured, the departing contractor's directory record points at the
    # test login, so the scope check accepts removing it and still refuses anyone else. Writes
    # are off, so the removal is a dry run and nothing reaches the network.
    monkeypatch.setenv("WARDEN_GITHUB_TOKEN", "test-token")
    monkeypatch.setenv("WARDEN_GITHUB_ORG", "throwaway-org")
    monkeypatch.setenv("WARDEN_GITHUB_REPOS", "payments-api")
    monkeypatch.setenv("WARDEN_GITHUB_TEST_LOGINS", "contractor-test")
    monkeypatch.setenv("WARDEN_GITHUB_ALLOW_WRITES", "false")
    cp = build_control_plane(task="offboard", subject="jordan.lee")
    assert "contractor-test" in cp.registry.identities_of("jordan.lee")

    own = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="repo", operation="remove_member",
        arguments={"repo": "payments-api", "user_id": "contractor-test"}, message="remove",
    )
    other = await cp.broker.handle_action(
        session=cp.session, agent_id=cp.agent_id, tool="repo", operation="remove_member",
        arguments={"repo": "payments-api", "user_id": "real-employee"}, message="remove",
    )
    assert own.outcome is ActionOutcome.EXECUTED
    assert own.result.data["dry_run"] is True
    assert other.outcome is ActionOutcome.DENIED
