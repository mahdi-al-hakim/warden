"""The set of tools the broker can execute, plus helpers to describe them to a model.

The tool spec sets each operation's risk. Unknown tools or operations default to high risk, so a
typo or an invented call fails safe into approval or denial instead of slipping through as low.
"""

from __future__ import annotations

from typing import Any

from ..domain import RiskTier, ToolResult
from .access_ops import DirectoryTool, RepoTool, VpnTool
from .base import Tool


def _repo_tool() -> Tool:
    """The real GitHub connector when it is configured, otherwise the synthetic repository tool.

    Importing the connector lazily keeps httpx off the default path and means a missing or empty
    config simply falls back to the synthetic tool, so nothing reaches the network by accident.
    """
    from .github_ops import GitHubConfig, GitHubTool

    config = GitHubConfig.from_env()
    if config is None:
        return RepoTool()
    return GitHubTool(config)


class ToolRegistry:
    """Owns the tool instances. The broker is the only caller; the agent never gets a handle."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        if tools is None:
            tools = [DirectoryTool(), VpnTool(), _repo_tool()]
        self._tools: dict[str, Tool] = {t.name: t for t in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def risk_for(self, tool: str, operation: str) -> RiskTier:
        """The operation's declared risk, or HIGH if the tool or operation is unknown."""
        found = self._tools.get(tool)
        if found is None or operation not in found.operations:
            return RiskTier.HIGH
        return found.operations[operation].risk

    def execute(self, tool: str, operation: str, arguments: dict[str, Any]) -> ToolResult:
        """Run one operation. A missing tool, or a tool that raises, becomes a clean failed result,
        so a tool bug is recorded as a failed action and audited rather than crashing the run."""
        found = self._tools.get(tool)
        if found is None:
            return ToolResult(ok=False, error=f"unknown tool {tool!r}")
        try:
            return found.run(operation, arguments)
        except Exception as exc:
            return ToolResult(
                ok=False,
                error=f"the {tool}.{operation} tool raised {exc.__class__.__name__}: {exc}",
            )

    def identities_of(self, user_id: str) -> set[str]:
        """Every identifier that names this person across systems (directory id, logins)."""
        directory = self._tools.get("directory")
        if directory is None or not hasattr(directory, "identities"):
            return {user_id}
        return directory.identities(user_id)

    def resolve_account_tier(self, arguments: dict[str, Any]) -> str | None:
        """Look up the target user's tier from the directory, so policy sees the real tier."""
        directory = self._tools.get("directory")
        user_id = arguments.get("user_id")
        if directory is None or not user_id or not hasattr(directory, "account_tier"):
            return None
        return directory.account_tier(user_id)

    def anthropic_tool_schemas(self) -> list[dict[str, Any]]:
        """Tool-use schemas for live mode, one entry per tool operation."""
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            for op in tool.operations.values():
                schemas.append(
                    {
                        "name": f"{tool.name}__{op.name}",
                        "description": op.description,
                        "input_schema": {
                            "type": "object",
                            "properties": op.parameters,
                            "required": list(op.required),
                        },
                    }
                )
        return schemas
