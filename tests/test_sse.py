from __future__ import annotations

import json

from atuin_ai_adapter.sse import done_event, error_event, format_sse, text_event


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
