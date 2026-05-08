from __future__ import annotations

from atuin_ai_adapter.protocol.openai import OpenAIChatMessage, OpenAIChatRequest


def test_construct_valid_request() -> None:
    request = OpenAIChatRequest(
        model="test-model",
        messages=[OpenAIChatMessage(role="user", content="hello")],
        temperature=0.7,
        max_tokens=256,
        top_p=0.9,
    )

    dumped = request.model_dump(exclude_none=True)
    assert dumped["stream"] is True
    assert dumped["model"] == "test-model"
    assert dumped["messages"] == [{"role": "user", "content": "hello"}]


def test_none_params_excluded() -> None:
    request = OpenAIChatRequest(
        model="test-model",
        messages=[OpenAIChatMessage(role="user", content="hello")],
    )

    dumped = request.model_dump(exclude_none=True)
    assert "temperature" not in dumped


def test_message_serialization() -> None:
    message = OpenAIChatMessage(role="user", content="hello")
    assert message.model_dump() == {"role": "user", "content": "hello"}
