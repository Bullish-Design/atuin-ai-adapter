from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.service import handle_chat
from atuin_ai_adapter.vllm_client import VllmError
from tests.conftest import extract_events, load_call, parse_sse_frames


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

    async def stream_chat(self, request: object) -> AsyncIterator[str | None]:
        del request
        if self.error is not None and self.fail_after is None:
            raise self.error
        for idx, delta in enumerate(self.deltas):
            if self.fail_after is not None and idx >= self.fail_after:
                raise self.error or VllmError("boom")
            yield delta


def _req_from_fixture(name: str) -> AtuinChatRequest:
    return AtuinChatRequest.model_validate(load_call(name))


def _settings() -> Settings:
    return Settings.model_validate({"vllm_model": "test-model"})


async def _collect_frames(req: AtuinChatRequest, client: FakeVllmClient) -> list[dict[str, object]]:
    frames = [frame async for frame in handle_chat(req, client, _settings())]
    return parse_sse_frames("".join(frames))


@pytest.mark.asyncio
async def test_happy_path() -> None:
    frames = await _collect_frames(_req_from_fixture("minimal"), FakeVllmClient(["hello", " ", "world"]))
    assert extract_events(frames) == ["text", "text", "text", "done"]


@pytest.mark.asyncio
async def test_session_id_echo() -> None:
    req = AtuinChatRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "x"}],
            "invocation_id": "inv-1",
            "session_id": "my-session",
        }
    )
    frames = await _collect_frames(req, FakeVllmClient(["ok"]))
    assert frames[-1]["data"]["session_id"] == "my-session"  # type: ignore[index]


@pytest.mark.asyncio
async def test_session_id_generation() -> None:
    frames = await _collect_frames(_req_from_fixture("minimal"), FakeVllmClient(["ok"]))
    assert isinstance(frames[-1]["data"]["session_id"], str)  # type: ignore[index]


@pytest.mark.asyncio
async def test_upstream_error() -> None:
    frames = await _collect_frames(_req_from_fixture("minimal"), FakeVllmClient(error=VllmError("down")))
    assert extract_events(frames) == ["error", "done"]


@pytest.mark.asyncio
async def test_midstream_error() -> None:
    frames = await _collect_frames(
        _req_from_fixture("minimal"),
        FakeVllmClient(deltas=["a", "b", "c"], fail_after=2, error=VllmError("midstream")),
    )
    assert extract_events(frames) == ["text", "text", "error", "done"]


@pytest.mark.asyncio
async def test_none_deltas_skipped() -> None:
    frames = await _collect_frames(_req_from_fixture("minimal"), FakeVllmClient([None, "hello", None]))
    assert extract_events(frames) == ["text", "done"]


@pytest.mark.asyncio
async def test_empty_deltas_skipped() -> None:
    frames = await _collect_frames(_req_from_fixture("minimal"), FakeVllmClient(["", "hello", ""]))
    assert extract_events(frames) == ["text", "done"]


@pytest.mark.asyncio
async def test_fixture_with_tools_flows() -> None:
    frames = await _collect_frames(_req_from_fixture("with_tools"), FakeVllmClient(["tool result", " ok"]))
    assert extract_events(frames)[-1] == "done"


@pytest.mark.asyncio
async def test_internal_exception_returns_generic_error() -> None:
    frames = await _collect_frames(_req_from_fixture("minimal"), FakeVllmClient(error=RuntimeError("oops")))
    assert frames[0]["data"]["message"] == "Internal adapter error"  # type: ignore[index]
    assert extract_events(frames) == ["error", "done"]
