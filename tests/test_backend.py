from __future__ import annotations

import httpx
import pytest

from atuin_ai_adapter.backend import (
    BackendClient,
    BackendConnectionError,
    BackendDone,
    BackendError,
    BackendTextDelta,
)
from tests.conftest import load_stream


@pytest.mark.asyncio
async def test_stream_chat_happy_simple(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    client = BackendClient(base_url="http://test", timeout=30)
    events = [e async for e in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m")]
    chunks = [e.content for e in events if isinstance(e, BackendTextDelta)]
    assert chunks == ["find", " . -size", " +100M"]
    assert any(isinstance(e, BackendDone) for e in events)
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_happy_long(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=load_stream("happy_long"))
    client = BackendClient(base_url="http://test", timeout=30)
    events = [e async for e in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m")]
    chunks = [e.content for e in events if isinstance(e, BackendTextDelta)]
    assert "".join(chunks).endswith("by size.")
    assert len(chunks) >= 9
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_with_role_only_chunk(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        text=load_stream("with_role_chunk"),
    )
    client = BackendClient(base_url="http://test", timeout=30)
    events = [e async for e in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m")]
    chunks = [e.content for e in events if isinstance(e, BackendTextDelta)]
    assert any(c == "find . -size +100M" for c in chunks)
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_malformed_json(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        text=load_stream("malformed_json"),
    )
    client = BackendClient(base_url="http://test", timeout=30)
    events = [e async for e in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m")]
    assert any(isinstance(e, BackendError) and "parse" in e.message.lower() for e in events)
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_upstream_500(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test/v1/chat/completions",
        status_code=500,
        text=load_stream("upstream_500"),
    )
    client = BackendClient(base_url="http://test", timeout=30)
    events = [e async for e in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m")]
    assert any(isinstance(e, BackendError) and "500" in e.message for e in events)
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_unreachable(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_exception(httpx.ConnectError("connection refused"))
    client = BackendClient(base_url="http://test", timeout=30)
    with pytest.raises(BackendConnectionError, match="Cannot reach model server"):
        async for _ in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m"):
            pass
    await client.close()


@pytest.mark.asyncio
async def test_stream_chat_ignores_non_data_lines(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    stream = ": ping\n\n" + load_stream("happy_simple")
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=stream)
    client = BackendClient(base_url="http://test", timeout=30)
    events = [e async for e in client.stream_chat(messages=[{"role": "user", "content": "x"}], model="m")]
    chunks = [e.content for e in events if isinstance(e, BackendTextDelta)]
    assert chunks[:3] == ["find", " . -size", " +100M"]
    await client.close()


@pytest.mark.asyncio
async def test_health_check_success(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(method="GET", url="http://test/v1/models", status_code=200)
    client = BackendClient(base_url="http://test", timeout=30)
    assert await client.health_check() is True
    await client.close()


@pytest.mark.asyncio
async def test_health_check_failure(httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_exception(httpx.ConnectError("down"))
    client = BackendClient(base_url="http://test", timeout=30)
    assert await client.health_check() is False
    await client.close()
