from __future__ import annotations

from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.translator import build_openai_messages, flatten_content_blocks
from tests.conftest import load_call

PREAMBLE = "Custom preamble."


def _req(payload: dict) -> AtuinChatRequest:
    return AtuinChatRequest.model_validate(payload)


def test_simple_text_message() -> None:
    req = _req({"messages": [{"role": "user", "content": "hello"}], "invocation_id": "inv-1"})
    out = build_openai_messages(req, PREAMBLE)
    assert len(out) == 2
    assert out[1].content == "hello"


def test_system_prompt_includes_context() -> None:
    req = _req(
        {
            "messages": [],
            "context": {"os": "linux", "shell": "zsh", "pwd": "/home/test"},
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    text = out[0].content
    assert "OS: linux" in text
    assert "Shell: zsh" in text
    assert "Working directory: /home/test" in text


def test_system_prompt_omits_missing_context_fields() -> None:
    req = _req({"messages": [], "context": {"os": "linux"}, "invocation_id": "inv-1"})
    text = build_openai_messages(req, PREAMBLE)[0].content
    assert "OS: linux" in text
    assert "Shell:" not in text


def test_system_prompt_no_context() -> None:
    req = _req({"messages": [], "invocation_id": "inv-1"})
    text = build_openai_messages(req, PREAMBLE)[0].content
    assert PREAMBLE in text
    assert "Environment:" not in text


def test_system_prompt_user_contexts() -> None:
    req = _req(
        {
            "messages": [],
            "config": {"user_contexts": ["Always use sudo", "Prefer fish shell"]},
            "invocation_id": "inv-1",
        }
    )
    text = build_openai_messages(req, PREAMBLE)[0].content
    assert "User context:" in text
    assert "Always use sudo" in text
    assert "Prefer fish shell" in text


def test_multi_turn_roles() -> None:
    req = _req(
        {
            "messages": [
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ],
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    assert [m.role for m in out] == ["system", "user", "assistant", "user"]


def test_content_block_text() -> None:
    req = _req(
        {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hello"}]}],
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    assert out[1].content == "hello"


def test_content_block_tool_use() -> None:
    req = _req(
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "checking"},
                        {"type": "tool_use", "id": "1", "name": "run", "input": {"cmd": "ls"}},
                    ],
                }
            ],
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    assert "checking" in out[1].content
    assert "[Tool call: run(" in out[1].content


def test_content_block_tool_result() -> None:
    req = _req(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok", "is_error": False}],
                }
            ],
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    assert "[Tool result (1): ok]" in out[1].content


def test_content_block_tool_result_error() -> None:
    req = _req(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "1", "content": "boom", "is_error": True}],
                }
            ],
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    assert "[Tool error (1): boom]" in out[1].content


def test_unknown_block_type() -> None:
    req = _req(
        {
            "messages": [{"role": "user", "content": [{"type": "magic", "data": 42}]}],
            "invocation_id": "inv-1",
        }
    )
    out = build_openai_messages(req, PREAMBLE)
    assert "[Unknown block:" in out[1].content


def test_empty_messages_list() -> None:
    req = _req({"messages": [], "invocation_id": "inv-1"})
    out = build_openai_messages(req, PREAMBLE)
    assert len(out) == 1


def test_custom_system_prompt_template() -> None:
    req = _req({"messages": [], "invocation_id": "inv-1"})
    out = build_openai_messages(req, "Custom prompt.")
    assert out[0].content.startswith("Custom prompt.")


def test_flatten_string() -> None:
    assert flatten_content_blocks("hello") == "hello"


def test_flatten_non_string_non_list() -> None:
    assert flatten_content_blocks(123) == "123"


def test_fixture_simple_translates() -> None:
    req = AtuinChatRequest.model_validate(load_call("simple"))
    out = build_openai_messages(req, PREAMBLE)
    assert out[0].role == "system"
    assert out[-1].role == "user"


def test_fixture_conversation_translates() -> None:
    req = AtuinChatRequest.model_validate(load_call("conversation"))
    out = build_openai_messages(req, PREAMBLE)
    assert len(out) >= 3
    assert [m.role for m in out[1:]] == ["user", "assistant", "user"]


def test_fixture_with_tools_translates() -> None:
    req = AtuinChatRequest.model_validate(load_call("with_tools"))
    out = build_openai_messages(req, PREAMBLE)
    combined = "\n".join(m.content for m in out)
    assert "[Tool call:" in combined
    assert "[Tool result" in combined or "[Tool error" in combined
