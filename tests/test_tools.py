from __future__ import annotations

import json

from atuin_ai_adapter.tools import (
    CAPABILITY_TOOL_MAP,
    ToolExecution,
    build_tool_registry,
    to_openai_tools,
)


def test_all_capabilities_returns_all_tools() -> None:
    all_caps = list(CAPABILITY_TOOL_MAP.keys())
    registry = build_tool_registry(all_caps)
    names = {t.name for t in registry}
    assert names == {
        "suggest_command",
        "load_skill",
        "atuin_history",
        "read_file",
        "edit_file",
        "write_file",
        "execute_shell_command",
    }


def test_empty_capabilities_returns_empty() -> None:
    assert build_tool_registry([]) == []


def test_single_capability() -> None:
    registry = build_tool_registry(["client_invocations"])
    assert len(registry) == 1
    assert registry[0].name == "suggest_command"
    assert registry[0].execution == ToolExecution.PSEUDO


def test_partial_capabilities() -> None:
    registry = build_tool_registry(["client_invocations", "client_v1_read_file"])
    names = {t.name for t in registry}
    assert names == {"suggest_command", "read_file"}


def test_unknown_capability_ignored() -> None:
    registry = build_tool_registry(["client_invocations", "future_capability_v99"])
    assert len(registry) == 1
    assert registry[0].name == "suggest_command"


def test_duplicate_capabilities_no_duplicate_tools() -> None:
    registry = build_tool_registry(["client_invocations", "client_invocations"])
    assert len(registry) == 1


def test_tool_execution_types() -> None:
    registry = build_tool_registry(list(CAPABILITY_TOOL_MAP.keys()))
    by_name = {t.name: t for t in registry}
    assert by_name["suggest_command"].execution == ToolExecution.PSEUDO
    assert by_name["read_file"].execution == ToolExecution.CLIENT
    assert by_name["execute_shell_command"].execution == ToolExecution.CLIENT


def test_converts_to_openai_format() -> None:
    registry = build_tool_registry(["client_invocations"])
    openai_tools = to_openai_tools(registry)
    assert len(openai_tools) == 1
    tool = openai_tools[0]
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "suggest_command"
    assert "parameters" in tool["function"]
    assert "description" in tool["function"]


def test_openai_tools_empty_registry() -> None:
    assert to_openai_tools([]) == []


def test_all_tools_have_valid_schemas() -> None:
    registry = build_tool_registry(list(CAPABILITY_TOOL_MAP.keys()))
    openai_tools = to_openai_tools(registry)
    for tool in openai_tools:
        assert tool["type"] == "function"
        func = tool["function"]
        assert isinstance(func["name"], str)
        assert isinstance(func["description"], str)
        params = func["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        assert "required" in params
        json.dumps(tool)


def test_suggest_command_schema() -> None:
    registry = build_tool_registry(["client_invocations"])
    openai_tools = to_openai_tools(registry)
    params = openai_tools[0]["function"]["parameters"]
    assert "command" in params["properties"]
    assert params["required"] == ["command"]


def test_execute_shell_command_schema() -> None:
    registry = build_tool_registry(["client_v1_execute_shell_command"])
    openai_tools = to_openai_tools(registry)
    params = openai_tools[0]["function"]["parameters"]
    assert "command" in params["properties"]
    assert "shell" in params["properties"]
    assert "timeout" in params["properties"]
    assert params["required"] == ["command"]
