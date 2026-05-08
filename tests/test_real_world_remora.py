from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from atuin_ai_adapter.config import get_settings
from tests.conftest import fire_call, load_call, parse_sse_frames, save_response

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_WORLD") != "1",
    reason="Set RUN_REAL_WORLD=1 to run live remora-server integration tests.",
)


def _configure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_MODEL", os.getenv("REAL_VLLM_MODEL", "Qwen3.5-9B-UD-Q6_K_XL.gguf"))
    monkeypatch.setenv("VLLM_BASE_URL", os.getenv("REAL_VLLM_BASE_URL", "http://remora-server:8000"))
    monkeypatch.setenv("ADAPTER_API_TOKEN", "local-dev-token")
    get_settings.cache_clear()


def _client(monkeypatch: pytest.MonkeyPatch) -> TestClient:  # type: ignore[type-arg]
    _configure_env(monkeypatch)
    from atuin_ai_adapter.app import app

    return TestClient(app)


def test_live_ready_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(monkeypatch) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "upstream": "reachable"}


def test_live_stream_simple(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(monkeypatch) as client:
        status, body, frames = fire_call(client, "simple", token="local-dev-token")
    assert status == 200
    assert any(f["event"] == "text" for f in frames)
    assert frames[-1]["event"] == "done"
    save_response("real_world_simple", body)


def test_live_stream_conversation(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client(monkeypatch) as client:
        status, body, frames = fire_call(client, "conversation", token="local-dev-token")
    assert status == 200
    assert frames[-1]["event"] == "done"
    save_response("real_world_conversation", body)


def test_live_stream_with_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = load_call("with_tools")
    payload["invocation_id"] = "real-world-tools-001"

    with _client(monkeypatch) as client:
        response = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer local-dev-token", "Accept": "text/event-stream"},
            json=payload,
        )

    assert response.status_code == 200
    frames = parse_sse_frames(response.text)
    assert frames[-1]["event"] == "done"
    save_response("real_world_with_tools", response.text)
