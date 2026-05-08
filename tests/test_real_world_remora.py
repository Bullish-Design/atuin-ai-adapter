from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from atuin_ai_adapter.config import get_settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_WORLD") != "1",
    reason="Set RUN_REAL_WORLD=1 to run live remora-server integration tests.",
)


def _configure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_MODEL", os.getenv("REAL_VLLM_MODEL", "Qwen3.5-9B-UD-Q6_K_XL.gguf"))
    monkeypatch.setenv("VLLM_BASE_URL", os.getenv("REAL_VLLM_BASE_URL", "http://remora-server:8000"))
    monkeypatch.setenv("ADAPTER_API_TOKEN", "local-dev-token")
    get_settings.cache_clear()


def test_live_ready_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_env(monkeypatch)
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        resp = client.get("/health/ready")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "upstream": "reachable"}


def test_live_stream_response_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_env(monkeypatch)
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer local-dev-token"},
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "What command lists files by size in Linux? Return only command.",
                    }
                ],
                "context": {"os": "linux", "shell": "zsh", "pwd": "/home/user"},
                "invocation_id": "real-world-test-001",
            },
        )

    assert resp.status_code == 200
    assert "event: text" in resp.text
    assert "event: done" in resp.text
    assert '"session_id":"' in resp.text
