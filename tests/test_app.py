from __future__ import annotations

import httpx

from tests.conftest import extract_events, fire_call, load_call, load_stream, parse_sse_frames


def test_happy_path_end_to_end(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    status, body, frames = fire_call(adapter_client, "simple", save_as="simple_happy")
    assert status == 200
    assert "event: text" in body
    assert extract_events(frames)[-1] == "done"


def test_auth_rejection(adapter_client) -> None:  # type: ignore[no-untyped-def]
    status, _body, frames = fire_call(adapter_client, "auth_bad_token", token="wrong-token")
    assert status == 401
    assert frames == []


def test_missing_auth_header(adapter_client) -> None:  # type: ignore[no-untyped-def]
    status, _body, frames = fire_call(adapter_client, "simple", token="")
    assert status == 401
    assert frames == []


def test_invalid_request_body(adapter_client) -> None:  # type: ignore[no-untyped-def]
    response = adapter_client.post(
        "/api/cli/chat",
        headers={"Authorization": "Bearer test-token"},
        json={"invocation_id": "inv-1"},
    )
    assert response.status_code == 422


def test_health_endpoint(adapter_client) -> None:  # type: ignore[no-untyped-def]
    response = adapter_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_up(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(method="GET", url="http://test-upstream/v1/models", status_code=200)
    response = adapter_client.get("/health/ready")
    assert response.status_code == 200


def test_readiness_down(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_exception(httpx.ConnectError("down"))
    response = adapter_client.get("/health/ready")
    assert response.status_code == 503


def test_upstream_error_sse_error(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        status_code=500,
        text=load_stream("upstream_500"),
    )
    _status, body, _frames = fire_call(adapter_client, "simple", save_as="simple_upstream_500")
    assert "event: error" in body
    assert "event: done" in body


def test_session_id_round_trip(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    status, _body, frames = fire_call(adapter_client, "conversation")
    assert status == 200
    assert frames[-1]["data"]["session_id"] == "session-abc-123"  # type: ignore[index]


def test_call_fixture_minimal(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    status, _body, frames = fire_call(adapter_client, "minimal", save_as="minimal_happy")
    assert status == 200
    assert extract_events(frames)[-1] == "done"


def test_call_fixture_no_context(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    status, _body, frames = fire_call(adapter_client, "no_context", save_as="no_context_happy")
    assert status == 200
    assert extract_events(frames).count("text") > 0


def test_call_fixture_with_tools(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    status, _body, frames = fire_call(adapter_client, "with_tools", save_as="with_tools_happy")
    assert status == 200
    assert extract_events(frames)[-1] == "done"


def test_call_fixture_conversation(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_simple"),
    )
    status, _body, frames = fire_call(adapter_client, "conversation", save_as="conversation_happy")
    assert status == 200
    assert extract_events(frames)[-1] == "done"


def test_malformed_upstream_json_returns_error(adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("malformed_json"),
    )
    status, body, frames = fire_call(adapter_client, "simple", save_as="simple_malformed")
    assert status == 200
    assert "event: error" in body
    assert extract_events(frames)[-1] == "done"


def test_concurrent_requests(httpx_mock, adapter_env) -> None:  # type: ignore[no-untyped-def]
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_long"),
    )
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_long"),
    )
    httpx_mock.add_response(
        method="POST",
        url="http://test-upstream/v1/chat/completions",
        text=load_stream("happy_long"),
    )

    from fastapi.testclient import TestClient

    from atuin_ai_adapter.app import app

    statuses: list[int] = []
    with TestClient(app) as client:
        for inv_id in ["inv-a", "inv-b", "inv-c"]:
            payload = load_call("simple")
            payload["invocation_id"] = inv_id
            resp = client.post(
                "/api/cli/chat",
                headers={"Authorization": "Bearer test-token", "Accept": "text/event-stream"},
                json=payload,
            )
            statuses.append(resp.status_code)
    assert statuses == [200, 200, 200]


def test_missing_messages_field_returns_422(adapter_client) -> None:  # type: ignore[no-untyped-def]
    response = adapter_client.post(
        "/api/cli/chat",
        headers={"Authorization": "Bearer test-token"},
        json={"invocation_id": "inv-missing-messages"},
    )
    assert response.status_code == 422


def test_missing_invocation_id_field_returns_422(adapter_client) -> None:  # type: ignore[no-untyped-def]
    response = adapter_client.post(
        "/api/cli/chat",
        headers={"Authorization": "Bearer test-token"},
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 422


class TestToolCallIntegration:
    def test_tool_call_sse_event_emitted(self, adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        stream_body = load_stream("with_tool_call")
        httpx_mock.add_response(
            url="http://test-upstream/v1/chat/completions",
            content=stream_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
        call_data = load_call("simple")
        response = adapter_client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token", "Accept": "text/event-stream"},
            json=call_data,
        )
        assert response.status_code == 200
        frames = parse_sse_frames(response.text)
        event_types = extract_events(frames)
        assert "status" in event_types
        assert "text" in event_types
        assert "tool_call" in event_types
        assert "done" in event_types

    def test_continuation_request(self, adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        stream_body = load_stream("happy_simple")
        httpx_mock.add_response(
            url="http://test-upstream/v1/chat/completions",
            content=stream_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
        call_data = load_call("continuation")
        response = adapter_client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token", "Accept": "text/event-stream"},
            json=call_data,
        )
        assert response.status_code == 200
        frames = parse_sse_frames(response.text)
        assert any(f["event"] == "done" for f in frames)

    def test_skills_in_request(self, adapter_client, httpx_mock) -> None:  # type: ignore[no-untyped-def]
        stream_body = load_stream("happy_simple")
        httpx_mock.add_response(
            url="http://test-upstream/v1/chat/completions",
            content=stream_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
        call_data = load_call("with_skills")
        response = adapter_client.post(
            "/api/cli/chat",
            headers={"Authorization": "Bearer test-token", "Accept": "text/event-stream"},
            json=call_data,
        )
        assert response.status_code == 200
        frames = parse_sse_frames(response.text)
        assert any(f["event"] == "done" for f in frames)
