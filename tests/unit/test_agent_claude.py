"""The live Claude adapter, driven by a fake client so no key or network is needed.

These tests prove the adapter parses tool-use into an AgentStep, threads results back as a
tool_result, and ends the run on a plain text turn. The governance tests already cover what happens
after a step is proposed, so here we only check the model-to-broker boundary.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import pytest

from warden.agent import AgentError
from warden.agent.claude import ClaudeAgent
from warden.tools import ToolRegistry


@dataclass
class _Text:
    text: str
    type: str = "text"


@dataclass
class _ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class _Thinking:
    thinking: str
    signature: str
    type: str = "thinking"


@dataclass
class _StopDetails:
    category: str | None = None


@dataclass
class _Response:
    content: list[Any]
    stop_reason: str = "tool_use"
    stop_details: Any = None


@dataclass
class _FakeMessages:
    scripted: list[_Response]
    calls: list[dict[str, Any]] = field(default_factory=list)
    _index: int = 0

    async def create(self, **kwargs: Any) -> _Response:
        # Snapshot the arguments as sent: the adapter reuses and mutates the same messages list.
        self.calls.append(copy.deepcopy(kwargs))
        response = self.scripted[self._index]
        self._index += 1
        return response


@dataclass
class _FakeClient:
    messages: _FakeMessages


def _agent(scripted: list[_Response]) -> tuple[ClaudeAgent, _FakeClient]:
    client = _FakeClient(messages=_FakeMessages(scripted=scripted))
    agent = ClaudeAgent(
        client=client, tool_schemas=ToolRegistry().anthropic_tool_schemas(), model="test-model"
    )
    return agent, client


async def test_tool_use_turn_becomes_a_tool_step():
    scripted = [
        _Response(
            content=[
                _Text("Looking up the user."),
                _ToolUse(id="tu_1", name="directory__lookup_user", input={"user_id": "jordan.lee"}),
            ]
        )
    ]
    agent, client = _agent(scripted)
    step = await agent.propose("offboard jordan.lee", [])

    assert step.kind == "tool"
    assert step.tool == "directory"
    assert step.operation == "lookup_user"
    assert step.arguments == {"user_id": "jordan.lee"}
    assert step.message == "Looking up the user."
    # The first turn seeds the conversation with the task as a user message.
    assert client.messages.calls[0]["messages"][0]["role"] == "user"
    assert client.messages.calls[0]["tool_choice"]["disable_parallel_tool_use"] is True


async def test_result_is_fed_back_as_tool_result():
    scripted = [
        _Response(
            content=[_ToolUse(id="tu_1", name="directory__lookup_user", input={"user_id": "x"})]
        ),
        _Response(content=[_Text("All done.")], stop_reason="end_turn"),
    ]
    agent, client = _agent(scripted)
    await agent.propose("task", [])
    history = [{"tool": "directory", "operation": "lookup_user", "outcome": "executed",
                "result": {"enabled": True}, "error": None}]
    step = await agent.propose("task", history)

    assert step.kind == "finish"
    assert step.message == "All done."
    # The second call carries the tool_result that references the first tool_use id.
    second_messages = client.messages.calls[1]["messages"]
    tool_result_msg = second_messages[-1]
    assert tool_result_msg["role"] == "user"
    block = tool_result_msg["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "tu_1"
    assert "enabled" in block["content"]


async def test_denied_outcome_is_reported_to_the_model():
    scripted = [
        _Response(content=[_ToolUse(id="tu_1", name="directory__disable_account", input={})]),
        _Response(content=[_Text("Understood, skipping it.")], stop_reason="end_turn"),
    ]
    agent, client = _agent(scripted)
    await agent.propose("task", [])
    history = [{"tool": "directory", "operation": "disable_account", "outcome": "denied",
                "result": None, "error": None}]
    await agent.propose("task", history)

    block = client.messages.calls[1]["messages"][-1]["content"][0]
    assert "denied" in block["content"].lower()


async def test_thinking_blocks_go_back_to_the_model_unchanged():
    # Current models need the assistant turn passed back exactly as returned, thinking included.
    thinking = _Thinking(thinking="", signature="sig-abc")
    scripted = [
        _Response(content=[thinking, _ToolUse(id="tu_1", name="directory__lookup_user",
                                               input={"user_id": "jordan.lee"})]),
        _Response(content=[_Text("Done.")], stop_reason="end_turn"),
    ]
    agent, client = _agent(scripted)
    await agent.propose("task", [])
    history = [{"tool": "directory", "operation": "lookup_user", "outcome": "executed",
                "result": {}, "error": None}]
    await agent.propose("task", history)

    assistant_turn = client.messages.calls[1]["messages"][1]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"][0] == thinking
    assert assistant_turn["content"][1].type == "tool_use"


async def test_a_refusal_is_an_error_not_a_finish():
    scripted = [_Response(content=[], stop_reason="refusal",
                          stop_details=_StopDetails(category="cyber"))]
    agent, _ = _agent(scripted)
    with pytest.raises(AgentError, match=r"declined to continue \(cyber\)"):
        await agent.propose("task", [])


async def test_running_out_of_tokens_is_not_mistaken_for_finishing():
    # Thinking counts against max_tokens. A truncated turn with no tool call must not end the run
    # as if the agent had decided it was done.
    scripted = [_Response(content=[_Text("I will now remove the")], stop_reason="max_tokens")]
    agent, _ = _agent(scripted)
    with pytest.raises(AgentError, match="ran out of output tokens"):
        await agent.propose("task", [])


async def test_an_unexpected_stop_reason_is_an_error():
    scripted = [_Response(content=[], stop_reason="model_context_window_exceeded")]
    agent, _ = _agent(scripted)
    with pytest.raises(AgentError, match="stopped unexpectedly"):
        await agent.propose("task", [])
