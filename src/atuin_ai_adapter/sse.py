from __future__ import annotations

from atuin_ai_adapter.protocol.atuin import AtuinDoneEvent, AtuinErrorEvent, AtuinTextEvent


def format_sse(event: str, data: str) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {data}\n\n"


def text_event(content: str) -> str:
    """Format an Atuin 'text' SSE event."""
    return format_sse("text", AtuinTextEvent(content=content).model_dump_json())


def done_event(session_id: str) -> str:
    """Format an Atuin 'done' SSE event."""
    return format_sse("done", AtuinDoneEvent(session_id=session_id).model_dump_json())


def error_event(message: str) -> str:
    """Format an Atuin 'error' SSE event."""
    return format_sse("error", AtuinErrorEvent(message=message).model_dump_json())
