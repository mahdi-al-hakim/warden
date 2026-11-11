"""The set of tools the broker can execute, plus helpers to describe them to a model.

The tool spec sets each operation's risk. Unknown tools or operations default to high risk, so a
typo or an invented call fails safe into approval or denial instead of slipping through as low.
"""

from __future__ import annotations

import time
from collections.abc import Callable
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
        # Every call any tool actually receives, logged by the registry itself rather than by the
        # broker. The eval checks the broker's own records against this, so a broker that ran a
        # tool without recording it is caught.
        self.calls: list[dict[str, Any]] = []
        self.clock: Callable[[], float] = time.monotonic
        # For each revoked agent, how many calls had been logged when the revoke landed. Any call
        # at or past that position came after it; positions are exact where clocks can tie.
        self.revoke_marks: dict[str, int] = {}
        for tool in self._tools.values():
            self._log_calls(tool)

    def mark_revoke(self, agent_id: str) -> None:
        self.revoke_marks.setdefault(agent_id, len(self.calls))

    def _log_calls(self, tool: Tool) -> None:
        inner = tool.run

        def run(operation: str, arguments: dict[str, Any]) -> ToolResult:
            self.calls.append({"tool": tool.name, "operation": operation,
                               "arguments": dict(arguments) if isinstance(arguments, dict) else {},
                               "at": self.clock()})
            return inner(operation, arguments)

        tool.run = run  # type: ignore[method-assign]

    @classmethod
    def synthetic(cls) -> ToolRegistry:
        """Only the synthetic tools, whatever the environment says. The eval uses this, so a
        GitHub token exported for a demo can never send an eval run to a real org."""
        return cls([DirectoryTool(), VpnTool(), RepoTool()])

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

    def argument_problem(self, tool: str, operation: str, arguments: Any) -> str | None:
        """A description of what is wrong with the arguments, or None if they fit the operation.

        Unknown tools and operations are left to risk and policy, which deny them by default.
        """
        found = self._tools.get(tool)
        if found is None or operation not in found.operations:
            return None
        if not isinstance(arguments, dict) or not all(isinstance(k, str) for k in arguments):
            return "arguments must be an object with string keys"
        op = found.operations[operation]
        for name in op.required:
            if name not in arguments:
                return f"missing required argument {name!r}"
        for name, spec in op.parameters.items():
            if name in arguments and spec.get("type") == "string":
                if not isinstance(arguments[name], str):
                    return f"{name!r} must be a string"
        return None

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
