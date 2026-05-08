"""Shared test fixtures for the atuin-ai-adapter test suite."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from atuin_ai_adapter.config import get_settings

FIXTURES = Path(__file__).parent / "fixtures"
CALLS = FIXTURES / "calls"
STREAMS = FIXTURES / "streams"
RESPONSES = FIXTURES / "responses"


def load_call(name: str) -> dict[str, Any]:
    path = CALLS / name if name.endswith(".json") else CALLS / f"{name}.json"
    return json.loads(path.read_text())


def load_stream(name: str) -> str:
    path = STREAMS / name if name.endswith(".txt") else STREAMS / f"{name}.txt"
    return path.read_text()


def save_response(name: str, body: str, *, tag: str = "") -> Path:
    RESPONSES.mkdir(parents=True, exist_ok=True)
    suffix = f"_{tag}" if tag else ""
    path = RESPONSES / f"{name}{suffix}.txt"
    header = f"# Captured: {datetime.now(tz=UTC).isoformat()}\n# Call: {name}\n\n"
    path.write_text(header + body)
    return path


def parse_sse_frames(body: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    current_event: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("event: "):
            current_event = stripped.removeprefix("event: ")
            continue
        if stripped.startswith("data: ") and current_event is not None:
            raw = stripped.removeprefix("data: ")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"_raw": raw}
            frames.append({"event": current_event, "data": data})
            current_event = None
    return frames


def extract_text(frames: list[dict[str, Any]]) -> str:
    return "".join(f["data"].get("content", "") for f in frames if f["event"] == "text")


def extract_events(frames: list[dict[str, Any]]) -> list[str]:
    return [str(f["event"]) for f in frames]


@pytest.fixture
def adapter_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_MODEL", "test-model")
    monkeypatch.setenv("ADAPTER_API_TOKEN", "test-token")
    monkeypatch.setenv("VLLM_BASE_URL", "http://test-upstream")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def adapter_client(adapter_env: None) -> TestClient:  # type: ignore[type-arg]
    from atuin_ai_adapter.app import app

    with TestClient(app) as client:
        yield client


def fire_call(
    client: TestClient,
    call_name: str,
    *,
    token: str = "test-token",
    save_as: str | None = None,
    tag: str = "",
) -> tuple[int, str, list[dict[str, Any]]]:
    call_data = load_call(call_name)
    headers = {"Accept": "text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.post("/api/cli/chat", headers=headers, json=call_data)
    if save_as is not None:
        save_response(save_as, response.text, tag=tag)
    frames = parse_sse_frames(response.text) if response.status_code == 200 else []
    return response.status_code, response.text, frames
