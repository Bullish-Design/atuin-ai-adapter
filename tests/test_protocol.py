from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from atuin_ai_adapter.protocol import (
    AtuinChatRequest,
    AtuinDoneEvent,
    AtuinErrorEvent,
    AtuinSkillSummary,
    AtuinStatusEvent,
    AtuinTextEvent,
    AtuinToolCallEvent,
    AtuinToolResultEvent,
    done_event,
    error_event,
    format_sse,
    status_event,
    text_event,
    tool_call_event,
    tool_result_event,
)


def test_parse_minimal_valid_request() -> None:
    request = AtuinChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "hello"}], "invocation_id": "inv-1"}
    )
    assert request.context is None
    assert request.session_id is None
    assert request.config is None


def test_missing_required_messages() -> None:
    with pytest.raises(ValidationError):
        AtuinChatRequest.model_validate({"invocation_id": "inv-1"})


def test_missing_required_invocation_id() -> None:
    with pytest.raises(ValidationError):
        AtuinChatRequest.model_validate({"messages": [{"role": "user", "content": "hello"}]})


def test_text_event_serialization() -> None:
    assert AtuinTextEvent(content="hello").model_dump_json() == '{"content":"hello"}'


def test_done_event_serialization() -> None:
    assert AtuinDoneEvent(session_id="abc").model_dump_json() == '{"session_id":"abc"}'


def test_error_event_serialization() -> None:
    assert AtuinErrorEvent(message="boom").model_dump_json() == '{"message":"boom"}'


def test_format_sse_frame() -> None:
    assert format_sse("text", '{"content":"hi"}') == 'event: text\ndata: {"content":"hi"}\n\n'


def test_text_event_output() -> None:
    assert text_event("hello world") == 'event: text\ndata: {"content":"hello world"}\n\n'


def test_done_event_output() -> None:
    output = done_event("session-123")
    assert '"session_id":"session-123"' in output


def test_error_event_output() -> None:
    output = error_event("something broke")
    assert '"message":"something broke"' in output


def test_json_escaping() -> None:
    output = text_event("line1\nline2")
    data_line = output.splitlines()[1].removeprefix("data: ")
    parsed = json.loads(data_line)
    assert parsed["content"] == "line1\nline2"


def test_quotes_in_content() -> None:
    output = text_event('say "hello"')
    data_line = output.splitlines()[1].removeprefix("data: ")
    parsed = json.loads(data_line)
    assert parsed["content"] == 'say "hello"'


def test_tool_call_event() -> None:
    result = tool_call_event("call_123", "suggest_command", {"command": "ls"})
    assert result.startswith("event: tool_call\n")


def test_tool_result_event() -> None:
    result = tool_result_event("call_123", "file contents", remote=True, content_length=13)
    assert result.startswith("event: tool_result\n")


def test_status_event() -> None:
    result = status_event("Thinking")
    assert result.startswith("event: status\n")


def test_tool_call_event_model() -> None:
    event = AtuinToolCallEvent(id="call_1", name="read_file", input={"file_path": "x"})
    data = event.model_dump()
    assert data["id"] == "call_1"


def test_tool_result_event_model() -> None:
    event = AtuinToolResultEvent(tool_use_id="call_1", content="ok")
    assert event.model_dump()["tool_use_id"] == "call_1"


def test_status_event_model() -> None:
    event = AtuinStatusEvent(state="Processing")
    assert event.model_dump() == {"state": "Processing"}


def test_skill_summary_model() -> None:
    skill = AtuinSkillSummary(name="deploy", description="Deploy to prod")
    assert skill.name == "deploy"
    assert skill.description == "Deploy to prod"
