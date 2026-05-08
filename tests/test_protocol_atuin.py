from __future__ import annotations

import pytest
from pydantic import ValidationError

from atuin_ai_adapter.protocol.atuin import (
    AtuinChatRequest,
    AtuinDoneEvent,
    AtuinErrorEvent,
    AtuinTextEvent,
)


def test_parse_minimal_valid_request() -> None:
    request = AtuinChatRequest.model_validate(
        {"messages": [{"role": "user", "content": "hello"}], "invocation_id": "inv-1"}
    )

    assert request.context is None
    assert request.session_id is None
    assert request.config is None


def test_parse_full_request() -> None:
    request = AtuinChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "context": {
                "os": "linux",
                "shell": "zsh",
                "distro": "arch",
                "pwd": "/tmp",
                "last_command": "ls",
            },
            "config": {
                "capabilities": ["client_invocations"],
                "user_contexts": ["ctx"],
                "skills": [],
                "skills_overflow": "",
            },
            "invocation_id": "inv-1",
            "session_id": "session-1",
        }
    )

    assert request.context is not None
    assert request.context.os == "linux"
    assert request.config is not None
    assert request.config.capabilities == ["client_invocations"]
    assert request.session_id == "session-1"


def test_extra_fields_ignored() -> None:
    request = AtuinChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "invocation_id": "inv-1",
            "foo": "bar",
        }
    )

    assert not hasattr(request, "foo")


def test_missing_required_messages() -> None:
    with pytest.raises(ValidationError):
        AtuinChatRequest.model_validate({"invocation_id": "inv-1"})


def test_missing_required_invocation_id() -> None:
    with pytest.raises(ValidationError):
        AtuinChatRequest.model_validate({"messages": [{"role": "user", "content": "hello"}]})


def test_context_partial_fields() -> None:
    request = AtuinChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "context": {"os": "linux", "shell": "zsh"},
            "invocation_id": "inv-1",
        }
    )

    assert request.context is not None
    assert request.context.os == "linux"
    assert request.context.pwd is None
    assert request.context.last_command is None


def test_text_event_serialization() -> None:
    assert AtuinTextEvent(content="hello").model_dump_json() == '{"content":"hello"}'


def test_done_event_serialization() -> None:
    assert AtuinDoneEvent(session_id="abc").model_dump_json() == '{"session_id":"abc"}'


def test_error_event_serialization() -> None:
    assert AtuinErrorEvent(message="boom").model_dump_json() == '{"message":"boom"}'
