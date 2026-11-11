"""A real GitHub connector for the repository tool, with safety built into the connector itself.

This lets the agent act on a real system during the demo: it removes a departing contractor from one
or more real repositories. It is a drop-in for the synthetic RepoTool, so the broker, policy,
approval, revocation, and audit path do not change. Only the far end of the tool call is different.

Three guardrails make it safe to show live, and the agent cannot talk its way past any of them,
because they are enforced here and not in the policy the agent can see:

  1. Scope. Every call is pinned to one configured organization and a configured set of repos. The
     agent never supplies the org, so it cannot be pointed at anything else.
  2. Allowlist. A write only touches a login on an explicit test-account allowlist. Anyone not on
     the list is refused, so a real collaborator can never be removed even if the agent proposes it.
  3. Dry run by default. Writes are simulated unless writes are explicitly enabled, so running the
     connector by accident changes nothing.

Build the config from the environment with GitHubConfig.from_env(). With no token the connector is
not used at all and the synthetic RepoTool stands in, so the default demo and the whole test suite
never touch the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import httpx

from ..domain import RiskTier, ToolResult
from .base import Operation, Tool

API_BASE = "https://api.github.com"


@dataclass(frozen=True)
class GitHubConfig:
    """Everything the connector needs, with the safety scopes it refuses to act outside of."""

    token: str
    org: str
    repos: frozenset[str] = field(default_factory=frozenset)
    test_logins: frozenset[str] = field(default_factory=frozenset)
    allow_writes: bool = False
    api_base: str = API_BASE

    @classmethod
    def from_env(cls) -> GitHubConfig | None:
        """Build config from WARDEN_GITHUB_* vars. None with no token, so synthetic tools run."""
        token = (os.getenv("WARDEN_GITHUB_TOKEN") or "").strip()
        org = (os.getenv("WARDEN_GITHUB_ORG") or "").strip()
        if not token or not org:
            return None
        repos = _split(os.getenv("WARDEN_GITHUB_REPOS"))
        logins = _split(os.getenv("WARDEN_GITHUB_TEST_LOGINS"))
        raw_allow = (os.getenv("WARDEN_GITHUB_ALLOW_WRITES") or "").strip().lower()
        allow = raw_allow in {"1", "true", "yes"}
        return cls(
            token=token,
            org=org,
            repos=frozenset(repos),
            test_logins=frozenset(logins),
            allow_writes=allow,
        )


def _split(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


class GitHubTool(Tool):
    """The repository tool, backed by the real GitHub API, scoped and allowlisted for safety."""

    name = "repo"

    def __init__(self, config: GitHubConfig, *, client: httpx.Client | None = None) -> None:
        super().__init__()
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.api_base,
            headers={
                "Authorization": f"Bearer {config.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10.0,
        )
        self.operations = {
            "list_repos": Operation(
                "list_repos", RiskTier.LOW, "List the repositories this connector may act on.",
            ),
            "list_members": Operation(
                "list_members", RiskTier.LOW, "Read the collaborators on a repository.",
                {"repo": {"type": "string"}}, ("repo",),
            ),
            "remove_member": Operation(
                "remove_member", RiskTier.MEDIUM,
                "Remove a collaborator from a repository. user_id is the collaborator's login.",
                {"repo": {"type": "string"}, "user_id": {"type": "string"}}, ("repo", "user_id"),
            ),
        }

    def run(self, operation: str, arguments: dict[str, object]) -> ToolResult:
        failed = self._maybe_fail(operation)
        if failed is not None:
            return failed
        repo = str(arguments.get("repo", ""))
        if operation == "list_repos":
            # The configured scope, not the whole org: these are the only repos it will touch.
            return ToolResult(ok=True, data={"repos": sorted(self._config.repos)})
        if operation == "list_members":
            return self._list_members(repo)
        if operation == "remove_member":
            return self._remove_member(repo, str(arguments.get("user_id", "")))
        return self.unknown_operation(operation)

    @property
    def test_logins(self) -> frozenset[str]:
        """The logins this connector is allowed to remove, used to link the subject's login."""
        return self._config.test_logins

    def _in_scope(self, repo: str) -> bool:
        # An empty repo allowlist means no repo is in scope, which fails safe.
        return repo in self._config.repos

    def _list_members(self, repo: str) -> ToolResult:
        if not self._in_scope(repo):
            return ToolResult(ok=False, error=f"repo {repo!r} is not in the configured scope")
        url: str | None = f"/repos/{self._config.org}/{repo}/collaborators"
        params: dict[str, int] | None = {"per_page": 100}
        logins: list[str] = []
        for _ in range(20):  # GitHub pages at 100; twenty pages is far past any demo repo
            try:
                resp = self._client.get(url, params=params)
            except httpx.HTTPError as exc:
                return ToolResult(ok=False, error=f"github request failed: {exc}")
            if resp.status_code != 200:
                return ToolResult(ok=False, error=f"github returned {resp.status_code}")
            logins.extend(item.get("login", "") for item in resp.json())
            url = resp.links.get("next", {}).get("url")
            params = None  # the next link carries its own query string
            if not url:
                break
        return ToolResult(ok=True, data={"repo": repo, "members": sorted(logins)})

    def _remove_member(self, repo: str, login: str) -> ToolResult:
        if not self._in_scope(repo):
            return ToolResult(ok=False, error=f"repo {repo!r} is not in the configured scope")
        if login not in self._config.test_logins:
            # Hard refusal: the agent can never remove a login that is not a designated test login.
            return ToolResult(
                ok=False,
                error=f"refusing to remove {login!r}: not on the test-account allowlist",
            )
        if not self._config.allow_writes:
            # Dry run: report what would happen without calling the destructive endpoint.
            return ToolResult(
                ok=True,
                data={"repo": repo, "removed": login, "dry_run": True},
            )
        try:
            resp = self._client.delete(
                f"/repos/{self._config.org}/{repo}/collaborators/{login}"
            )
        except httpx.HTTPError as exc:
            return ToolResult(ok=False, error=f"github request failed: {exc}")
        if resp.status_code == 204:
            return ToolResult(ok=True, data={"repo": repo, "removed": login, "dry_run": False})
        if resp.status_code == 404:
            # GitHub documents 204, 403 and 422 for this call. A 404 can mean the login was never a
            # collaborator, or that the token cannot see the repo, so it is reported as a failure;
            # the end-of-run check then reads the collaborator list to show the real state.
            return ToolResult(
                ok=False,
                error="github returned 404: the login is not a collaborator, or the token cannot "
                      "see the repository",
            )
        return ToolResult(ok=False, error=f"github returned {resp.status_code}")
