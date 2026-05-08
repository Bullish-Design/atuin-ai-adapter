from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

import pytest

from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.service import handle_chat
from atuin_ai_adapter.vllm_client import VllmError


class FakeVllmClient:
    def __init__(
        self,
        deltas: list[str | None] | None = None,
        fail_after: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.deltas = deltas or []
        self.fail_after = fail_after
        self.error = error

    async def stream_chat(self, request) -> AsyncIterator[str | None]:  # type: ignore[no-untyped-def]
        if self.error is not None and self.fail_after is None:
            raise self.error
        for idx, delta in enumerate(self.deltas):
            if self.fail_after is not None and idx >= self.fail_after:
                raise self.error or VllmError("boom")
            yield delta


def _request(payload: dict) -> AtuinChatRequest:
    return AtuinChatRequest.model_validate(payload)


def _settings() -> Settings:
    return Settings.model_validate({"vllm_model": "test-model"})


def _event_name(frame: str) -> str:
    return frame.splitlines()[0].removeprefix("event: ")


def _data(frame: str) -> dict:
    return json.loads(frame.splitlines()[1].removeprefix("data: "))


@pytest.mark.asyncio
async def test_happy_path() -> None:
    client = FakeVllmClient(deltas=["hello", " ", "world"])
    req = _request({"messages": [{"role": "user", "content": "x"}], "invocation_id": "inv-1"})

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    assert [_event_name(f) for f in frames] == ["text", "text", "text", "done"]
    assert _data(frames[0])["content"] == "hello"


@pytest.mark.asyncio
async def test_session_id_echo() -> None:
    client = FakeVllmClient(deltas=["ok"])
    req = _request(
        {
            "messages": [{"role": "user", "content": "x"}],
            "invocation_id": "inv-1",
            "session_id": "my-session",
        }
    )

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    assert _data(frames[-1])["session_id"] == "my-session"


@pytest.mark.asyncio
async def test_session_id_generation() -> None:
    client = FakeVllmClient(deltas=["ok"])
    req = _request({"messages": [{"role": "user", "content": "x"}], "invocation_id": "inv-1"})

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    sid = _data(frames[-1])["session_id"]
    assert re.fullmatch(r"[0-9a-f\-]{36}", sid)


@pytest.mark.asyncio
async def test_upstream_error() -> None:
    client = FakeVllmClient(error=VllmError("connection refused"))
    req = _request({"messages": [{"role": "user", "content": "x"}], "invocation_id": "inv-1"})

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    assert [_event_name(f) for f in frames] == ["error", "done"]


@pytest.mark.asyncio
async def test_midstream_error() -> None:
    client = FakeVllmClient(deltas=["a", "b", "c"], fail_after=2, error=VllmError("midstream"))
    req = _request({"messages": [{"role": "user", "content": "x"}], "invocation_id": "inv-1"})

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    assert [_event_name(f) for f in frames] == ["text", "text", "error", "done"]


@pytest.mark.asyncio
async def test_none_deltas_skipped() -> None:
    client = FakeVllmClient(deltas=[None, "hello", None])
    req = _request({"messages": [{"role": "user", "content": "x"}], "invocation_id": "inv-1"})

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    assert [_event_name(f) for f in frames] == ["text", "done"]


@pytest.mark.asyncio
async def test_empty_deltas_skipped() -> None:
    client = FakeVllmClient(deltas=["", "hello", ""])
    req = _request({"messages": [{"role": "user", "content": "x"}], "invocation_id": "inv-1"})

    frames = [frame async for frame in handle_chat(req, client, _settings())]

    assert [_event_name(f) for f in frames] == ["text", "done"]
