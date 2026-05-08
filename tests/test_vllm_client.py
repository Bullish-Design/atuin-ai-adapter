from __future__ import annotations

import httpx
import pytest

from atuin_ai_adapter.protocol.openai import OpenAIChatMessage, OpenAIChatRequest
from atuin_ai_adapter.vllm_client import VllmClient, VllmError


@pytest.mark.asyncio
async def test_stream_chat_happy_path(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    stream = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"find"}}]}',
            'data: {"choices":[{"delta":{"content":" / -size"}}]}',
            'data: {"choices":[{"delta":{"content":" +100M"}}]}',
            "data: [DONE]",
        ]
    )
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=stream)

    client = VllmClient(base_url="http://test", timeout=30)
    req = OpenAIChatRequest(model="m", messages=[OpenAIChatMessage(role="user", content="x")])

    chunks: list[str | None] = []
    async for chunk in client.stream_chat(req):
        chunks.append(chunk)

    assert chunks == ["find", " / -size", " +100M"]
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_null_content(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    stream = "\n".join(
        [
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            "data: [DONE]",
        ]
    )
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=stream)

    client = VllmClient(base_url="http://test", timeout=30)
    req = OpenAIChatRequest(model="m", messages=[OpenAIChatMessage(role="user", content="x")])

    chunks: list[str | None] = []
    async for chunk in client.stream_chat(req):
        chunks.append(chunk)

    assert chunks == [None, "hello"]
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_upstream_500(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", status_code=500, text="boom")

    client = VllmClient(base_url="http://test", timeout=30)
    req = OpenAIChatRequest(model="m", messages=[OpenAIChatMessage(role="user", content="x")])

    with pytest.raises(VllmError, match="500"):
        async for _ in client.stream_chat(req):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_unreachable(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))

    client = VllmClient(base_url="http://test", timeout=30)
    req = OpenAIChatRequest(model="m", messages=[OpenAIChatMessage(role="user", content="x")])

    with pytest.raises(VllmError):
        async for _ in client.stream_chat(req):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_health_check_success(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(method="GET", url="http://test/v1/models", status_code=200)

    client = VllmClient(base_url="http://test", timeout=30)
    assert await client.health_check() is True
    await client.close()


@pytest.mark.asyncio
async def test_health_check_failure(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_exception(httpx.ConnectError("down"))

    client = VllmClient(base_url="http://test", timeout=30)
    assert await client.health_check() is False
    await client.close()
