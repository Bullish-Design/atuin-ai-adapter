from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


# Atuin request models


class AtuinContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    os: str | None = None
    shell: str | None = None
    distro: str | None = None
    pwd: str | None = None
    last_command: str | None = None


class AtuinSkillSummary(BaseModel):
    name: str
    description: str


class AtuinConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capabilities: list[str] = []
    user_contexts: list[str] = []
    skills: list[AtuinSkillSummary] = []
    skills_overflow: str | None = None


class AtuinChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[dict[str, Any]]
    context: AtuinContext | None = None
    config: AtuinConfig | None = None
    invocation_id: str
    session_id: str | None = None


# SSE event models


class AtuinTextEvent(BaseModel):
    content: str


class AtuinToolCallEvent(BaseModel):
    id: str
    name: str
    input: dict[str, Any]


class AtuinToolResultEvent(BaseModel):
    tool_use_id: str
    content: str
    is_error: bool = False
    remote: bool = False
    content_length: int | None = None


class AtuinStatusEvent(BaseModel):
    state: str


class AtuinDoneEvent(BaseModel):
    session_id: str


class AtuinErrorEvent(BaseModel):
    message: str


# SSE frame builders


def format_sse(event: str, data: str) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {data}\n\n"


def text_event(content: str) -> str:
    """Format an Atuin 'text' SSE event."""
    return format_sse("text", AtuinTextEvent(content=content).model_dump_json())


def tool_call_event(id: str, name: str, input: dict[str, Any]) -> str:
    """Format an Atuin 'tool_call' SSE event."""
    return format_sse(
        "tool_call",
        AtuinToolCallEvent(id=id, name=name, input=input).model_dump_json(),
    )


def tool_result_event(
    tool_use_id: str,
    content: str,
    is_error: bool = False,
    remote: bool = False,
    content_length: int | None = None,
) -> str:
    """Format an Atuin 'tool_result' SSE event."""
    return format_sse(
        "tool_result",
        AtuinToolResultEvent(
            tool_use_id=tool_use_id,
            content=content,
            is_error=is_error,
            remote=remote,
            content_length=content_length,
        ).model_dump_json(),
    )


def status_event(state: str) -> str:
    """Format an Atuin 'status' SSE event."""
    return format_sse("status", AtuinStatusEvent(state=state).model_dump_json())


def done_event(session_id: str) -> str:
    """Format an Atuin 'done' SSE event."""
    return format_sse("done", AtuinDoneEvent(session_id=session_id).model_dump_json())


def error_event(message: str) -> str:
    """Format an Atuin 'error' SSE event."""
    return format_sse("error", AtuinErrorEvent(message=message).model_dump_json())
