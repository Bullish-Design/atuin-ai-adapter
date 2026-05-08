from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from atuin_ai_adapter.config import get_settings


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VLLM_MODEL", "test-model")
    monkeypatch.setenv("ADAPTER_API_TOKEN", "test-token")
    monkeypatch.setenv("VLLM_BASE_URL", "http://test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_happy_path_end_to_end(app_env, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    from atuin_ai_adapter.app import app

    stream = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"hello"}}]}',
            'data: {"choices":[{"delta":{"content":" world"}}]}',
            "data: [DONE]",
        ]
    )
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=stream)

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "hi"}], "invocation_id": "inv-1"},
        )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "event: text" in resp.text
    assert "event: done" in resp.text


def test_auth_rejection(app_env) -> None:
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer wrong-token"},
            json={"messages": [{"role": "user", "content": "hi"}], "invocation_id": "inv-1"},
        )

    assert resp.status_code == 401


def test_missing_auth_header(app_env) -> None:
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            json={"messages": [{"role": "user", "content": "hi"}], "invocation_id": "inv-1"},
        )

    assert resp.status_code == 401


def test_invalid_request_body(app_env) -> None:
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token"},
            json={"invocation_id": "inv-1"},
        )

    assert resp.status_code == 422


def test_health_endpoint(app_env) -> None:
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readiness_up(app_env, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    from atuin_ai_adapter.app import app

    httpx_mock.add_response(method="GET", url="http://test/v1/models", status_code=200)

    with TestClient(app) as client:
        resp = client.get("/health/ready")

    assert resp.status_code == 200


def test_readiness_down(app_env, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    from atuin_ai_adapter.app import app

    httpx_mock.add_exception(httpx.ConnectError("down"))

    with TestClient(app) as client:
        resp = client.get("/health/ready")

    assert resp.status_code == 503


def test_upstream_error_sse_error(app_env, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    from atuin_ai_adapter.app import app

    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", status_code=500, text="boom")

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token"},
            json={"messages": [{"role": "user", "content": "hi"}], "invocation_id": "inv-1"},
        )

    assert "event: error" in resp.text
    assert "event: done" in resp.text


def test_session_id_round_trip(app_env, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    from atuin_ai_adapter.app import app

    stream = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]
    )
    httpx_mock.add_response(method="POST", url="http://test/v1/chat/completions", text=stream)

    with TestClient(app) as client:
        resp = client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token"},
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "invocation_id": "inv-1",
                "session_id": "test-session-id",
            },
        )

    assert '"session_id":"test-session-id"' in resp.text
