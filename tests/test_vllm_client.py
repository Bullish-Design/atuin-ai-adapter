from __future__ import annotations

import httpx
import pytest

from atuin_ai_adapter.protocol.openai import OpenAIChatMessage, OpenAIChatRequest
from atuin_ai_adapter.vllm_client import VllmClient, VllmError
from tests.conftest import load_stream


def _request() -> OpenAIChatRequest:
    return OpenAIChatRequest(model="m", messages=[OpenAIChatMessage(role="user", content="x")])


@pytest.mark.asyncio
async def test_stream_chat_happy_simple(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    client = VllmClient(base_url="http://test", timeout=30)
    chunks: list[str | None] = [chunk async for chunk in client.stream_chat(_request())]
    assert chunks == ["", "find", " . -size", " +100M", ""]
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_happy_long(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=load_stream("happy_long"))
    client = VllmClient(base_url="http://test", timeout=30)
    chunks: list[str | None] = [chunk async for chunk in client.stream_chat(_request())]
    assert "".join(c or "" for c in chunks).endswith("by size.")
    assert len(chunks) >= 10
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_with_role_only_chunk(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        text=load_stream("with_role_chunk"),
    )
    client = VllmClient(base_url="http://test", timeout=30)
    chunks: list[str | None] = [chunk async for chunk in client.stream_chat(_request())]
    assert chunks[0] is None
    assert any(c == "find . -size +100M" for c in chunks)
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_malformed_json(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        text=load_stream("malformed_json"),
    )
    client = VllmClient(base_url="http://test", timeout=30)
    with pytest.raises(VllmError, match="Failed to parse upstream response"):
        async for _ in client.stream_chat(_request()):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_upstream_500(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        status_code=500,
        text=load_stream("upstream_500"),
    )
    client = VllmClient(base_url="http://test", timeout=30)
    with pytest.raises(VllmError, match="500"):
        async for _ in client.stream_chat(_request()):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_unreachable(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    client = VllmClient(base_url="http://test", timeout=30)
    with pytest.raises(VllmError, match="Cannot reach upstream model server"):
        async for _ in client.stream_chat(_request()):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_ignores_non_data_lines(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    stream = ": ping\n\n" + load_stream("happy_simple")
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=stream)
    client = VllmClient(base_url="http://test", timeout=30)
    chunks: list[str | None] = [chunk async for chunk in client.stream_chat(_request())]
    assert chunks[:3] == ["", "find", " . -size"]
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
