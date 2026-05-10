from __future__ import annotations

import json
import logging

from atuin_ai_adapter.translator import flatten_content_blocks, translate_messages


def test_flatten_string() -> None:
    assert flatten_content_blocks("hello") == "hello"


def test_flatten_non_string_non_list() -> None:
    assert flatten_content_blocks(123) == "123"


def test_translate_simple_text_passthrough() -> None:
    messages = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi there"}]
    assert translate_messages(messages, flatten_tools=False) == messages


def test_translate_assistant_tool_use() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me read that."},
                {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "foo.rs"}},
            ],
        }
    ]
    result = translate_messages(messages, flatten_tools=False)
    assert result[0]["role"] == "assistant"
    assert result[0]["content"] == "Let me read that."
    assert json.loads(result[0]["tool_calls"][0]["function"]["arguments"]) == {"file_path": "foo.rs"}  # type: ignore[index]


def test_translate_user_tool_result() -> None:
    messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tc_001", "content": "file contents..."}],
        }
    ]
    result = translate_messages(messages, flatten_tools=False)
    assert result == [{"role": "tool", "tool_call_id": "tc_001", "content": "file contents..."}]


def test_translate_flatten_tools_true() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "x.rs"}},
            ],
        }
    ]
    result = translate_messages(messages, flatten_tools=True)
    assert "[Tool call: read_file" in result[0]["content"]


def test_translate_structured_logs_and_stringifies_non_list_content(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.WARNING)
    messages = [{"role": "user", "content": 42}]

    result = translate_messages(messages, flatten_tools=False)

    assert result == [{"role": "user", "content": "42"}]
    assert "Unexpected content type: int" in caplog.text


def test_translate_structured_unknown_assistant_block_type(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.WARNING)
    messages = [{"role": "assistant", "content": [{"type": "mystery", "foo": "bar"}]}]

    result = translate_messages(messages, flatten_tools=False)

    assert result == [
        {"role": "assistant", "content": '[Unknown block (type=mystery): {"type": "mystery", "foo": "bar"}]'}
    ]
    assert "Unknown assistant content block type: mystery" in caplog.text


def test_translate_structured_unknown_user_block_type(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.WARNING)
    messages = [{"role": "user", "content": [{"type": "mystery", "foo": "bar"}]}]

    result = translate_messages(messages, flatten_tools=False)

    assert result == [{"role": "user", "content": '[Unknown block (type=mystery): {"type": "mystery", "foo": "bar"}]'}]
    assert "Unknown user content block type: mystery" in caplog.text


def test_translate_structured_unknown_role_falls_back_to_flattened(caplog) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.WARNING)
    messages = [{"role": "system", "content": [{"type": "mystery", "foo": "bar"}]}]

    result = translate_messages(messages, flatten_tools=False)

    assert result == [{"role": "system", "content": '[Unknown block: {"type": "mystery", "foo": "bar"}]'}]
    assert "Unknown content block type: mystery" in caplog.text


def test_translate_assistant_tool_use_missing_id_and_name_defaults() -> None:
    messages = [{"role": "assistant", "content": [{"type": "tool_use", "input": {"x": 1}}]}]

    result = translate_messages(messages, flatten_tools=False)

    assert result == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "", "type": "function", "function": {"name": "", "arguments": '{"x": 1}'}}],
        }
    ]
