# V2.1 Refactoring Guide — Step by Step

**Date:** 2026-05-09
**Reference:** `ATUIN_ADAPTER_V2_1.md` (full concept doc)

---

## Overview

This guide walks through refactoring `atuin-ai-adapter` from a text-only streaming bridge (V1) into a full Atuin AI backend with tool calling, command suggestions, continuation support, skills, and capability-driven behavior.

### What we're building

V1 does one thing: takes Atuin chat requests, forwards them to a vLLM backend as text, and streams text responses back via SSE.

V2.1 adds:
- **Tool-call passthrough** — capability-driven tool schemas sent to the backend, tool-call deltas accumulated and emitted as Atuin `tool_call` SSE events
- **Proper message translation** — Atuin's Anthropic-style `tool_use`/`tool_result` blocks translated to OpenAI format (instead of flattened to text)
- **System prompt composition** — dynamic prompt sections based on available tools, skills, and context
- **Backward compatibility** — `enable_tools = false` makes V2.1 behave identically to V1

### Module layout change

**V1 (current):**
```
src/atuin_ai_adapter/
    __init__.py
    app.py              # FastAPI routes, auth, lifespan
    config.py           # Settings
    service.py          # handle_chat() bridge logic
    sse.py              # SSE frame formatters
    translator.py       # flatten_content_blocks + build_openai_messages
    vllm_client.py      # VllmClient, stream_chat
    protocol/
        __init__.py
        atuin.py        # Atuin models (request + SSE events)
        openai.py       # OpenAI models (message + request)
```

**V2.1 (target):**
```
src/atuin_ai_adapter/
    __init__.py
    app.py              # FastAPI routes, auth, lifespan (updated imports)
    config.py           # Settings (extended with enable_tools, vllm_api_key)
    protocol.py         # Atuin models + all SSE event builders (merged)
    tools.py            # NEW: Tool registry, schemas, capability mapping
    orchestrator.py     # Replaces service.py — richer bridge logic
    backend.py          # Replaces vllm_client.py — BackendEvent + tool accumulation
    translator.py       # Extended: handles tool blocks properly
    prompt.py           # NEW: System prompt builder with sections
```

**Deleted:** `service.py`, `vllm_client.py`, `sse.py`, `protocol/` directory

### Phased approach

The refactor is split into 3 phases:

1. **Phase 1: Architecture refactor (text-only parity)** — new module layout, same V1 behavior, all tests pass
2. **Phase 2: Tool infrastructure** — tool schemas, accumulation, tool_call SSE emission, proper message translation
3. **Phase 3: Full integration** — prompt builder with tool instructions, skill support, E2E testing

Each phase has its own validation criteria. **Do not move to the next phase until all tests pass.**

---

## Phase 1: Architecture Refactor (Text-Only Parity)

**Goal:** Reorganize into the new module layout while preserving exact V1 behavior. All existing tests must pass (after updating imports).

---

### Step 1.1: Create `protocol.py`

This module consolidates `protocol/atuin.py`, `protocol/openai.py`, and `sse.py` into a single file. It also adds new SSE event models and builders that won't be used yet (Phase 2 will wire them).

**Create `src/atuin_ai_adapter/protocol.py`:**

```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


# ─── Atuin request models ───


class AtuinContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    os: str | None = None
    shell: str | None = None
    distro: str | None = None
    pwd: str | None = None
    last_command: str | None = None


class AtuinSkillSummary(BaseModel):
    name: str
    description: str


class AtuinConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capabilities: list[str] = []
    user_contexts: list[str] = []
    skills: list[AtuinSkillSummary] = []
    skills_overflow: str | None = None


class AtuinChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[dict[str, Any]]
    context: AtuinContext | None = None
    config: AtuinConfig | None = None
    invocation_id: str
    session_id: str | None = None


# ─── SSE event models ───


class AtuinTextEvent(BaseModel):
    content: str


class AtuinToolCallEvent(BaseModel):
    id: str
    name: str
    input: dict[str, Any]


class AtuinToolResultEvent(BaseModel):
    tool_use_id: str
    content: str
    is_error: bool = False
    remote: bool = False
    content_length: int | None = None


class AtuinStatusEvent(BaseModel):
    state: str


class AtuinDoneEvent(BaseModel):
    session_id: str


class AtuinErrorEvent(BaseModel):
    message: str


# ─── SSE frame builders ───


def format_sse(event: str, data: str) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {data}\n\n"


def text_event(content: str) -> str:
    """Format an Atuin 'text' SSE event."""
    return format_sse("text", AtuinTextEvent(content=content).model_dump_json())


def tool_call_event(id: str, name: str, input: dict[str, Any]) -> str:
    """Format an Atuin 'tool_call' SSE event."""
    return format_sse(
        "tool_call",
        AtuinToolCallEvent(id=id, name=name, input=input).model_dump_json(),
    )


def tool_result_event(
    tool_use_id: str,
    content: str,
    is_error: bool = False,
    remote: bool = False,
    content_length: int | None = None,
) -> str:
    """Format an Atuin 'tool_result' SSE event."""
    return format_sse(
        "tool_result",
        AtuinToolResultEvent(
            tool_use_id=tool_use_id,
            content=content,
            is_error=is_error,
            remote=remote,
            content_length=content_length,
        ).model_dump_json(),
    )


def status_event(state: str) -> str:
    """Format an Atuin 'status' SSE event."""
    return format_sse("status", AtuinStatusEvent(state=state).model_dump_json())


def done_event(session_id: str) -> str:
    """Format an Atuin 'done' SSE event."""
    return format_sse("done", AtuinDoneEvent(session_id=session_id).model_dump_json())


def error_event(message: str) -> str:
    """Format an Atuin 'error' SSE event."""
    return format_sse("error", AtuinErrorEvent(message=message).model_dump_json())
```

**Key changes from V1:**
- `AtuinConfig.skills` is now `list[AtuinSkillSummary]` (was `list[Any]`)
- New model: `AtuinSkillSummary` with `name` and `description` fields
- New models: `AtuinToolCallEvent`, `AtuinToolResultEvent`, `AtuinStatusEvent`
- New builders: `tool_call_event()`, `tool_result_event()`, `status_event()`
- `ConfigDict(extra="ignore")` removed from SSE event models (they're output-only, not parsed from external input)

---

### Step 1.2: Create `backend.py`

This replaces `vllm_client.py`. For Phase 1, it yields `BackendEvent` types but only handles text (no tool accumulation yet).

**Create `src/atuin_ai_adapter/backend.py`:**

```python
from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ─── Backend event types ───


@dataclass(frozen=True, slots=True)
class BackendTextDelta:
    content: str


@dataclass(frozen=True, slots=True)
class BackendToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BackendDone:
    pass


@dataclass(frozen=True, slots=True)
class BackendError:
    message: str


BackendEvent = BackendTextDelta | BackendToolCall | BackendDone | BackendError


# ─── Exceptions ───


class BackendConnectionError(Exception):
    """Raised when the upstream model server is unreachable or returns an HTTP error."""


# ─── Backend client ───


class BackendClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> AsyncIterator[BackendEvent]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if top_p is not None:
            body["top_p"] = top_p
        if tools:
            body["tools"] = tools

        try:
            async with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    snippet = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    yield BackendError(message=f"Model server returned {response.status_code}: {snippet}")
                    yield BackendDone()
                    return

                async for event in self._parse_stream(response):
                    yield event

        except httpx.HTTPError as exc:
            raise BackendConnectionError(
                f"Cannot reach model server at {self._base_url}"
            ) from exc

    async def _parse_stream(self, response: httpx.Response) -> AsyncIterator[BackendEvent]:
        """Parse an OpenAI-compatible SSE stream, yielding BackendEvents."""
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                yield BackendDone()
                return
            if not line.startswith("data: "):
                continue

            payload = line.removeprefix("data: ")
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                yield BackendError(message="Failed to parse upstream response")
                yield BackendDone()
                return

            choices = parsed.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})

            # Text content
            content = delta.get("content")
            if content:
                yield content_delta
                yield BackendTextDelta(content=content)

            # Tool call deltas will be handled in Phase 2

        # If we reach here without [DONE], still emit Done
        yield BackendDone()

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/v1/models")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
```

**Wait — there's a bug in the code above.** The line `yield content_delta` is erroneous. The correct `_parse_stream` method should be:

```python
    async def _parse_stream(self, response: httpx.Response) -> AsyncIterator[BackendEvent]:
        """Parse an OpenAI-compatible SSE stream, yielding BackendEvents."""
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                yield BackendDone()
                return
            if not line.startswith("data: "):
                continue

            payload = line.removeprefix("data: ")
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                yield BackendError(message="Failed to parse upstream response")
                yield BackendDone()
                return

            choices = parsed.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})

            # Text content
            content = delta.get("content")
            if content:
                yield BackendTextDelta(content=content)

            # Tool call deltas will be handled in Phase 2

        # If we reach here without [DONE], still emit Done
        yield BackendDone()
```

**Key differences from V1's `VllmClient`:**
- Returns `BackendEvent` types instead of raw `str | None`
- Accepts plain dicts for messages (not `OpenAIChatRequest` pydantic model)
- Accepts optional `tools` parameter (not used in Phase 1)
- Accepts optional `api_key` for remote APIs
- `BackendConnectionError` replaces `VllmError`
- HTTP errors yield `BackendError` events instead of raising exceptions
- JSON parse errors yield `BackendError` events instead of raising exceptions

---

### Step 1.3: Create `orchestrator.py`

This replaces `service.py`. For Phase 1, it delegates to the same translator logic and consumes `BackendEvent` types.

**Create `src/atuin_ai_adapter/orchestrator.py`:**

```python
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

from atuin_ai_adapter.backend import BackendClient, BackendConnectionError, BackendDone, BackendError, BackendTextDelta
from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.protocol import AtuinChatRequest, done_event, error_event, text_event
from atuin_ai_adapter.translator import build_openai_messages

logger = logging.getLogger(__name__)


async def handle_chat(
    request: AtuinChatRequest,
    backend: BackendClient,
    settings: Settings,
) -> AsyncIterator[str]:
    session_id = request.session_id or str(uuid.uuid4())

    try:
        # Build messages (Phase 1: same as V1 — flatten all tool blocks to text)
        openai_messages = build_openai_messages(request, settings.system_prompt_template)

        # Stream from backend
        async for event in backend.stream_chat(
            messages=[{"role": m.role, "content": m.content} for m in openai_messages],
            model=settings.vllm_model,
            temperature=settings.generation_temperature,
            max_tokens=settings.generation_max_tokens,
            top_p=settings.generation_top_p,
        ):
            match event:
                case BackendTextDelta(content=content):
                    yield text_event(content)

                case BackendDone():
                    pass  # handled below

                case BackendError(message=msg):
                    yield error_event(msg)
                    yield done_event(session_id)
                    return

        yield done_event(session_id)

    except BackendConnectionError as exc:
        logger.error("Backend connection error: %s", exc)
        yield error_event(str(exc))
        yield done_event(session_id)
    except Exception as exc:
        logger.error("Adapter error: %s", exc, exc_info=True)
        yield error_event("Internal adapter error")
        yield done_event(session_id)
```

**Note:** In Phase 1, the orchestrator still calls `build_openai_messages()` which returns `list[OpenAIChatMessage]`. We convert those to dicts for the new `BackendClient` interface. This will be cleaned up in Phase 2 when the translator is refactored.

---

### Step 1.4: Update `config.py`

Add the new config fields. Existing fields remain unchanged.

**Edit `src/atuin_ai_adapter/config.py` — add these fields to `Settings`:**

```python
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYSTEM_PROMPT_TEMPLATE = """You are a terminal assistant. The user is working in a shell and may ask you
to suggest commands, explain errors, or help with system administration tasks.

Be concise. Prefer direct answers over lengthy explanations.
When suggesting a command, output it directly without markdown code fences
unless you are comparing multiple options.
If you are unsure, say so rather than guessing."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Adapter server
    adapter_host: str = "127.0.0.1"
    adapter_port: int = 8787
    adapter_api_token: str = "local-dev-token"

    # Backend
    vllm_base_url: str = "http://127.0.0.1:8000"
    vllm_model: str
    vllm_timeout: float = 120.0
    vllm_api_key: str | None = None

    # Generation
    generation_temperature: float = 0.7
    generation_max_tokens: int = 2048
    generation_top_p: float = 0.95

    # Tools
    enable_tools: bool = True

    # Prompt
    system_prompt_template: str = DEFAULT_SYSTEM_PROMPT_TEMPLATE

    # Logging
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

**New fields:**
- `vllm_api_key: str | None = None` — API key for remote OpenAI-compatible backends
- `enable_tools: bool = True` — set to `False` for V1-compatible text-only behavior

---

### Step 1.5: Update `app.py`

Update imports to use the new modules. Replace `VllmClient` with `BackendClient`, `service.handle_chat` with `orchestrator.handle_chat`.

**Rewrite `src/atuin_ai_adapter/app.py`:**

```python
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from atuin_ai_adapter.backend import BackendClient
from atuin_ai_adapter.config import Settings, get_settings
from atuin_ai_adapter.orchestrator import handle_chat
from atuin_ai_adapter.protocol import AtuinChatRequest


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    backend = BackendClient(
        base_url=settings.vllm_base_url,
        timeout=settings.vllm_timeout,
        api_key=settings.vllm_api_key,
    )
    app.state.settings = settings
    app.state.backend = backend
    try:
        yield
    finally:
        await backend.close()


app = FastAPI(title="Atuin AI Adapter", lifespan=lifespan)


async def verify_token(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    expected = f"Bearer {request.app.state.settings.adapter_api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


@app.post("/api/cli/chat")
async def chat(
    chat_request: AtuinChatRequest,
    request: Request,
    _: None = Depends(verify_token),
) -> StreamingResponse:
    settings: Settings = request.app.state.settings
    backend: BackendClient = request.app.state.backend
    logging.getLogger(__name__).info("request invocation_id=%s", chat_request.invocation_id)
    return StreamingResponse(
        handle_chat(chat_request, backend, settings),
        media_type="text/event-stream",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request) -> object:
    backend: BackendClient = request.app.state.backend
    if await backend.health_check():
        return {"status": "ready", "upstream": "reachable"}
    return JSONResponse(
        {"status": "not_ready", "upstream": "unreachable"},
        status_code=503,
    )


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "atuin_ai_adapter.app:app",
        host=settings.adapter_host,
        port=settings.adapter_port,
        log_level=settings.log_level.lower(),
    )
```

---

### Step 1.6: Update `translator.py` imports

The translator currently imports from `atuin_ai_adapter.protocol.atuin` and `atuin_ai_adapter.protocol.openai`. Update to import from the new `protocol.py`.

**Edit `src/atuin_ai_adapter/translator.py`:**

Change the imports at the top:
```python
# OLD:
from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.protocol.openai import OpenAIChatMessage

# NEW:
from atuin_ai_adapter.protocol import AtuinChatRequest
```

Also, `OpenAIChatMessage` is no longer needed as a separate model. For Phase 1 compatibility, keep using it internally but define it locally in translator.py or just use dicts. The simplest approach for Phase 1: keep the `OpenAIChatMessage` as a simple dataclass in the translator:

```python
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from atuin_ai_adapter.protocol import AtuinChatRequest

logger = logging.getLogger(__name__)


@dataclass
class OpenAIChatMessage:
    role: str
    content: str


def flatten_content_blocks(content: str | list[dict[str, Any]] | Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        rendered: list[str] = []
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                rendered.append(str(block.get("text", "")))
            elif block_type == "tool_use":
                name = block.get("name", "unknown_tool")
                tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
                rendered.append(f"[Tool call: {name}({tool_input})]")
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id", "unknown")
                tool_content = str(block.get("content", ""))
                if block.get("is_error"):
                    rendered.append(f"[Tool error ({tool_use_id}): {tool_content}]")
                else:
                    rendered.append(f"[Tool result ({tool_use_id}): {tool_content}]")
            else:
                dumped = json.dumps(block, ensure_ascii=False)
                logger.warning("Unknown content block type: %s", block_type)
                rendered.append(f"[Unknown block: {dumped}]")
        return "\n\n".join(rendered)

    logger.warning("Unexpected content type: %s", type(content).__name__)
    return str(content)


def build_openai_messages(request: AtuinChatRequest, system_prompt_template: str) -> list[OpenAIChatMessage]:
    body_lines: list[str] = []

    if request.context is not None:
        env_lines: list[str] = []
        field_map = [
            ("OS", request.context.os),
            ("Shell", request.context.shell),
            ("Distribution", request.context.distro),
            ("Working directory", request.context.pwd),
            ("Last command", request.context.last_command),
        ]
        for label, value in field_map:
            if value:
                env_lines.append(f"- {label}: {value}")
        if env_lines:
            body_lines.append("Environment:")
            body_lines.extend(env_lines)

    if request.config is not None and request.config.user_contexts:
        if body_lines:
            body_lines.append("")
        body_lines.append("User context:")
        body_lines.extend(request.config.user_contexts)

    system_content = system_prompt_template
    if body_lines:
        system_content = f"{system_prompt_template}\n\n" + "\n".join(body_lines)

    translated: list[OpenAIChatMessage] = [OpenAIChatMessage(role="system", content=system_content)]
    for message in request.messages:
        role = str(message.get("role", "user"))
        content = flatten_content_blocks(message.get("content", ""))
        translated.append(OpenAIChatMessage(role=role, content=content))

    return translated
```

---

### Step 1.7: Delete old modules

Remove the files that have been replaced:

```bash
rm src/atuin_ai_adapter/service.py
rm src/atuin_ai_adapter/sse.py
rm src/atuin_ai_adapter/vllm_client.py
rm src/atuin_ai_adapter/protocol/atuin.py
rm src/atuin_ai_adapter/protocol/openai.py
rm src/atuin_ai_adapter/protocol/__init__.py
rmdir src/atuin_ai_adapter/protocol/
```

---

### Step 1.8: Update all tests

Every test file needs its imports updated. Here's a mapping of what changed:

| Old import | New import |
|---|---|
| `from atuin_ai_adapter.protocol.atuin import AtuinChatRequest, AtuinContext, AtuinConfig, AtuinTextEvent, AtuinDoneEvent, AtuinErrorEvent` | `from atuin_ai_adapter.protocol import AtuinChatRequest, AtuinContext, AtuinConfig, AtuinTextEvent, AtuinDoneEvent, AtuinErrorEvent` |
| `from atuin_ai_adapter.protocol.openai import OpenAIChatMessage, OpenAIChatRequest` | `from atuin_ai_adapter.translator import OpenAIChatMessage` (for message); `OpenAIChatRequest` is removed |
| `from atuin_ai_adapter.sse import format_sse, text_event, done_event, error_event` | `from atuin_ai_adapter.protocol import format_sse, text_event, done_event, error_event` |
| `from atuin_ai_adapter.service import handle_chat` | `from atuin_ai_adapter.orchestrator import handle_chat` |
| `from atuin_ai_adapter.vllm_client import VllmClient, VllmError` | `from atuin_ai_adapter.backend import BackendClient, BackendConnectionError` |

**Key test changes required:**

#### `test_service.py` → rename and refactor

This file tests `handle_chat()`. It currently uses a `FakeVllmClient`. Update it to work with the new `BackendClient` interface.

The current fake client yields `str | None`. The new `BackendClient.stream_chat()` yields `BackendEvent`. Create a helper that makes a mock backend:

```python
"""Tests for orchestrator.handle_chat()."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from atuin_ai_adapter.backend import BackendClient, BackendConnectionError, BackendDone, BackendError, BackendTextDelta
from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.protocol import AtuinChatRequest


def make_settings(**overrides: Any) -> Settings:
    defaults = {"vllm_model": "test-model", "adapter_api_token": "tok"}
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[call-arg]


async def fake_stream(*events):
    """Create a mock stream_chat that yields the given events then BackendDone."""
    async def stream_chat(**kwargs) -> AsyncIterator:
        for event in events:
            yield event
        yield BackendDone()
    return stream_chat


class TestHandleChat:
    async def test_text_only_stream(self):
        from atuin_ai_adapter.orchestrator import handle_chat

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            invocation_id="test-inv-1",
        )
        backend = AsyncMock(spec=BackendClient)

        async def mock_stream(**kwargs):
            yield BackendTextDelta(content="Hello ")
            yield BackendTextDelta(content="world")
            yield BackendDone()

        backend.stream_chat = mock_stream
        settings = make_settings()

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        # Should have text events + done event
        text_frames = [f for f in frames if "event: text" in f]
        done_frames = [f for f in frames if "event: done" in f]
        assert len(text_frames) == 2
        assert len(done_frames) == 1
        assert '"content":"Hello "' in text_frames[0]
        assert '"content":"world"' in text_frames[1]

    async def test_backend_error_mid_stream(self):
        from atuin_ai_adapter.orchestrator import handle_chat

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            invocation_id="test-inv-2",
        )
        backend = AsyncMock(spec=BackendClient)

        async def mock_stream(**kwargs):
            yield BackendTextDelta(content="partial")
            yield BackendError(message="upstream failed")

        backend.stream_chat = mock_stream
        settings = make_settings()

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        text_frames = [f for f in frames if "event: text" in f]
        error_frames = [f for f in frames if "event: error" in f]
        done_frames = [f for f in frames if "event: done" in f]
        assert len(text_frames) == 1
        assert len(error_frames) == 1
        assert len(done_frames) == 1

    async def test_connection_error(self):
        from atuin_ai_adapter.orchestrator import handle_chat

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            invocation_id="test-inv-3",
        )
        backend = AsyncMock(spec=BackendClient)

        async def mock_stream(**kwargs):
            raise BackendConnectionError("Cannot reach server")
            yield  # make it a generator  # noqa: RET503

        backend.stream_chat = mock_stream
        settings = make_settings()

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        error_frames = [f for f in frames if "event: error" in f]
        done_frames = [f for f in frames if "event: done" in f]
        assert len(error_frames) == 1
        assert len(done_frames) == 1

    async def test_session_id_echoed(self):
        from atuin_ai_adapter.orchestrator import handle_chat

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            invocation_id="test-inv-4",
            session_id="my-session-123",
        )
        backend = AsyncMock(spec=BackendClient)

        async def mock_stream(**kwargs):
            yield BackendTextDelta(content="hi")
            yield BackendDone()

        backend.stream_chat = mock_stream
        settings = make_settings()

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        done_frames = [f for f in frames if "event: done" in f]
        assert '"session_id":"my-session-123"' in done_frames[0]

    async def test_session_id_generated_when_absent(self):
        from atuin_ai_adapter.orchestrator import handle_chat

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            invocation_id="test-inv-5",
        )
        backend = AsyncMock(spec=BackendClient)

        async def mock_stream(**kwargs):
            yield BackendDone()

        backend.stream_chat = mock_stream
        settings = make_settings()

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        done_frames = [f for f in frames if "event: done" in f]
        assert len(done_frames) == 1
        assert "session_id" in done_frames[0]
```

#### `test_vllm_client.py` → rename to `test_backend.py`

Update to test `BackendClient` with `BackendEvent` types. The test structure stays similar but assertions change from checking yielded strings to checking `BackendEvent` instances.

```python
"""Tests for backend.BackendClient."""
from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from atuin_ai_adapter.backend import (
    BackendClient,
    BackendConnectionError,
    BackendDone,
    BackendError,
    BackendTextDelta,
)


@pytest.fixture
def backend(httpx_mock: HTTPXMock) -> BackendClient:
    return BackendClient(base_url="http://test-backend", timeout=10.0)


def make_sse_body(chunks: list[str]) -> str:
    lines = []
    for chunk in chunks:
        lines.append(f"data: {chunk}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


class TestStreamChat:
    async def test_text_stream(self, backend: BackendClient, httpx_mock: HTTPXMock):
        body = make_sse_body([
            '{"choices":[{"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
            '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
            '{"choices":[{"delta":{"content":" world"},"finish_reason":null}]}',
            '{"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}',
        ])
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        ):
            events.append(event)

        text_events = [e for e in events if isinstance(e, BackendTextDelta)]
        done_events = [e for e in events if isinstance(e, BackendDone)]
        assert len(text_events) == 2
        assert text_events[0].content == "hello"
        assert text_events[1].content == " world"
        assert len(done_events) == 1

    async def test_upstream_error_status(self, backend: BackendClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            status_code=500,
            content=b"Internal Server Error",
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        ):
            events.append(event)

        error_events = [e for e in events if isinstance(e, BackendError)]
        assert len(error_events) == 1
        assert "500" in error_events[0].message

    async def test_malformed_json(self, backend: BackendClient, httpx_mock: HTTPXMock):
        body = "data: {invalid json}\n\ndata: [DONE]\n\n"
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        ):
            events.append(event)

        error_events = [e for e in events if isinstance(e, BackendError)]
        assert len(error_events) == 1
        assert "parse" in error_events[0].message.lower()

    async def test_role_only_chunk_skipped(self, backend: BackendClient, httpx_mock: HTTPXMock):
        body = make_sse_body([
            '{"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}',
            '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
        ])
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        ):
            events.append(event)

        text_events = [e for e in events if isinstance(e, BackendTextDelta)]
        assert len(text_events) == 1
        assert text_events[0].content == "hello"


class TestHealthCheck:
    async def test_healthy(self, backend: BackendClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url="http://test-backend/v1/models", status_code=200)
        assert await backend.health_check() is True

    async def test_unhealthy(self, backend: BackendClient, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url="http://test-backend/v1/models", status_code=503)
        assert await backend.health_check() is False
```

#### `test_protocol_atuin.py` → rename to `test_protocol.py`

Update imports from `atuin_ai_adapter.protocol.atuin` to `atuin_ai_adapter.protocol`. Add tests for new models (`AtuinToolCallEvent`, `AtuinStatusEvent`, etc.) and new SSE builders.

#### `test_protocol_openai.py` → delete

The `OpenAIChatRequest` model is gone. `OpenAIChatMessage` is now a simple dataclass in `translator.py`. Any relevant tests can move to `test_translator.py`.

#### `test_sse.py` → merge into `test_protocol.py`

SSE builders are now in `protocol.py`. Move the SSE tests into `test_protocol.py` and update imports.

#### Other test files

- `test_app.py` — update import for `VllmClient` → `BackendClient`. The `app.state.vllm_client` is now `app.state.backend`.
- `test_translator.py` — update imports only
- `test_config.py` — add tests for new fields (`enable_tools`, `vllm_api_key`)
- `test_atuin_cli_e2e.py` — update imports only
- `test_real_world_remora.py` — update imports only
- `tests/conftest.py` — update imports only

---

### Step 1.9: Validation

Run the full test suite:

```bash
uv run pytest -x -q
```

**All existing tests must pass.** The behavior is unchanged — only the module layout and internal types have changed.

Also run:
```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

**Acceptance criteria for Phase 1:**
- [ ] All V1 tests pass (with updated imports)
- [ ] No import errors anywhere
- [ ] `ruff check` clean
- [ ] `ruff format` clean
- [ ] Old modules deleted (`service.py`, `sse.py`, `vllm_client.py`, `protocol/` directory)
- [ ] New modules exist (`protocol.py`, `backend.py`, `orchestrator.py`)
- [ ] The `with_tools.json` fixture still works (tool blocks are flattened to text, same as V1)
- [ ] `app.py` creates `BackendClient` at startup

---

## Phase 2: Tool Infrastructure

**Goal:** Tool schemas sent to backend, tool-call deltas accumulated, `tool_call` SSE events emitted, proper Atuin ↔ OpenAI message translation for tool blocks.

---

### Step 2.1: Create `tools.py`

The tool registry maps Atuin capabilities to tool definitions and converts them to OpenAI format.

**Create `src/atuin_ai_adapter/tools.py`:**

```python
from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel


class ToolExecution(str, Enum):
    CLIENT = "client"     # Atuin executes locally
    PSEUDO = "pseudo"     # UI signal, no execution (e.g., suggest_command)
    ADAPTER = "adapter"   # Adapter executes (future remote tools)


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    execution: ToolExecution


# ─── Tool schemas (matching Atuin's tools/mod.rs) ───

_SUGGEST_COMMAND = ToolDefinition(
    name="suggest_command",
    description="Suggest a shell command for the user to run or edit. Use this when the best answer is a command.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": ["string", "null"],
                "description": "The shell command to suggest",
            },
            "description": {
                "type": ["string", "null"],
                "description": "Brief description of what the command does",
            },
            "confidence": {
                "type": ["string", "null"],
                "enum": ["low", "medium", "high", None],
            },
            "danger": {
                "type": ["string", "null"],
                "enum": ["low", "medium", "high", None],
            },
            "warning": {
                "type": ["string", "null"],
                "description": "Warning message for dangerous commands",
            },
        },
        "required": ["command"],
    },
    execution=ToolExecution.PSEUDO,
)

_READ_FILE = ToolDefinition(
    name="read_file",
    description="Read the contents of a file.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000},
        },
        "required": ["file_path"],
    },
    execution=ToolExecution.CLIENT,
)

_EDIT_FILE = ToolDefinition(
    name="edit_file",
    description="Edit a file by replacing a specific string with a new string.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    execution=ToolExecution.CLIENT,
)

_WRITE_FILE = ToolDefinition(
    name="write_file",
    description="Write content to a file. Creates the file if it doesn't exist.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "content"],
    },
    execution=ToolExecution.CLIENT,
)

_EXECUTE_SHELL_COMMAND = ToolDefinition(
    name="execute_shell_command",
    description="Execute a shell command and return the output.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "shell": {"type": "string", "default": "bash"},
            "dir": {"type": ["string", "null"]},
            "timeout": {"type": "integer", "default": 30, "minimum": 1, "maximum": 600},
            "description": {"type": ["string", "null"]},
        },
        "required": ["command"],
    },
    execution=ToolExecution.CLIENT,
)

_ATUIN_HISTORY = ToolDefinition(
    name="atuin_history",
    description="Search the user's shell command history.",
    parameters={
        "type": "object",
        "properties": {
            "filter_modes": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["global", "host", "session", "directory", "workspace"],
                },
            },
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
        },
        "required": ["filter_modes", "query"],
    },
    execution=ToolExecution.CLIENT,
)

_LOAD_SKILL = ToolDefinition(
    name="load_skill",
    description="Load the full content of a skill by name.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
    },
    execution=ToolExecution.CLIENT,
)

# ─── Capability → tool mapping ───

_TOOL_BY_NAME: dict[str, ToolDefinition] = {
    "suggest_command": _SUGGEST_COMMAND,
    "read_file": _READ_FILE,
    "edit_file": _EDIT_FILE,
    "write_file": _WRITE_FILE,
    "execute_shell_command": _EXECUTE_SHELL_COMMAND,
    "atuin_history": _ATUIN_HISTORY,
    "load_skill": _LOAD_SKILL,
}

CAPABILITY_TOOL_MAP: dict[str, list[str]] = {
    "client_invocations": ["suggest_command"],
    "client_v1_load_skill": ["load_skill"],
    "client_v1_atuin_history": ["atuin_history"],
    "client_v1_read_file": ["read_file"],
    "client_v1_edit_file": ["edit_file"],
    "client_v1_write_file": ["write_file"],
    "client_v1_execute_shell_command": ["execute_shell_command"],
}


def build_tool_registry(capabilities: list[str]) -> list[ToolDefinition]:
    """Return tool definitions for the given capability list.

    Unknown capabilities are silently ignored (forward compatibility).
    """
    seen: set[str] = set()
    tools: list[ToolDefinition] = []
    for cap in capabilities:
        for tool_name in CAPABILITY_TOOL_MAP.get(cap, []):
            if tool_name not in seen:
                seen.add(tool_name)
                tools.append(_TOOL_BY_NAME[tool_name])
    return tools


def to_openai_tools(registry: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert tool definitions to OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in registry
    ]
```

#### Test: `tests/test_tools.py`

```python
"""Tests for tools.py — tool registry and capability mapping."""
from __future__ import annotations

import json

import pytest

from atuin_ai_adapter.tools import (
    CAPABILITY_TOOL_MAP,
    ToolDefinition,
    ToolExecution,
    build_tool_registry,
    to_openai_tools,
)


class TestBuildToolRegistry:
    def test_all_capabilities_returns_all_tools(self):
        all_caps = list(CAPABILITY_TOOL_MAP.keys())
        registry = build_tool_registry(all_caps)
        names = {t.name for t in registry}
        assert names == {
            "suggest_command", "load_skill", "atuin_history",
            "read_file", "edit_file", "write_file", "execute_shell_command",
        }

    def test_empty_capabilities_returns_empty(self):
        assert build_tool_registry([]) == []

    def test_single_capability(self):
        registry = build_tool_registry(["client_invocations"])
        assert len(registry) == 1
        assert registry[0].name == "suggest_command"
        assert registry[0].execution == ToolExecution.PSEUDO

    def test_partial_capabilities(self):
        registry = build_tool_registry(["client_invocations", "client_v1_read_file"])
        names = {t.name for t in registry}
        assert names == {"suggest_command", "read_file"}

    def test_unknown_capability_ignored(self):
        registry = build_tool_registry(["client_invocations", "future_capability_v99"])
        assert len(registry) == 1
        assert registry[0].name == "suggest_command"

    def test_duplicate_capabilities_no_duplicate_tools(self):
        registry = build_tool_registry(["client_invocations", "client_invocations"])
        assert len(registry) == 1

    def test_tool_execution_types(self):
        registry = build_tool_registry(list(CAPABILITY_TOOL_MAP.keys()))
        by_name = {t.name: t for t in registry}
        assert by_name["suggest_command"].execution == ToolExecution.PSEUDO
        assert by_name["read_file"].execution == ToolExecution.CLIENT
        assert by_name["execute_shell_command"].execution == ToolExecution.CLIENT


class TestToOpenAITools:
    def test_converts_to_openai_format(self):
        registry = build_tool_registry(["client_invocations"])
        openai_tools = to_openai_tools(registry)
        assert len(openai_tools) == 1
        tool = openai_tools[0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "suggest_command"
        assert "parameters" in tool["function"]
        assert "description" in tool["function"]

    def test_empty_registry(self):
        assert to_openai_tools([]) == []

    def test_all_tools_have_valid_schemas(self):
        registry = build_tool_registry(list(CAPABILITY_TOOL_MAP.keys()))
        openai_tools = to_openai_tools(registry)
        for tool in openai_tools:
            assert tool["type"] == "function"
            func = tool["function"]
            assert isinstance(func["name"], str)
            assert isinstance(func["description"], str)
            params = func["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params
            # Verify JSON-serializable
            json.dumps(tool)

    def test_suggest_command_schema(self):
        registry = build_tool_registry(["client_invocations"])
        openai_tools = to_openai_tools(registry)
        params = openai_tools[0]["function"]["parameters"]
        assert "command" in params["properties"]
        assert params["required"] == ["command"]

    def test_execute_shell_command_schema(self):
        registry = build_tool_registry(["client_v1_execute_shell_command"])
        openai_tools = to_openai_tools(registry)
        params = openai_tools[0]["function"]["parameters"]
        assert "command" in params["properties"]
        assert "shell" in params["properties"]
        assert "timeout" in params["properties"]
        assert params["required"] == ["command"]
```

Run and validate:
```bash
uv run pytest tests/test_tools.py -v
```

---

### Step 2.2: Add tool-call accumulation to `backend.py`

This is the trickiest part of the refactor. The backend driver must accumulate fragmented tool-call deltas from the OpenAI stream and yield complete `BackendToolCall` events.

**Add to `backend.py` — the accumulator dataclass and updated `_parse_stream`:**

```python
@dataclass
class _ToolCallAccumulator:
    """Accumulates fragmented tool-call deltas from OpenAI streaming."""
    id: str = ""
    name: str = ""
    arguments: str = ""
```

**Replace the `_parse_stream` method in `BackendClient`:**

```python
    async def _parse_stream(self, response: httpx.Response) -> AsyncIterator[BackendEvent]:
        """Parse an OpenAI-compatible SSE stream, yielding BackendEvents."""
        accumulators: dict[int, _ToolCallAccumulator] = {}

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                break
            if not line.startswith("data: "):
                continue

            payload = line.removeprefix("data: ")
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                yield BackendError(message="Failed to parse upstream response")
                yield BackendDone()
                return

            choices = parsed.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta", {})

            # Text content — yield immediately
            content = delta.get("content")
            if content:
                yield BackendTextDelta(content=content)

            # Tool call deltas — accumulate
            tool_calls = delta.get("tool_calls")
            if tool_calls:
                for tc_delta in tool_calls:
                    index = tc_delta.get("index", 0)
                    if index not in accumulators:
                        accumulators[index] = _ToolCallAccumulator()

                    acc = accumulators[index]
                    if "id" in tc_delta:
                        acc.id = tc_delta["id"]
                    func = tc_delta.get("function", {})
                    if "name" in func:
                        acc.name = func["name"]
                    if "arguments" in func:
                        acc.arguments += func["arguments"]

        # Stream ended — emit accumulated tool calls
        for index in sorted(accumulators):
            acc = accumulators[index]
            try:
                arguments = json.loads(acc.arguments) if acc.arguments else {}
            except json.JSONDecodeError:
                yield BackendError(
                    message=f"Malformed tool call arguments for {acc.name}"
                )
                yield BackendDone()
                return
            yield BackendToolCall(
                id=acc.id or f"call_{index}",
                name=acc.name,
                arguments=arguments,
            )

        yield BackendDone()
```

**Key behaviors:**
- Text deltas are yielded immediately as `BackendTextDelta`
- Tool call deltas are accumulated by index
- When the stream ends (either `[DONE]` or natural end), all accumulated tool calls are emitted as `BackendToolCall` events
- If tool-call argument JSON is malformed, a `BackendError` is emitted
- A `BackendDone` is always the last event

#### New test fixtures

**Create `tests/fixtures/streams/with_tool_call.txt`:**
```
data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Let me suggest a command."},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_abc123","type":"function","function":{"name":"suggest_command","arguments":""}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"command\":"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":" \"ls -la\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

**Create `tests/fixtures/streams/with_multiple_tool_calls.txt`:**
```
data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Let me read both files."},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_001","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"file_path\": \"foo.rs\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"call_002","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\"file_path\": \"bar.rs\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

**Create `tests/fixtures/streams/malformed_tool_args.txt`:**
```
data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_bad","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{invalid json!!!"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: [DONE]
```

#### Tool accumulation tests in `test_backend.py`

Add these test cases to `test_backend.py`:

```python
class TestToolCallAccumulation:
    async def test_single_tool_call(self, backend: BackendClient, httpx_mock: HTTPXMock):
        from tests.conftest import load_stream

        body = load_stream("with_tool_call")
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "list files"}],
            model="test-model",
        ):
            events.append(event)

        text_events = [e for e in events if isinstance(e, BackendTextDelta)]
        tool_events = [e for e in events if isinstance(e, BackendToolCall)]
        done_events = [e for e in events if isinstance(e, BackendDone)]

        assert len(text_events) == 1
        assert text_events[0].content == "Let me suggest a command."

        assert len(tool_events) == 1
        assert tool_events[0].id == "call_abc123"
        assert tool_events[0].name == "suggest_command"
        assert tool_events[0].arguments == {"command": "ls -la"}

        assert len(done_events) == 1

    async def test_multiple_tool_calls(self, backend: BackendClient, httpx_mock: HTTPXMock):
        from tests.conftest import load_stream

        body = load_stream("with_multiple_tool_calls")
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "read files"}],
            model="test-model",
        ):
            events.append(event)

        tool_events = [e for e in events if isinstance(e, BackendToolCall)]
        assert len(tool_events) == 2
        assert tool_events[0].id == "call_001"
        assert tool_events[0].name == "read_file"
        assert tool_events[0].arguments == {"file_path": "foo.rs"}
        assert tool_events[1].id == "call_002"
        assert tool_events[1].name == "read_file"
        assert tool_events[1].arguments == {"file_path": "bar.rs"}

    async def test_malformed_tool_arguments(self, backend: BackendClient, httpx_mock: HTTPXMock):
        from tests.conftest import load_stream

        body = load_stream("malformed_tool_args")
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "read file"}],
            model="test-model",
        ):
            events.append(event)

        error_events = [e for e in events if isinstance(e, BackendError)]
        assert len(error_events) == 1
        assert "malformed" in error_events[0].message.lower()

    async def test_text_only_no_tools(self, backend: BackendClient, httpx_mock: HTTPXMock):
        """When no tool calls appear, just text + done."""
        body = make_sse_body([
            '{"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}',
            '{"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}',
        ])
        httpx_mock.add_response(
            url="http://test-backend/v1/chat/completions",
            content=body.encode(),
            headers={"content-type": "text/event-stream"},
        )

        events = []
        async for event in backend.stream_chat(
            messages=[{"role": "user", "content": "hi"}],
            model="test-model",
        ):
            events.append(event)

        tool_events = [e for e in events if isinstance(e, BackendToolCall)]
        assert len(tool_events) == 0
```

Run and validate:
```bash
uv run pytest tests/test_backend.py -v
```

---

### Step 2.3: Extend `translator.py` — proper tool block translation

The translator needs a new function `translate_messages()` that converts Atuin's Anthropic-style tool blocks to OpenAI format, instead of flattening them to text.

**Add to `translator.py`:**

```python
def translate_messages(
    messages: list[dict[str, Any]],
    *,
    flatten_tools: bool = False,
) -> list[dict[str, Any]]:
    """Translate Atuin-format messages to OpenAI-format messages.

    When flatten_tools is True, tool blocks are converted to text (V1 behavior).
    When False, tool_use becomes tool_calls and tool_result becomes role=tool messages.
    """
    if flatten_tools:
        return _translate_flattened(messages)
    return _translate_structured(messages)


def _translate_flattened(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """V1 behavior: flatten all structured content to text."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = flatten_content_blocks(msg.get("content", ""))
        result.append({"role": role, "content": content})
    return result


def _translate_structured(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """V2 behavior: translate tool blocks to OpenAI format."""
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")

        # Simple string content — pass through
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        # List of content blocks — need to translate
        if not isinstance(content, list):
            logger.warning("Unexpected content type: %s", type(content).__name__)
            result.append({"role": role, "content": str(content)})
            continue

        if role == "assistant":
            result.extend(_translate_assistant_blocks(content))
        elif role == "user":
            result.extend(_translate_user_blocks(content))
        else:
            # Unknown role with structured content — flatten
            result.append({"role": role, "content": flatten_content_blocks(content)})

    return result


def _translate_assistant_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate assistant content blocks with tool_use to OpenAI format.

    Atuin: {"role": "assistant", "content": [{"type": "text", ...}, {"type": "tool_use", ...}]}
    OpenAI: {"role": "assistant", "content": "...", "tool_calls": [...]}
    """
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                },
            })
        else:
            dumped = json.dumps(block, ensure_ascii=False)
            logger.warning("Unknown assistant content block type: %s", block_type)
            text_parts.append(f"[Unknown block (type={block_type}): {dumped}]")

    msg: dict[str, Any] = {"role": "assistant"}
    text_content = "\n\n".join(text_parts) if text_parts else None
    msg["content"] = text_content
    if tool_calls:
        msg["tool_calls"] = tool_calls

    return [msg]


def _translate_user_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate user content blocks with tool_result to OpenAI format.

    Atuin: {"role": "user", "content": [{"type": "tool_result", ...}, {"type": "text", ...}]}
    OpenAI: [{"role": "tool", "tool_call_id": "...", "content": "..."}, {"role": "user", "content": "..."}]

    One Atuin message with N tool_result blocks becomes N OpenAI tool messages.
    Text blocks become a separate user message.
    """
    result: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for block in blocks:
        block_type = block.get("type")
        if block_type == "tool_result":
            result.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": str(block.get("content", "")),
            })
        elif block_type == "text":
            text_parts.append(str(block.get("text", "")))
        else:
            dumped = json.dumps(block, ensure_ascii=False)
            logger.warning("Unknown user content block type: %s", block_type)
            text_parts.append(f"[Unknown block (type={block_type}): {dumped}]")

    if text_parts:
        result.append({"role": "user", "content": "\n\n".join(text_parts)})

    return result
```

#### Test: add to `tests/test_translator.py`

```python
class TestTranslateMessages:
    def test_simple_text_passthrough(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert result == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    def test_assistant_tool_use_translated(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me read that."},
                    {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "foo.rs"}},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert len(result) == 1
        msg = result[0]
        assert msg["role"] == "assistant"
        assert msg["content"] == "Let me read that."
        assert len(msg["tool_calls"]) == 1
        tc = msg["tool_calls"][0]
        assert tc["id"] == "tc_001"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"file_path": "foo.rs"}

    def test_assistant_tool_use_no_text(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "foo.rs"}},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert result[0]["content"] is None
        assert len(result[0]["tool_calls"]) == 1

    def test_user_tool_result_translated(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_001", "content": "file contents...", "is_error": False},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert len(result) == 1
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc_001"
        assert result[0]["content"] == "file contents..."

    def test_multiple_tool_results_become_multiple_messages(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_001", "content": "result 1"},
                    {"type": "tool_result", "tool_use_id": "tc_002", "content": "result 2"},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[0]["tool_call_id"] == "tc_001"
        assert result[1]["role"] == "tool"
        assert result[1]["tool_call_id"] == "tc_002"

    def test_mixed_tool_result_and_text(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tc_001", "content": "result"},
                    {"type": "text", "text": "Also check this."},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert len(result) == 2
        assert result[0]["role"] == "tool"
        assert result[1]["role"] == "user"
        assert result[1]["content"] == "Also check this."

    def test_flatten_tools_true_uses_v1_behavior(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "x.rs"}},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=True)
        assert len(result) == 1
        assert result[0]["role"] == "assistant"
        assert "tool_calls" not in result[0]
        assert "[Tool call: read_file" in result[0]["content"]

    def test_full_conversation_with_tools(self):
        """Test a full multi-turn conversation with tool use and results."""
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {"role": "user", "content": "check my disk usage"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "tool-001", "name": "execute_shell_command",
                     "input": {"command": "df -h"}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool-001",
                     "content": "Filesystem Size Used\n/dev/sda1 100G 45G", "is_error": False},
                ],
            },
            {"role": "assistant", "content": "Your disk is at 45%."},
            {"role": "user", "content": "thanks"},
        ]

        result = translate_messages(messages, flatten_tools=False)

        assert result[0] == {"role": "user", "content": "check my disk usage"}
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Let me check."
        assert len(result[1]["tool_calls"]) == 1
        assert result[2]["role"] == "tool"
        assert result[2]["tool_call_id"] == "tool-001"
        assert result[3] == {"role": "assistant", "content": "Your disk is at 45%."}
        assert result[4] == {"role": "user", "content": "thanks"}

    def test_unknown_block_type_in_structured_mode(self):
        from atuin_ai_adapter.translator import translate_messages

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "weird_type", "data": "something"},
                ],
            }
        ]
        result = translate_messages(messages, flatten_tools=False)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert "Unknown block" in result[0]["content"]
```

Run and validate:
```bash
uv run pytest tests/test_translator.py -v
```

---

### Step 2.4: Create new fixture files

**Create `tests/fixtures/calls/continuation.json`:**

```json
{
  "messages": [
    {"role": "user", "content": "read my config file"},
    {
      "role": "assistant",
      "content": [
        {"type": "text", "text": "Let me read that file."},
        {
          "type": "tool_use",
          "id": "call_001",
          "name": "read_file",
          "input": {"file_path": "/etc/hosts"}
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "call_001",
          "content": "127.0.0.1 localhost\n::1 localhost",
          "is_error": false
        }
      ]
    }
  ],
  "context": {
    "os": "linux",
    "shell": "zsh",
    "pwd": "/home/user"
  },
  "config": {
    "capabilities": ["client_invocations", "client_v1_read_file"]
  },
  "invocation_id": "test-invocation-cont-001",
  "session_id": "session-cont-001"
}
```

**Create `tests/fixtures/calls/with_skills.json`:**

```json
{
  "messages": [
    {"role": "user", "content": "deploy the app"}
  ],
  "context": {
    "os": "linux",
    "shell": "bash",
    "pwd": "/home/user/myapp"
  },
  "config": {
    "capabilities": ["client_invocations", "client_v1_load_skill", "client_v1_execute_shell_command"],
    "skills": [
      {"name": "deploy", "description": "Deploy the application to production using Docker"},
      {"name": "release", "description": "Create a release with changelog and version bump"}
    ],
    "user_contexts": ["Always use Docker Compose for deployments"]
  },
  "invocation_id": "test-invocation-skill-001"
}
```

---

### Step 2.5: Wire tool infrastructure into `orchestrator.py`

Update the orchestrator to use the tool registry, structured message translation, and emit `tool_call` and `status` SSE events.

**Replace `src/atuin_ai_adapter/orchestrator.py`:**

```python
from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

from atuin_ai_adapter.backend import (
    BackendClient,
    BackendConnectionError,
    BackendDone,
    BackendError,
    BackendTextDelta,
    BackendToolCall,
)
from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.prompt import build_system_prompt
from atuin_ai_adapter.protocol import AtuinChatRequest, done_event, error_event, status_event, text_event, tool_call_event
from atuin_ai_adapter.tools import build_tool_registry, to_openai_tools
from atuin_ai_adapter.translator import translate_messages

logger = logging.getLogger(__name__)


async def handle_chat(
    request: AtuinChatRequest,
    backend: BackendClient,
    settings: Settings,
) -> AsyncIterator[str]:
    session_id = request.session_id or str(uuid.uuid4())

    try:
        # 1. Build tool registry from capabilities
        if settings.enable_tools and request.config:
            registry = build_tool_registry(request.config.capabilities)
            openai_tools = to_openai_tools(registry) or None
        else:
            registry = []
            openai_tools = None

        # 2. Build system prompt
        system_prompt = build_system_prompt(
            context=request.context,
            config=request.config,
            tools=registry,
            base_prompt=settings.system_prompt_template,
        )

        # 3. Translate messages
        flatten = not settings.enable_tools
        openai_messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            *translate_messages(request.messages, flatten_tools=flatten),
        ]

        # 4. Emit status
        yield status_event("Thinking")

        # 5. Stream from backend
        async for event in backend.stream_chat(
            messages=openai_messages,
            model=settings.vllm_model,
            tools=openai_tools,
            temperature=settings.generation_temperature,
            max_tokens=settings.generation_max_tokens,
            top_p=settings.generation_top_p,
        ):
            match event:
                case BackendTextDelta(content=content):
                    yield text_event(content)

                case BackendToolCall(id=tc_id, name=name, arguments=args):
                    yield tool_call_event(tc_id, name, args)

                case BackendDone():
                    pass  # handled below

                case BackendError(message=msg):
                    yield error_event(msg)
                    yield done_event(session_id)
                    return

        # 6. Done
        yield done_event(session_id)

    except BackendConnectionError as exc:
        logger.error("Backend connection error: %s", exc)
        yield error_event(str(exc))
        yield done_event(session_id)
    except Exception as exc:
        logger.error("Adapter error: %s", exc, exc_info=True)
        yield error_event("Internal adapter error")
        yield done_event(session_id)
```

**Key changes from Phase 1 orchestrator:**
- Builds tool registry from capabilities
- Uses `build_system_prompt()` instead of raw template
- Uses `translate_messages()` instead of `build_openai_messages()`
- Passes `tools` to backend
- Handles `BackendToolCall` events → emits `tool_call_event()`
- Emits `status_event("Thinking")` at the start of streaming

---

### Step 2.6: Create `prompt.py`

The prompt builder composes the system prompt from sections.

**Create `src/atuin_ai_adapter/prompt.py`:**

```python
from __future__ import annotations

from atuin_ai_adapter.protocol import AtuinConfig, AtuinContext
from atuin_ai_adapter.tools import ToolDefinition


def build_system_prompt(
    context: AtuinContext | None,
    config: AtuinConfig | None,
    tools: list[ToolDefinition],
    base_prompt: str,
) -> str:
    """Build the full system prompt from context, config, tools, and base template."""
    sections: list[str] = [base_prompt]

    # Environment context
    env_section = _build_environment_section(context)
    if env_section:
        sections.append(env_section)

    # Tool instructions
    tool_section = _build_tool_section(tools)
    if tool_section:
        sections.append(tool_section)

    # Skill summaries
    skill_section = _build_skill_section(config, tools)
    if skill_section:
        sections.append(skill_section)

    # User contexts
    user_section = _build_user_context_section(config)
    if user_section:
        sections.append(user_section)

    return "\n\n".join(sections)


def _build_environment_section(context: AtuinContext | None) -> str | None:
    if context is None:
        return None

    lines: list[str] = []
    field_map = [
        ("OS", context.os),
        ("Shell", context.shell),
        ("Distribution", context.distro),
        ("Working directory", context.pwd),
        ("Last command", context.last_command),
    ]
    for label, value in field_map:
        if value:
            lines.append(f"- {label}: {value}")

    if not lines:
        return None

    return "## Environment\n" + "\n".join(lines)


def _build_tool_section(tools: list[ToolDefinition]) -> str | None:
    if not tools:
        return None

    tool_lines = []
    for tool in tools:
        tool_lines.append(f"- {tool.name}: {tool.description}")

    guidelines = [
        "- When the user asks for a command, use suggest_command rather than just writing it in text.",
        "- Use read_file before edit_file to understand current file contents.",
        "- Prefer suggest_command over execute_shell_command when the user should review first.",
        "- For dangerous operations, set danger to \"high\" and include a warning.",
    ]

    # Only include guidelines for tools that are actually available
    tool_names = {t.name for t in tools}
    filtered_guidelines = []
    guideline_tool_deps = {
        0: {"suggest_command"},
        1: {"read_file", "edit_file"},
        2: {"suggest_command", "execute_shell_command"},
        3: {"suggest_command"},
    }
    for i, guideline in enumerate(guidelines):
        if guideline_tool_deps.get(i, set()) <= tool_names:
            filtered_guidelines.append(guideline)

    section = "## Available tools\nYou have the following tools available. Use them when appropriate:\n"
    section += "\n".join(tool_lines)

    if filtered_guidelines:
        section += "\n\n## Guidelines\n" + "\n".join(filtered_guidelines)

    return section


def _build_skill_section(config: AtuinConfig | None, tools: list[ToolDefinition]) -> str | None:
    if config is None or not config.skills:
        return None

    # Only show skills section if load_skill tool is available
    tool_names = {t.name for t in tools}
    if "load_skill" not in tool_names:
        return None

    lines = []
    for skill in config.skills:
        lines.append(f"- {skill.name}: {skill.description}")

    section = "## Available skills\n"
    section += "The user has the following skills installed. Use load_skill to load the full content when relevant:\n"
    section += "\n".join(lines)

    if config.skills_overflow:
        section += f"\n\nAdditional skills not shown: {config.skills_overflow}"

    return section


def _build_user_context_section(config: AtuinConfig | None) -> str | None:
    if config is None or not config.user_contexts:
        return None

    return "## User preferences\n" + "\n".join(f"- {ctx}" for ctx in config.user_contexts)
```

#### Test: `tests/test_prompt.py`

```python
"""Tests for prompt.py — system prompt composition."""
from __future__ import annotations

from atuin_ai_adapter.prompt import build_system_prompt
from atuin_ai_adapter.protocol import AtuinConfig, AtuinContext, AtuinSkillSummary
from atuin_ai_adapter.tools import build_tool_registry


BASE_PROMPT = "You are a test assistant."


class TestBuildSystemPrompt:
    def test_base_prompt_only(self):
        result = build_system_prompt(
            context=None, config=None, tools=[], base_prompt=BASE_PROMPT,
        )
        assert result == BASE_PROMPT

    def test_with_context(self):
        ctx = AtuinContext(os="linux", shell="zsh", pwd="/home/user")
        result = build_system_prompt(
            context=ctx, config=None, tools=[], base_prompt=BASE_PROMPT,
        )
        assert "## Environment" in result
        assert "- OS: linux" in result
        assert "- Shell: zsh" in result
        assert "- Working directory: /home/user" in result

    def test_context_omits_none_fields(self):
        ctx = AtuinContext(os="linux")
        result = build_system_prompt(
            context=ctx, config=None, tools=[], base_prompt=BASE_PROMPT,
        )
        assert "Shell" not in result
        assert "Working directory" not in result

    def test_with_tools(self):
        tools = build_tool_registry(["client_invocations"])
        result = build_system_prompt(
            context=None, config=None, tools=tools, base_prompt=BASE_PROMPT,
        )
        assert "## Available tools" in result
        assert "suggest_command" in result

    def test_no_tools_no_tool_section(self):
        result = build_system_prompt(
            context=None, config=None, tools=[], base_prompt=BASE_PROMPT,
        )
        assert "## Available tools" not in result

    def test_with_skills(self):
        config = AtuinConfig(
            capabilities=["client_v1_load_skill"],
            skills=[
                AtuinSkillSummary(name="deploy", description="Deploy to prod"),
                AtuinSkillSummary(name="release", description="Create a release"),
            ],
        )
        tools = build_tool_registry(config.capabilities)
        result = build_system_prompt(
            context=None, config=config, tools=tools, base_prompt=BASE_PROMPT,
        )
        assert "## Available skills" in result
        assert "deploy: Deploy to prod" in result
        assert "release: Create a release" in result
        assert "load_skill" in result

    def test_skills_without_load_skill_capability(self):
        config = AtuinConfig(
            capabilities=["client_invocations"],
            skills=[AtuinSkillSummary(name="deploy", description="Deploy")],
        )
        tools = build_tool_registry(config.capabilities)
        result = build_system_prompt(
            context=None, config=config, tools=tools, base_prompt=BASE_PROMPT,
        )
        # Skills section should NOT appear because load_skill is not available
        assert "## Available skills" not in result

    def test_with_user_contexts(self):
        config = AtuinConfig(user_contexts=["Always use sudo", "Prefer fish shell"])
        result = build_system_prompt(
            context=None, config=config, tools=[], base_prompt=BASE_PROMPT,
        )
        assert "## User preferences" in result
        assert "- Always use sudo" in result
        assert "- Prefer fish shell" in result

    def test_full_prompt(self):
        ctx = AtuinContext(os="linux", shell="zsh", pwd="/home/user")
        config = AtuinConfig(
            capabilities=["client_invocations", "client_v1_load_skill"],
            skills=[AtuinSkillSummary(name="deploy", description="Deploy")],
            user_contexts=["Use sudo"],
        )
        tools = build_tool_registry(config.capabilities)
        result = build_system_prompt(
            context=ctx, config=config, tools=tools, base_prompt=BASE_PROMPT,
        )
        # All sections present in order
        assert result.index("## Environment") < result.index("## Available tools")
        assert result.index("## Available tools") < result.index("## Available skills")
        assert result.index("## Available skills") < result.index("## User preferences")

    def test_skills_overflow(self):
        config = AtuinConfig(
            capabilities=["client_v1_load_skill"],
            skills=[AtuinSkillSummary(name="deploy", description="Deploy")],
            skills_overflow="build, test, lint",
        )
        tools = build_tool_registry(config.capabilities)
        result = build_system_prompt(
            context=None, config=config, tools=tools, base_prompt=BASE_PROMPT,
        )
        assert "Additional skills not shown: build, test, lint" in result
```

Run and validate:
```bash
uv run pytest tests/test_prompt.py -v
```

---

### Step 2.7: Update orchestrator tests for tool call flow

Add test cases to `test_service.py` (now `test_orchestrator.py`) for tool call emission and `enable_tools` flag:

```python
    async def test_tool_call_emitted(self):
        from atuin_ai_adapter.orchestrator import handle_chat
        from atuin_ai_adapter.backend import BackendToolCall

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "list files"}],
            invocation_id="test-inv-tool-1",
            config=AtuinConfig(capabilities=["client_invocations"]),
        )
        backend = AsyncMock(spec=BackendClient)

        async def mock_stream(**kwargs):
            yield BackendTextDelta(content="Here's a command:")
            yield BackendToolCall(
                id="call_123",
                name="suggest_command",
                arguments={"command": "ls -la"},
            )
            yield BackendDone()

        backend.stream_chat = mock_stream
        settings = make_settings(enable_tools=True)

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        status_frames = [f for f in frames if "event: status" in f]
        text_frames = [f for f in frames if "event: text" in f]
        tool_frames = [f for f in frames if "event: tool_call" in f]
        done_frames = [f for f in frames if "event: done" in f]

        assert len(status_frames) == 1
        assert '"state":"Thinking"' in status_frames[0]
        assert len(text_frames) == 1
        assert len(tool_frames) == 1
        assert '"name":"suggest_command"' in tool_frames[0]
        assert '"command":"ls -la"' in tool_frames[0] or '"command": "ls -la"' in tool_frames[0]
        assert len(done_frames) == 1

    async def test_enable_tools_false_no_tools(self):
        from atuin_ai_adapter.orchestrator import handle_chat

        request = AtuinChatRequest(
            messages=[{"role": "user", "content": "hello"}],
            invocation_id="test-inv-notool",
            config=AtuinConfig(capabilities=["client_invocations"]),
        )
        backend = AsyncMock(spec=BackendClient)
        received_kwargs = {}

        async def mock_stream(**kwargs):
            received_kwargs.update(kwargs)
            yield BackendTextDelta(content="hello")
            yield BackendDone()

        backend.stream_chat = mock_stream
        settings = make_settings(enable_tools=False)

        frames = [frame async for frame in handle_chat(request, backend, settings)]

        # When enable_tools=False, no tools should be passed to backend
        assert received_kwargs.get("tools") is None
```

You'll need to import `AtuinConfig` at the top of the test file:
```python
from atuin_ai_adapter.protocol import AtuinChatRequest, AtuinConfig
```

---

### Step 2.8: Update SSE event tests in `test_protocol.py`

Add tests for the new SSE event builders:

```python
class TestNewSSEEvents:
    def test_tool_call_event(self):
        from atuin_ai_adapter.protocol import tool_call_event

        result = tool_call_event("call_123", "suggest_command", {"command": "ls"})
        assert result.startswith("event: tool_call\n")
        assert '"id":"call_123"' in result or '"id": "call_123"' in result
        assert '"name":"suggest_command"' in result or '"name": "suggest_command"' in result

    def test_tool_result_event(self):
        from atuin_ai_adapter.protocol import tool_result_event

        result = tool_result_event("call_123", "file contents", remote=True, content_length=13)
        assert result.startswith("event: tool_result\n")
        assert '"tool_use_id":"call_123"' in result or '"tool_use_id": "call_123"' in result
        assert '"remote":true' in result or '"remote": true' in result

    def test_status_event(self):
        from atuin_ai_adapter.protocol import status_event

        result = status_event("Thinking")
        assert result.startswith("event: status\n")
        assert '"state":"Thinking"' in result or '"state": "Thinking"' in result

    def test_tool_call_event_model(self):
        from atuin_ai_adapter.protocol import AtuinToolCallEvent

        event = AtuinToolCallEvent(id="call_1", name="read_file", input={"file_path": "x"})
        data = event.model_dump()
        assert data["id"] == "call_1"
        assert data["name"] == "read_file"
        assert data["input"] == {"file_path": "x"}

    def test_status_event_model(self):
        from atuin_ai_adapter.protocol import AtuinStatusEvent

        event = AtuinStatusEvent(state="Processing")
        assert event.model_dump() == {"state": "Processing"}

    def test_skill_summary_model(self):
        from atuin_ai_adapter.protocol import AtuinSkillSummary

        skill = AtuinSkillSummary(name="deploy", description="Deploy to prod")
        assert skill.name == "deploy"
        assert skill.description == "Deploy to prod"
```

---

### Step 2.9: Validation

Run the full test suite:

```bash
uv run pytest -x -q --cov=atuin_ai_adapter --cov-report=term-missing
```

Also run linting:
```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

**Acceptance criteria for Phase 2:**
- [ ] All Phase 1 tests still pass
- [ ] `tools.py` tests pass — all 7 tools registered, schemas valid, capability mapping correct
- [ ] `backend.py` tool accumulation tests pass — single tool call, multiple tool calls, malformed args
- [ ] `translator.py` structured translation tests pass — tool_use → tool_calls, tool_result → role=tool
- [ ] `prompt.py` tests pass — all sections compose correctly
- [ ] Orchestrator tests pass — tool_call events emitted, enable_tools=False works
- [ ] New fixture files exist (continuation, with_skills, stream fixtures)
- [ ] New SSE event models and builders tested
- [ ] `ruff check` clean
- [ ] `ruff format` clean

---

## Phase 3: Full Integration and Testing

**Goal:** End-to-end tool flows work. Integration tests cover the full request→response cycle with tool calls.

---

### Step 3.1: Integration tests with FastAPI TestClient

Add integration tests to `test_app.py` that verify the full flow through the FastAPI app with mocked backend responses.

**Add to `test_app.py`:**

```python
class TestToolCallIntegration:
    """Integration tests for tool call flows through the full app."""

    def test_tool_call_sse_event_emitted(self, adapter_client: TestClient, httpx_mock: HTTPXMock):
        """A backend response with tool calls should produce tool_call SSE events."""
        from tests.conftest import load_stream

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

        tool_frames = [f for f in frames if f["event"] == "tool_call"]
        assert len(tool_frames) == 1
        assert tool_frames[0]["data"]["name"] == "suggest_command"
        assert tool_frames[0]["data"]["input"]["command"] == "ls -la"

    def test_continuation_request(self, adapter_client: TestClient, httpx_mock: HTTPXMock):
        """A request with tool results in history should translate correctly."""
        from tests.conftest import load_stream

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

    def test_skills_in_request(self, adapter_client: TestClient, httpx_mock: HTTPXMock):
        """A request with skills should parse correctly."""
        from tests.conftest import load_stream

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
```

You'll need to add these imports at the top of `test_app.py`:
```python
from tests.conftest import load_call, load_stream, parse_sse_frames, extract_events
```

---

### Step 3.2: Update the dummy server for E2E tests

Update `tests/helpers/dummy_openai_server.py` to support returning tool calls when requested, so E2E tests can verify tool_call SSE events.

**Add a tool-call response mode to the dummy server:**

```python
# Add at module level in dummy_openai_server.py:

TOOL_CALL_RESPONSE = [
    '{"id":"chatcmpl-tc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
    '{"id":"chatcmpl-tc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"Here\'s the command:"},"finish_reason":null}]}',
    '{"id":"chatcmpl-tc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_e2e","type":"function","function":{"name":"suggest_command","arguments":""}}]},"finish_reason":null}]}',
    '{"id":"chatcmpl-tc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\": \\"ls -la\\"}"}}]},"finish_reason":null}]}',
    '{"id":"chatcmpl-tc","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
]

# Add a second endpoint or query parameter to trigger tool-call mode
```

---

### Step 3.3: Clean up the old `build_openai_messages` function

After Phase 2, the old `build_openai_messages()` function in `translator.py` is no longer called by the orchestrator. The orchestrator now uses `translate_messages()` and `build_system_prompt()` separately.

Remove `build_openai_messages()` and `OpenAIChatMessage` from `translator.py` unless any tests still reference them directly. If tests rely on them, either:
1. Update those tests to use the new `translate_messages()` function, or
2. Keep `build_openai_messages()` as a legacy helper and mark it for removal

**Recommended approach:** Update all tests that use `build_openai_messages()` to use `translate_messages()` + `build_system_prompt()` instead. Then remove `build_openai_messages()` and `OpenAIChatMessage`.

---

### Step 3.4: Final validation

Run the complete test suite with coverage:

```bash
uv run pytest -x -q --cov=atuin_ai_adapter --cov-report=term-missing
```

Run linting and formatting:
```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

Run type checking (if mypy is configured):
```bash
uv run mypy src/
```

Run E2E tests (if backend is available):
```bash
RUN_CLI_E2E=1 uv run pytest tests/test_atuin_cli_e2e.py -v
```

---

### Step 3.5: Final acceptance criteria

**V2.1 is complete when all of the following are true:**

1. **V1 parity:** `enable_tools=False` produces identical behavior to V1 (text-only streaming, tool blocks flattened)
2. **Tool schemas:** With `enable_tools=True`, tool schemas are sent to the backend based on request capabilities
3. **Tool accumulation:** Backend tool-call deltas are accumulated and emitted as Atuin `tool_call` SSE events
4. **suggest_command:** `suggest_command` tool calls are emitted correctly as `tool_call` SSE events
5. **Continuation support:** Requests with `tool_use` + `tool_result` in message history are translated correctly to OpenAI format
6. **Status events:** `status` events are emitted during generation
7. **All client-side tools:** `read_file`, `edit_file`, `write_file`, `execute_shell_command`, `atuin_history`, `load_skill` have correct schemas matching Atuin's `tools/mod.rs`
8. **Skills:** Skill summaries are injected into the system prompt when present and `load_skill` is available
9. **Config:** `enable_tools` and `vllm_api_key` config fields work correctly
10. **Tests:** Unit and integration tests pass with >95% coverage
11. **Linting:** `ruff check` and `ruff format` clean
12. **No Atuin patches:** The adapter works with stock Atuin

---

## Appendix A: Complete file listing after refactor

```
src/atuin_ai_adapter/
    __init__.py          # empty
    app.py               # FastAPI app, routes, auth, lifespan (~85 lines)
    config.py            # Settings with enable_tools, vllm_api_key (~35 lines)
    protocol.py          # Atuin models + SSE event builders (~110 lines)
    tools.py             # Tool registry, schemas, capability mapping (~200 lines)
    orchestrator.py      # handle_chat() bridge logic (~70 lines)
    backend.py           # BackendClient, BackendEvent types, accumulator (~170 lines)
    translator.py        # translate_messages(), flatten_content_blocks() (~170 lines)
    prompt.py            # build_system_prompt() with sections (~110 lines)

tests/
    __init__.py
    conftest.py          # shared fixtures + helpers
    test_app.py          # FastAPI integration tests
    test_backend.py      # BackendClient + tool accumulation tests
    test_config.py       # Settings tests
    test_orchestrator.py # handle_chat() orchestration tests
    test_prompt.py       # System prompt composition tests
    test_protocol.py     # Atuin models + SSE builder tests
    test_tools.py        # Tool registry + capability mapping tests
    test_translator.py   # Message translation tests
    test_atuin_cli_e2e.py
    test_real_world_remora.py
    helpers/
        __init__.py
        dummy_openai_server.py
    fixtures/
        calls/
            minimal.json
            simple.json
            no_context.json
            conversation.json
            with_tools.json
            continuation.json       # NEW
            with_skills.json        # NEW
            auth_bad_token.json
        streams/
            happy_simple.txt
            happy_long.txt
            malformed_json.txt
            mid_stream_cut.txt
            upstream_500.txt
            with_role_chunk.txt
            with_tool_call.txt          # NEW
            with_multiple_tool_calls.txt # NEW
            malformed_tool_args.txt      # NEW
        responses/
            *.txt (auto-captured)
```

## Appendix B: Import migration cheat sheet

| Old import path | New import path |
|---|---|
| `atuin_ai_adapter.protocol.atuin.AtuinChatRequest` | `atuin_ai_adapter.protocol.AtuinChatRequest` |
| `atuin_ai_adapter.protocol.atuin.AtuinContext` | `atuin_ai_adapter.protocol.AtuinContext` |
| `atuin_ai_adapter.protocol.atuin.AtuinConfig` | `atuin_ai_adapter.protocol.AtuinConfig` |
| `atuin_ai_adapter.protocol.atuin.AtuinTextEvent` | `atuin_ai_adapter.protocol.AtuinTextEvent` |
| `atuin_ai_adapter.protocol.atuin.AtuinDoneEvent` | `atuin_ai_adapter.protocol.AtuinDoneEvent` |
| `atuin_ai_adapter.protocol.atuin.AtuinErrorEvent` | `atuin_ai_adapter.protocol.AtuinErrorEvent` |
| `atuin_ai_adapter.protocol.openai.OpenAIChatMessage` | Removed — use dicts or `translator.OpenAIChatMessage` |
| `atuin_ai_adapter.protocol.openai.OpenAIChatRequest` | Removed — backend builds dicts directly |
| `atuin_ai_adapter.sse.format_sse` | `atuin_ai_adapter.protocol.format_sse` |
| `atuin_ai_adapter.sse.text_event` | `atuin_ai_adapter.protocol.text_event` |
| `atuin_ai_adapter.sse.done_event` | `atuin_ai_adapter.protocol.done_event` |
| `atuin_ai_adapter.sse.error_event` | `atuin_ai_adapter.protocol.error_event` |
| `atuin_ai_adapter.service.handle_chat` | `atuin_ai_adapter.orchestrator.handle_chat` |
| `atuin_ai_adapter.vllm_client.VllmClient` | `atuin_ai_adapter.backend.BackendClient` |
| `atuin_ai_adapter.vllm_client.VllmError` | `atuin_ai_adapter.backend.BackendConnectionError` |
| N/A (new) | `atuin_ai_adapter.protocol.tool_call_event` |
| N/A (new) | `atuin_ai_adapter.protocol.tool_result_event` |
| N/A (new) | `atuin_ai_adapter.protocol.status_event` |
| N/A (new) | `atuin_ai_adapter.tools.build_tool_registry` |
| N/A (new) | `atuin_ai_adapter.tools.to_openai_tools` |
| N/A (new) | `atuin_ai_adapter.prompt.build_system_prompt` |
| N/A (new) | `atuin_ai_adapter.translator.translate_messages` |

## Appendix C: Data flow diagram

```
Atuin Client
    │
    │  POST /api/cli/chat
    │  Authorization: Bearer <token>
    │  Body: AtuinChatRequest
    ▼
app.py
    │  verify_token()
    │  parse AtuinChatRequest
    ▼
orchestrator.py :: handle_chat()
    │
    ├─ tools.py :: build_tool_registry(capabilities)
    │   └─ Returns: list[ToolDefinition]
    │
    ├─ tools.py :: to_openai_tools(registry)
    │   └─ Returns: list[dict] (OpenAI format)
    │
    ├─ prompt.py :: build_system_prompt(context, config, tools, base)
    │   └─ Returns: str (composed system prompt)
    │
    ├─ translator.py :: translate_messages(messages, flatten_tools=False)
    │   └─ Returns: list[dict] (OpenAI-format messages)
    │
    ├─ yield status_event("Thinking")
    │
    └─ backend.py :: BackendClient.stream_chat(messages, model, tools, ...)
        │
        │  POST /v1/chat/completions (with tools)
        │  stream=true
        │
        ├─ Text delta → BackendTextDelta → text_event()
        ├─ Tool call delta → accumulate...
        │   └─ Stream end → BackendToolCall → tool_call_event()
        ├─ Error → BackendError → error_event() + done_event()
        └─ Done → BackendDone → done_event()
        │
        ▼
    SSE Response to Atuin Client
        event: status    {"state":"Thinking"}
        event: text      {"content":"..."}
        event: tool_call {"id":"...","name":"...","input":{...}}
        event: done      {"session_id":"..."}
```

**For continuations:**
```
1. Adapter emits: text + tool_call + done
2. Atuin executes tool locally (e.g., read_file)
3. Atuin sends NEW request with updated messages:
   - Original user message
   - Assistant message with tool_use blocks
   - User message with tool_result blocks
4. Adapter translates full history → OpenAI format
5. Backend generates continuation response
6. Repeat until no more tool calls
```
