"""Live agent adapter: a real Claude model proposes each step through the Messages API tool loop.

This is optional. The core and the demo run without it in replay mode. When a key is present and
the mode is live, the runner uses this adapter, and the same broker, policy, approval, revocation,
and audit path governs whatever the model decides to do. Live mode reuses the same governance layer;
the only difference is where the next action comes from.
"""

from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from .base import AgentError, AgentStep

SYSTEM_PROMPT = (
    "You are an IT operations agent handling an employee offboarding. You have tools for the "
    "directory, VPN, and source control. Work one step at a time: call a single tool, read the "
    "result, then decide the next step. Before each tool call, say in one short sentence what you "
    "are about to do. Only do what the task asks, and prefer the least access needed. When the "
    "offboarding is complete, stop calling tools and summarize what you did and anything you could "
    "not do. Write plain text, without markdown or emoji."
)

# Stop reasons that mean the model finished its turn normally. Anything else is a broken turn.
_NORMAL_STOPS = {None, "tool_use", "end_turn", "stop_sequence"}

_DENIED = "Policy denied this action. Do not retry it; continue with the rest of the task."
_TIMED_OUT = "No operator approved this in time, so it did not run. Continue with the other steps."
_REVOKED = "Access has been revoked. Stop and do nothing further."


class ClaudeAgent:
    """Proposes steps with a real Claude model, one tool call per turn, fed back via the broker."""

    source = "claude-live"

    def __init__(
        self,
        *,
        client: AsyncAnthropic,
        tool_schemas: list[dict[str, Any]],
        model: str,
        max_tokens: int = 16000,
        system: str = SYSTEM_PROMPT,
        effort: str | None = None,
    ) -> None:
        # The agent is handed only the tool schemas, never the registry or a tool instance, so it
        # has no way to execute anything. Every call it proposes still goes through the broker.
        self._client = client
        self._tools = tool_schemas
        self._model = model
        self._max_tokens = max_tokens
        self._system = system
        self._effort = effort
        self._messages: list[dict[str, Any]] = []
        self._pending_tool_use_id: str | None = None
        self._started = False

    async def propose(self, task: str, history: list[dict[str, Any]]) -> AgentStep:
        """Ask the model for the next action, feeding back the result of the previous one."""
        if not self._started:
            self._messages.append({"role": "user", "content": task})
            self._started = True
        elif history and self._pending_tool_use_id is not None:
            self._messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": self._pending_tool_use_id,
                            "content": _result_text(history[-1]),
                        }
                    ],
                }
            )

        extra: dict[str, Any] = {}
        if self._effort:
            # Sent as a raw body field: older SDK releases have no typed parameter for it.
            extra["extra_body"] = {"output_config": {"effort": self._effort}}
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._system,
            tools=self._tools,
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},
            messages=self._messages,
            **extra,
        )
        # Keep the assistant turn exactly as returned, thinking blocks included. Current models
        # need those blocks passed back unchanged on the next request.
        self._messages.append({"role": "assistant", "content": response.content})

        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None) if details is not None else None
            reason = f" ({category})" if category else ""
            raise AgentError(f"the model declined to continue{reason}")
        if stop == "max_tokens":
            raise AgentError("the model ran out of output tokens before choosing a step")
        if stop not in _NORMAL_STOPS:
            raise AgentError(f"the model stopped unexpectedly ({stop})")

        tool_use = next(
            (b for b in response.content if getattr(b, "type", None) == "tool_use"), None
        )
        if tool_use is None:
            return AgentStep(kind="finish", message=_text_of(response.content) or "Done.")

        self._pending_tool_use_id = tool_use.id
        tool, _, operation = tool_use.name.partition("__")
        return AgentStep(
            kind="tool",
            tool=tool,
            operation=operation,
            arguments=dict(tool_use.input or {}),
            message=_text_of(response.content) or f"Calling {tool_use.name}.",
        )


def _text_of(content: list[Any]) -> str:
    parts = [b.text for b in content if getattr(b, "type", None) == "text"]
    return " ".join(p.strip() for p in parts if p).strip()


def _result_text(entry: dict[str, Any]) -> str:
    """Turn a broker outcome into the tool_result text the model sees next turn."""
    outcome = entry.get("outcome")
    if outcome == "executed":
        return json.dumps(entry.get("result") or {})
    if outcome == "failed":
        return f"The tool failed: {entry.get('error') or 'unknown error'}"
    if outcome == "denied":
        return _DENIED
    if outcome == "timed_out":
        return _TIMED_OUT
    if outcome == "blocked_revoked":
        return _REVOKED
    return f"Action outcome: {outcome}."
