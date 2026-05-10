from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from atuin_ai_adapter.backend import BackendConnectionError, BackendDone, BackendError, BackendTextDelta
from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.orchestrator import handle_chat
from atuin_ai_adapter.protocol import AtuinChatRequest
from tests.conftest import extract_events, load_call, parse_sse_frames


class FakeBackendClient:
    def __init__(
        self,
        events: list[object] | None = None,
        fail_after: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.events = events or []
        self.fail_after = fail_after
        self.error = error

    async def stream_chat(self, **kwargs: object) -> AsyncIterator[object]:
        del kwargs
        if self.error is not None and self.fail_after is None:
            raise self.error
        for idx, event in enumerate(self.events):
            if self.fail_after is not None and idx >= self.fail_after:
                raise self.error or BackendConnectionError("boom")
            yield event


def _req_from_fixture(name: str) -> AtuinChatRequest:
    return AtuinChatRequest.model_validate(load_call(name))


def _settings() -> Settings:
    return Settings.model_validate({"vllm_model": "test-model"})


async def _collect_frames(req: AtuinChatRequest, client: FakeBackendClient) -> list[dict[str, object]]:
    frames = [frame async for frame in handle_chat(req, client, _settings())]
    return parse_sse_frames("".join(frames))


@pytest.mark.asyncio
async def test_happy_path() -> None:
    frames = await _collect_frames(
        _req_from_fixture("minimal"),
        FakeBackendClient([BackendTextDelta("hello"), BackendTextDelta(" "), BackendTextDelta("world"), BackendDone()]),
    )
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
    frames = await _collect_frames(req, FakeBackendClient([BackendTextDelta("ok"), BackendDone()]))
    assert frames[-1]["data"]["session_id"] == "my-session"  # type: ignore[index]


@pytest.mark.asyncio
async def test_session_id_generation() -> None:
    frames = await _collect_frames(
        _req_from_fixture("minimal"),
        FakeBackendClient([BackendTextDelta("ok"), BackendDone()]),
    )
    assert isinstance(frames[-1]["data"]["session_id"], str)  # type: ignore[index]


@pytest.mark.asyncio
async def test_upstream_error() -> None:
    frames = await _collect_frames(
        _req_from_fixture("minimal"),
        FakeBackendClient([BackendError("down")]),
    )
    assert extract_events(frames) == ["error", "done"]


@pytest.mark.asyncio
async def test_midstream_error() -> None:
    frames = await _collect_frames(
        _req_from_fixture("minimal"),
        FakeBackendClient(
            events=[BackendTextDelta("a"), BackendTextDelta("b"), BackendTextDelta("c")],
            fail_after=2,
            error=BackendConnectionError("midstream"),
        ),
    )
    assert extract_events(frames) == ["text", "text", "error", "done"]


@pytest.mark.asyncio
async def test_fixture_with_tools_flows() -> None:
    frames = await _collect_frames(
        _req_from_fixture("with_tools"),
        FakeBackendClient([BackendTextDelta("tool result"), BackendTextDelta(" ok"), BackendDone()]),
    )
    assert extract_events(frames)[-1] == "done"


@pytest.mark.asyncio
async def test_internal_exception_returns_generic_error() -> None:
    frames = await _collect_frames(
        _req_from_fixture("minimal"),
        FakeBackendClient(error=RuntimeError("oops")),
    )
    assert frames[0]["data"]["message"] == "Internal adapter error"  # type: ignore[index]
    assert extract_events(frames) == ["error", "done"]
