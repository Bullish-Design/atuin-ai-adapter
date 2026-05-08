# IMPLEMENTATION_GUIDE.md

# Atuin AI Adapter — Step-by-Step Implementation Guide

**Prerequisite reading:** Read `SPEC.md` in this directory before starting. It is the authoritative reference for every decision below. When in doubt, defer to the spec.

**Environment:** This project uses Nix devenv. All commands that touch Python tooling must be run via `devenv shell -- ...`. You do not need it for read-only commands like `ls`, `cat`, or `git`.

---

## How to Use This Guide

- Work through the steps **in order**. Each step builds on the previous one.
- Each step ends with a **Verification** section. Do not proceed to the next step until every check in that section passes.
- All file paths are relative to the repository root: `/home/andrew/Documents/Projects/atuin-ai-adapter/`.
- When the guide says "run tests," it means: `devenv shell -- pytest -q` (or a more targeted command if specified).
- When the guide says "run lint," it means: `devenv shell -- uv run ruff check src/ tests/`.
- When the guide says "run type check," it means: `devenv shell -- uv run mypy`.
- Before your very first test run, sync dependencies: `devenv shell -- uv sync --extra dev`.

---

## Step 0: Update `pyproject.toml`

The current `pyproject.toml` is a template placeholder. It references `template_py` and `embeddify` which do not exist. Update it so the project tooling points at the real package.

### What to change

1. **`[project]` section** — update `description`:
   ```toml
   description = "Adapter bridging Atuin AI protocol to vLLM/OpenAI-compatible backends."
   ```

2. **`dependencies`** — add the runtime dependencies:
   ```toml
   dependencies = [
     "pydantic>=2.12.5",
     "pydantic-settings>=2.0",
     "fastapi>=0.115",
     "uvicorn[standard]>=0.30",
     "httpx>=0.28",
   ]
   ```

3. **`[project.optional-dependencies] dev`** — add async test support:
   ```toml
   dev = [
     "pytest>=7.0",
     "pytest-asyncio>=0.24",
     "pytest-cov>=4.1",
     "pytest-httpx>=0.35",
     "mypy>=1.10",
     "ruff>=0.5.0",
   ]
   ```

4. **`[project.scripts]`** — add an entry point:
   ```toml
   [project.scripts]
   atuin-ai-adapter = "atuin_ai_adapter.app:main"
   ```

5. **`[tool.hatch.build.targets.wheel]`** — point at the real package:
   ```toml
   packages = ["src/atuin_ai_adapter"]
   ```

6. **`[tool.pytest.ini_options]`** — fix the coverage target:
   ```toml
   addopts = "-q --cov=atuin_ai_adapter --cov-report=term-missing"
   ```

7. **`[tool.mypy]`** — fix the package target:
   ```toml
   packages = ["src/atuin_ai_adapter"]
   ```

8. Leave everything else (ruff config, build-system, etc.) as-is.

### Create the package directory skeleton

```text
src/atuin_ai_adapter/__init__.py       (empty file)
src/atuin_ai_adapter/protocol/__init__.py  (empty file)
tests/__init__.py                      (empty file)
```

Create these as empty files. They establish the Python package structure so that imports resolve and tooling can find the code.

### Verification

```bash
devenv shell -- uv sync --extra dev
```

This must complete without errors. It confirms that all declared dependencies are installable and the package structure is recognized.

```bash
devenv shell -- python -c "import atuin_ai_adapter; print('ok')"
```

This must print `ok`. It confirms the package is importable.

---

## Step 1: `config.py` — Settings

Implement the configuration module. This is the foundation that every other module will depend on.

### What to implement

Create `src/atuin_ai_adapter/config.py` containing a `Settings` class built on `pydantic-settings`.

**Fields** (see SPEC.md §6 for the full table):

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `adapter_host` | `str` | `"127.0.0.1"` | |
| `adapter_port` | `int` | `8787` | |
| `adapter_api_token` | `str` | `"local-dev-token"` | |
| `vllm_base_url` | `str` | `"http://127.0.0.1:8000"` | |
| `vllm_model` | `str` | **no default** | Must raise if unset |
| `vllm_timeout` | `float` | `120.0` | |
| `generation_temperature` | `float` | `0.7` | |
| `generation_max_tokens` | `int` | `2048` | |
| `generation_top_p` | `float` | `0.95` | |
| `system_prompt_template` | `str` | *(the default preamble from SPEC.md §5.1)* | |
| `log_level` | `str` | `"INFO"` | |

**Important details:**
- Use `model_config = SettingsConfigDict(env_file=".env", extra="ignore")`.
- Environment variable names are the UPPER_CASE versions of the field names (e.g., `VLLM_MODEL`). Pydantic-settings does this automatically.
- `vllm_model` has no default. If the env var `VLLM_MODEL` is not set, Pydantic will raise a `ValidationError` at construction time. This is the desired behavior — the adapter must refuse to start without a model name.
- The default `system_prompt_template` is the multi-line string from SPEC.md §5.1 (everything above the `Environment:` section — the static preamble only, not the environment variables).

**Provide a `get_settings()` function** that creates and caches a `Settings` instance using `functools.lru_cache`:

```python
from functools import lru_cache

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

### What to test

Create `tests/test_config.py`:

1. **Test defaults are applied** — Set only `VLLM_MODEL` in the environment (via `monkeypatch`). Instantiate `Settings`. Assert that `adapter_host == "127.0.0.1"`, `adapter_port == 8787`, `generation_temperature == 0.7`, etc.

2. **Test env override** — Set `VLLM_MODEL=test-model` and `ADAPTER_PORT=9999`. Assert `settings.adapter_port == 9999`.

3. **Test missing required field** — Do not set `VLLM_MODEL`. Assert that constructing `Settings()` raises `ValidationError`.

4. **Test system prompt default is non-empty** — Assert `settings.system_prompt_template` contains the string `"terminal assistant"` (or whatever key phrase is in your default).

### Verification

```bash
devenv shell -- pytest tests/test_config.py -v
```

All tests pass.

```bash
devenv shell -- uv run ruff check src/atuin_ai_adapter/config.py tests/test_config.py
```

No lint errors.

---

## Step 2: `protocol/atuin.py` — Atuin Request/Response Models

Implement the Pydantic models that represent the Atuin side of the protocol.

### What to implement

Create `src/atuin_ai_adapter/protocol/atuin.py` with the models defined in SPEC.md §9.2 (`protocol/atuin.py` section):

- `AtuinContext` — optional environment fields (os, shell, distro, pwd, last_command).
- `AtuinConfig` — capabilities, user_contexts, skills, skills_overflow. All optional with defaults.
- `AtuinChatRequest` — the top-level request model. Required: `messages` (list of dicts), `invocation_id` (str). Optional: `context`, `config`, `session_id`.
- `AtuinTextEvent` — `{"content": "..."}`.
- `AtuinDoneEvent` — `{"session_id": "..."}`.
- `AtuinErrorEvent` — `{"message": "..."}`.

**Every model** must use `model_config = ConfigDict(extra="ignore")`.

**`messages` is typed as `list[dict[str, Any]]`** — not strongly typed. The translator will handle the internal structure. This is deliberate: Atuin's message format may evolve, and we want the Pydantic layer to accept it without breaking.

### What to test

Create `tests/test_protocol_atuin.py`:

1. **Parse a minimal valid request** — JSON with only `messages` (one user message) and `invocation_id`. Assert it parses without error. Assert `context is None`, `session_id is None`, `config is None`.

2. **Parse a full request** — JSON with all fields populated (messages, context, config, invocation_id, session_id). Assert all fields are populated correctly.

3. **Extra fields are ignored** — Add an unknown field `"foo": "bar"` to the top level. Assert parsing succeeds and the unknown field is silently dropped.

4. **Missing required field `messages`** — Omit `messages`. Assert `ValidationError` is raised.

5. **Missing required field `invocation_id`** — Omit `invocation_id`. Assert `ValidationError` is raised.

6. **Context with partial fields** — Provide only `os` and `shell` in context. Assert `pwd is None`, `last_command is None`.

7. **Event model serialization** — Create `AtuinTextEvent(content="hello")`, call `.model_dump_json()`, assert the output is `'{"content":"hello"}'`.

8. **Done event serialization** — Create `AtuinDoneEvent(session_id="abc")`, assert JSON output.

9. **Error event serialization** — Create `AtuinErrorEvent(message="boom")`, assert JSON output.

### Verification

```bash
devenv shell -- pytest tests/test_protocol_atuin.py -v
```

All tests pass.

```bash
devenv shell -- uv run ruff check src/atuin_ai_adapter/protocol/ tests/test_protocol_atuin.py
```

No lint errors.

---

## Step 3: `protocol/openai.py` — OpenAI Request Models

Implement the Pydantic models for the upstream vLLM/OpenAI side.

### What to implement

Create `src/atuin_ai_adapter/protocol/openai.py`:

- `OpenAIChatMessage` — `role: str`, `content: str`.
- `OpenAIChatRequest` — `model: str`, `messages: list[OpenAIChatMessage]`, `stream: bool = True`, `temperature: float | None = None`, `max_tokens: int | None = None`, `top_p: float | None = None`.

These are simpler than the Atuin models. No `extra="ignore"` needed — we fully control these objects.

**Note:** We do not model the streaming response chunks as Pydantic models. They are parsed with lightweight dict access in `vllm_client.py` (SPEC.md §9.2). This is a performance choice — no need to validate every chunk from our own upstream.

### What to test

Create `tests/test_protocol_openai.py`:

1. **Construct a valid request** — Create `OpenAIChatRequest` with model, messages, and generation params. Call `.model_dump(exclude_none=True)`. Assert the dict has the expected shape and that `stream` is `True`.

2. **None params are excluded** — Create a request without setting `temperature`. Assert `"temperature"` is not in the `model_dump(exclude_none=True)` output.

3. **Message serialization** — Create `OpenAIChatMessage(role="user", content="hello")`. Assert `model_dump()` returns the expected dict.

### Verification

```bash
devenv shell -- pytest tests/test_protocol_openai.py -v
```

All tests pass.

---

## Step 4: `sse.py` — SSE Frame Formatting

Implement the SSE formatting utilities. This is a small module but critical for correctness.

### What to implement

Create `src/atuin_ai_adapter/sse.py` with:

```python
def format_sse(event: str, data: str) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {data}\n\n"
```

And three convenience functions that JSON-serialize their payloads:

```python
def text_event(content: str) -> str:
    """Format an Atuin 'text' SSE event."""

def done_event(session_id: str) -> str:
    """Format an Atuin 'done' SSE event."""

def error_event(message: str) -> str:
    """Format an Atuin 'error' SSE event."""
```

Each convenience function:
1. Creates the appropriate `AtuinTextEvent` / `AtuinDoneEvent` / `AtuinErrorEvent`.
2. Calls `.model_dump_json()` on it to get the JSON string.
3. Passes it to `format_sse()` with the correct event name.

**Or** — if you prefer not to import the protocol models here, you can use `json.dumps()` directly with a plain dict. Either approach is fine. The key requirement is that the `data:` line contains valid JSON.

### What to test

Create `tests/test_sse.py`:

1. **`format_sse` produces correct frame** — Assert `format_sse("text", '{"content":"hi"}')` equals `'event: text\ndata: {"content":"hi"}\n\n'`.

2. **`text_event` produces correct output** — Assert `text_event("hello world")` equals `'event: text\ndata: {"content":"hello world"}\n\n'`.

3. **`done_event` produces correct output** — Assert `done_event("session-123")` contains `"session_id"` and `"session-123"` in the data line.

4. **`error_event` produces correct output** — Assert `error_event("something broke")` contains `"message"` and `"something broke"`.

5. **JSON escaping** — Assert `text_event('line1\nline2')` produces a data line where the content is properly JSON-escaped (the `\n` should appear as `\\n` in the JSON string, not as a literal newline). Parse the data portion back as JSON and verify the content field equals the original string with the newline.

6. **Quotes in content** — Assert `text_event('say "hello"')` produces valid JSON in the data line. Parse it back and verify.

### Verification

```bash
devenv shell -- pytest tests/test_sse.py -v
```

All tests pass.

---

## Step 5: `translator.py` — Message Translation

This is the core logic module. It converts Atuin requests into OpenAI-compatible messages.

### What to implement

Create `src/atuin_ai_adapter/translator.py` with two public functions:

#### `flatten_content_blocks(content: str | list[dict[str, Any]]) -> str`

Handles the three cases from SPEC.md §5.2:

- **String content:** return as-is.
- **List of blocks:** iterate and render each block, join with `"\n\n"`.
  - `{"type": "text", "text": "..."}` → use `text` value verbatim.
  - `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}` → `[Tool call: {name}({json of input})]`.
  - `{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": false}` → `[Tool result ({tool_use_id}): {content}]`. If `is_error` is true: `[Tool error ({tool_use_id}): {content}]`.
  - Unknown block type → `[Unknown block: {json dump of entire block}]` and log a WARNING.
- **Anything else:** `str()` it and log a WARNING.

#### `build_openai_messages(request: AtuinChatRequest, system_prompt_template: str) -> list[OpenAIChatMessage]`

1. **Build the system message:**
   - Start with `system_prompt_template` (the static preamble).
   - Append `"\n\nEnvironment:"`.
   - For each of `(os, shell, distro, pwd, last_command)` in `request.context`: if the value is non-None and non-empty, append a line like `"\n- OS: {value}"`. Use the display labels from SPEC.md §5.1 (`OS`, `Shell`, `Distribution`, `Working directory`, `Last command`).
   - If `request.context` is None or all fields are empty, still include the preamble but omit the `Environment:` section entirely.
   - If `request.config` is not None and `request.config.user_contexts` is non-empty, append `"\n\nUser context:"` followed by each entry on its own line.
   - Wrap the result in `OpenAIChatMessage(role="system", content=...)`.

2. **Translate each message in `request.messages`:**
   - Extract `role` from the message dict (default to `"user"` if missing).
   - Extract `content` from the message dict.
   - Call `flatten_content_blocks(content)` to get a plain string.
   - Create `OpenAIChatMessage(role=role, content=flattened)`.

3. **Return** `[system_message, *translated_messages]`.

### What to test

Create `tests/test_translator.py`:

1. **Simple text message** — A single user message with string content. Assert the output has 2 messages: system + user. Assert user content matches input.

2. **System prompt includes context** — Provide context with `os="linux"`, `shell="zsh"`, `pwd="/home/test"`. Assert the system message contains `"OS: linux"`, `"Shell: zsh"`, `"Working directory: /home/test"`.

3. **System prompt omits missing context fields** — Provide context with only `os="linux"` (everything else None). Assert the system message contains `"OS: linux"` but does not contain `"Shell:"`, `"Distribution:"`, etc.

4. **System prompt with no context at all** — `context=None`. Assert the system message contains the preamble text but does not contain `"Environment:"`.

5. **System prompt with user_contexts** — Provide `config.user_contexts=["Always use sudo", "Prefer fish shell"]`. Assert the system message contains both strings.

6. **Multi-turn conversation** — Provide messages: user, assistant, user. Assert output is: system, user, assistant, user (4 messages total). Assert roles are correct.

7. **Content block with text type** — A user message with `content=[{"type": "text", "text": "hello"}]`. Assert flattened content is `"hello"`.

8. **Content block with tool_use** — An assistant message with mixed text + tool_use blocks. Assert the flattened content contains the text and a `[Tool call: ...]` string.

9. **Content block with tool_result** — A user message with a tool_result block. Assert flattened content contains `[Tool result ...]`.

10. **Content block with tool_result error** — A tool_result with `is_error=true`. Assert flattened content contains `[Tool error ...]`.

11. **Unknown block type** — A message with `content=[{"type": "magic", "data": 42}]`. Assert flattened content contains `[Unknown block: ...]`.

12. **Empty messages list** — `messages=[]`. Assert output is just the system message (1 message).

13. **Custom system prompt template** — Pass a custom `system_prompt_template="Custom prompt."`. Assert the system message starts with `"Custom prompt."`.

14. **`flatten_content_blocks` with plain string** — Assert it returns the string unchanged.

15. **`flatten_content_blocks` with non-string, non-list** — Pass an integer. Assert it returns the `str()` representation.

### Verification

```bash
devenv shell -- pytest tests/test_translator.py -v
```

All tests pass.

```bash
devenv shell -- uv run ruff check src/atuin_ai_adapter/translator.py tests/test_translator.py
```

No lint errors.

---

## Step 6: `vllm_client.py` — Upstream Streaming Client

Implement the async HTTP client that talks to vLLM.

### What to implement

Create `src/atuin_ai_adapter/vllm_client.py` with a `VllmClient` class:

```python
class VllmClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        # Create self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def stream_chat(self, request: OpenAIChatRequest) -> AsyncIterator[str | None]:
        # See below

    async def health_check(self) -> bool:
        # GET /v1/models, return True if 200, False otherwise

    async def close(self) -> None:
        # await self._client.aclose()
```

#### `stream_chat` implementation details

1. Build the request body: `request.model_dump(exclude_none=True)`.
2. Send `POST /v1/chat/completions` using `self._client.stream("POST", ...)`.
3. Use `async with` to manage the streaming response.
4. Check the response status code. If not 2xx, read the body and raise an exception with a descriptive message (include status code and a snippet of the body).
5. Iterate over the response using `response.aiter_lines()`.
6. For each line:
   - Strip whitespace.
   - Skip empty lines.
   - If the line is `data: [DONE]`, return (end the generator).
   - If the line starts with `data: `, strip the prefix and parse the rest as JSON.
   - Extract `choices[0]["delta"].get("content")`.
   - Yield the content (may be `None` if the delta has no content field).
7. Wrap the streaming logic so that connection errors (`httpx.ConnectError`, `httpx.TimeoutException`, etc.) propagate as exceptions with descriptive messages.

#### Error types

Define a simple exception class at module level:

```python
class VllmError(Exception):
    """Raised when the upstream vLLM server returns an error or is unreachable."""
```

Use this for all upstream failures so that `service.py` can catch it with a single except clause.

### What to test

Create `tests/test_vllm_client.py`:

These tests use `pytest-httpx` to mock the upstream HTTP server.

1. **Happy path** — Mock a streaming response with 3 data lines containing text deltas, then `data: [DONE]`. Iterate `stream_chat()` and collect results. Assert you get 3 non-None strings matching the expected deltas.

2. **Delta with null content** — Include a chunk where `delta` has no `content` key (e.g., the initial role chunk). Assert `None` is yielded for that chunk.

3. **Upstream returns 500** — Mock a 500 response. Assert `stream_chat()` raises `VllmError` with a message containing "500".

4. **Upstream unreachable** — Configure `pytest-httpx` to raise a `ConnectError`. Assert `stream_chat()` raises `VllmError`.

5. **Health check success** — Mock `GET /v1/models` returning 200. Assert `health_check()` returns `True`.

6. **Health check failure** — Mock `GET /v1/models` raising a connection error. Assert `health_check()` returns `False`.

**Important:** Tests for this module require `pytest-asyncio`. Add `@pytest.mark.asyncio` to each async test, or use `asyncio_mode = "auto"` in pytest config.

If you haven't already, add to `pyproject.toml`:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

### Verification

```bash
devenv shell -- pytest tests/test_vllm_client.py -v
```

All tests pass.

---

## Step 7: `service.py` — Bridge Orchestration

This module ties everything together: translate, stream, emit SSE.

### What to implement

Create `src/atuin_ai_adapter/service.py` with:

```python
async def handle_chat(
    request: AtuinChatRequest,
    vllm_client: VllmClient,
    settings: Settings,
) -> AsyncIterator[str]:
```

This is an **async generator** that yields SSE frame strings (each is a complete `event: ...\ndata: ...\n\n`).

#### Logic

1. **Resolve session_id:**
   - If `request.session_id` is not None, use it.
   - Otherwise, generate a new one with `str(uuid.uuid4())`.

2. **Translate the request:**
   - Call `build_openai_messages(request, settings.system_prompt_template)`.
   - Construct an `OpenAIChatRequest` with:
     - `model=settings.vllm_model`
     - `messages=translated`
     - `temperature=settings.generation_temperature`
     - `max_tokens=settings.generation_max_tokens`
     - `top_p=settings.generation_top_p`

3. **Stream upstream and emit SSE:**
   - Wrap the entire upstream interaction in `try/except`.
   - Call `vllm_client.stream_chat(openai_request)`.
   - For each yielded delta:
     - If the delta is not None and not empty, yield `text_event(delta)`.
   - After the stream completes normally, yield `done_event(session_id)`.

4. **Error handling:**
   - Catch `VllmError` and any other `Exception`.
   - Log the error at ERROR level with `request.invocation_id`.
   - Yield `error_event(descriptive_message)`.
   - Yield `done_event(session_id)`.
   - **Every exit path must yield a `done` event.** This is critical.

### What to test

Create `tests/test_service.py`:

For these tests, you need to mock `VllmClient`. The cleanest approach is to create a fake/mock `VllmClient` whose `stream_chat` is an async generator you control.

1. **Happy path** — Mock `stream_chat` to yield `["hello", " ", "world"]`. Collect all SSE frames from `handle_chat`. Assert:
   - 3 `text` events with the correct content.
   - 1 `done` event at the end.
   - The done event contains a session_id.

2. **Session ID echo** — Provide a request with `session_id="my-session"`. Assert the done event contains `"my-session"`.

3. **Session ID generation** — Provide a request without `session_id`. Assert the done event contains a UUID-formatted string.

4. **Upstream error** — Mock `stream_chat` to raise `VllmError("connection refused")`. Assert:
   - 1 `error` event is yielded.
   - 1 `done` event follows the error.
   - No `text` events.

5. **Mid-stream error** — Mock `stream_chat` to yield 2 text deltas then raise `VllmError`. Assert:
   - 2 `text` events.
   - 1 `error` event.
   - 1 `done` event.

6. **None deltas are skipped** — Mock `stream_chat` to yield `[None, "hello", None]`. Assert only 1 `text` event (for "hello").

7. **Empty string deltas are skipped** — Mock `stream_chat` to yield `["", "hello", ""]`. Assert only 1 `text` event.

### Verification

```bash
devenv shell -- pytest tests/test_service.py -v
```

All tests pass.

---

## Step 8: `app.py` — FastAPI Application

This is the final assembly step. Wire everything into a running HTTP server.

### What to implement

Create `src/atuin_ai_adapter/app.py`:

#### Lifespan

Use FastAPI's `@asynccontextmanager` lifespan to manage startup/shutdown:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[dict]:
    settings = get_settings()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper()),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # Create vLLM client
    vllm_client = VllmClient(
        base_url=settings.vllm_base_url,
        timeout=settings.vllm_timeout,
    )

    yield {"vllm_client": vllm_client, "settings": settings}

    await vllm_client.close()
```

Store these in `app.state` via the lifespan dict so route handlers can access them.

#### Auth dependency

Create a FastAPI dependency that validates the bearer token:

```python
async def verify_token(request: Request) -> None:
    settings = request.state.settings  # or however you access it
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {settings.adapter_api_token}":
        raise HTTPException(status_code=401, detail="Invalid or missing API token")
```

**Or** use a simpler approach: extract settings from `app.state` in the dependency. The exact wiring pattern is up to you — just make sure the token check happens before any streaming begins.

#### Routes

**`POST /api/cli/chat`:**
1. Validate the auth token (via dependency).
2. Read the raw request body and parse it into `AtuinChatRequest`.
3. Log `invocation_id` at INFO level.
4. Return `StreamingResponse(handle_chat(atuin_request, vllm_client, settings), media_type="text/event-stream")`.

**Note on request parsing:** FastAPI's normal Pydantic body parsing works fine here. Declare the route parameter as `chat_request: AtuinChatRequest` in the function signature.

**`GET /health`:**
Return `{"status": "ok"}`.

**`GET /health/ready`:**
1. Call `vllm_client.health_check()`.
2. If True: return `{"status": "ready", "upstream": "reachable"}` with HTTP 200.
3. If False: return `JSONResponse({"status": "not_ready", "upstream": "unreachable"}, status_code=503)`.

#### `main()` entry point

```python
def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "atuin_ai_adapter.app:app",
        host=settings.adapter_host,
        port=settings.adapter_port,
        log_level=settings.log_level.lower(),
    )
```

This is what the `[project.scripts]` entry point calls.

### What to test

Create `tests/test_app.py`:

These are **integration tests** that exercise the full stack with mocked upstream.

Use `httpx.AsyncClient` with `ASGITransport` to test the FastAPI app directly, and `pytest-httpx` to mock the vLLM upstream.

**Important setup:** You need to ensure that the app is created with the right settings for testing. The simplest approach:
- Set `VLLM_MODEL=test-model` and `ADAPTER_API_TOKEN=test-token` in the test environment (via `monkeypatch` or a fixture).
- Clear the `lru_cache` on `get_settings` between tests if needed.

1. **Happy path end-to-end** — Mock vLLM to return a streaming response with 2 text chunks + `[DONE]`. Send a valid Atuin request to `/api/cli/chat` with the correct token. Assert:
   - HTTP 200.
   - Response content-type is `text/event-stream`.
   - Body contains `event: text` lines with the expected content.
   - Body ends with `event: done`.

2. **Auth rejection** — Send a request with `Authorization: Bearer wrong-token`. Assert HTTP 401.

3. **Missing auth header** — Send a request with no Authorization header. Assert HTTP 401.

4. **Invalid request body** — Send a request with missing `messages` field. Assert HTTP 422.

5. **Health endpoint** — GET `/health`. Assert 200 and `{"status": "ok"}`.

6. **Readiness endpoint — upstream up** — Mock `GET /v1/models` to return 200. GET `/health/ready`. Assert 200.

7. **Readiness endpoint — upstream down** — Mock `GET /v1/models` to fail. GET `/health/ready`. Assert 503.

8. **Upstream error produces SSE error** — Mock vLLM to return 500. Send a valid Atuin request. Assert the response body contains `event: error` followed by `event: done`.

9. **Session ID round-trip** — Send a request with `session_id="test-session-id"`. Assert the `done` event data contains `"test-session-id"`.

### Verification

```bash
devenv shell -- pytest tests/test_app.py -v
```

All tests pass.

```bash
devenv shell -- pytest -v
```

**All tests across all modules pass.** This is the moment where you confirm the entire test suite is green.

---

## Step 9: Full Quality Gate

Run the complete quality gate from `AGENTS.md`:

### 9a. Lint

```bash
devenv shell -- uv run ruff check src/ tests/
```

Must produce no errors. If there are errors, fix them and re-run.

### 9b. Format check

```bash
devenv shell -- uv run ruff format --check src/ tests/
```

Must produce no errors. If formatting is off:

```bash
devenv shell -- uv run ruff format src/ tests/
```

Then re-run the check.

### 9c. Type check

```bash
devenv shell -- uv run mypy
```

Must produce no errors. This will catch:
- Missing type annotations.
- Incorrect types passed between modules.
- Pydantic model misuse.

If mypy reports errors, fix them. Common issues:
- `httpx` may need type stubs: `pip install types-httpx` (add to dev deps if needed), or use `# type: ignore` sparingly with a comment explaining why.
- Async generators need `AsyncIterator` from `collections.abc`.
- `dict[str, Any]` needs `from typing import Any`.

### 9d. Full test suite

```bash
devenv shell -- pytest -v --cov=atuin_ai_adapter --cov-report=term-missing
```

All tests pass. Review the coverage report. Target: every module should have at least some coverage. The translator and SSE modules should be near 100%. The app module may have slightly lower coverage depending on how much of the lifespan logic is exercised.

### Verification

All four commands pass cleanly. This is the gate you must pass before the code is considered complete.

---

## Step 10: Test Fixtures

Create realistic test fixtures so future tests and debugging are easier.

### What to create

Create a `tests/fixtures/` directory with these JSON files:

#### `tests/fixtures/valid_request_simple.json`

```json
{
  "messages": [
    {"role": "user", "content": "how do I list files by size?"}
  ],
  "context": {
    "os": "linux",
    "shell": "zsh",
    "pwd": "/home/user/projects"
  },
  "config": {
    "capabilities": ["client_invocations"]
  },
  "invocation_id": "test-invocation-001"
}
```

#### `tests/fixtures/valid_request_conversation.json`

```json
{
  "messages": [
    {"role": "user", "content": "how do I find large files?"},
    {"role": "assistant", "content": "You can use `find / -size +100M` to find files larger than 100MB."},
    {"role": "user", "content": "how about only in the current directory?"}
  ],
  "context": {
    "os": "linux",
    "shell": "bash",
    "pwd": "/var/log",
    "last_command": "find / -size +100M"
  },
  "config": {
    "capabilities": ["client_invocations"],
    "user_contexts": []
  },
  "invocation_id": "test-invocation-002",
  "session_id": "session-abc-123"
}
```

#### `tests/fixtures/valid_request_with_tools.json`

```json
{
  "messages": [
    {"role": "user", "content": "check my disk usage"},
    {
      "role": "assistant",
      "content": [
        {"type": "text", "text": "Let me check your disk usage."},
        {
          "type": "tool_use",
          "id": "tool-001",
          "name": "execute_shell_command",
          "input": {"command": "df -h"}
        }
      ]
    },
    {
      "role": "user",
      "content": [
        {
          "type": "tool_result",
          "tool_use_id": "tool-001",
          "content": "Filesystem      Size  Used Avail Use% Mounted on\n/dev/sda1       100G   45G   55G  45% /",
          "is_error": false
        }
      ]
    },
    {"role": "assistant", "content": "Your root filesystem is at 45% capacity with 55GB free."},
    {"role": "user", "content": "thanks, what about my home directory?"}
  ],
  "context": {
    "os": "linux",
    "shell": "zsh",
    "distro": "arch",
    "pwd": "/home/user"
  },
  "config": {
    "capabilities": ["client_invocations", "client_v1_execute_shell_command"]
  },
  "invocation_id": "test-invocation-003",
  "session_id": "session-def-456"
}
```

#### `tests/fixtures/vllm_stream_simple.txt`

```text
data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"find"},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" . -size"},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":" +100M"},"finish_reason":null}]}

data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":""},"finish_reason":"stop"}]}

data: [DONE]
```

### Refactor existing tests (optional but recommended)

Go back through your existing tests and see if any of them would benefit from loading these fixtures instead of inline JSON. If a test uses a complex multi-message request, load it from the fixture file instead. This reduces duplication and makes the test data easier to maintain.

Create a small helper in `tests/conftest.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"

def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())
```

### Verification

```bash
devenv shell -- pytest -v
```

All tests still pass (refactoring didn't break anything).

Manually inspect each fixture file to confirm the JSON is valid:

```bash
devenv shell -- python -m json.tool tests/fixtures/valid_request_simple.json > /dev/null && echo "ok"
devenv shell -- python -m json.tool tests/fixtures/valid_request_conversation.json > /dev/null && echo "ok"
devenv shell -- python -m json.tool tests/fixtures/valid_request_with_tools.json > /dev/null && echo "ok"
```

All print `ok`.

---

## Step 11: Manual Smoke Test

Now test the adapter as a running server. This requires a vLLM server (or any OpenAI-compatible server) running somewhere.

### 11a. Start the adapter

Set the required environment variable and start:

```bash
VLLM_MODEL=your-model-name devenv shell -- python -m uvicorn atuin_ai_adapter.app:app --host 127.0.0.1 --port 8787
```

Replace `your-model-name` with the model loaded in your vLLM instance.

You should see uvicorn startup logs. The server should be listening on `http://127.0.0.1:8787`.

### 11b. Test health endpoint

In another terminal:

```bash
curl -s http://127.0.0.1:8787/health | python -m json.tool
```

Expected:
```json
{
    "status": "ok"
}
```

### 11c. Test readiness endpoint

```bash
curl -s http://127.0.0.1:8787/health/ready | python -m json.tool
```

If vLLM is running: `{"status": "ready", "upstream": "reachable"}`.
If vLLM is not running: `{"status": "not_ready", "upstream": "unreachable", ...}` with HTTP 503.

### 11d. Test auth rejection

```bash
curl -s -X POST http://127.0.0.1:8787/api/cli/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer wrong-token" \
  -d '{"messages":[{"role":"user","content":"test"}],"invocation_id":"test"}' \
  -w "\nHTTP status: %{http_code}\n"
```

Expected: HTTP 401.

### 11e. Test streaming chat (requires vLLM running)

```bash
curl -s -N -X POST http://127.0.0.1:8787/api/cli/chat \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer local-dev-token" \
  -d '{
    "messages": [{"role": "user", "content": "what command lists files by size?"}],
    "context": {"os": "linux", "shell": "zsh", "pwd": "/home/user"},
    "invocation_id": "smoke-test-001"
  }'
```

Expected: You should see SSE events streaming in:
```
event: text
data: {"content":"ls"}

event: text
data: {"content":" -lS"}

...

event: done
data: {"session_id":"<some-uuid>"}
```

The exact text depends on the model. The key things to verify:
- You see multiple `event: text` lines (streaming works).
- The stream ends with `event: done`.
- The done event has a `session_id`.

### 11f. Test with Atuin (the real thing)

Configure Atuin per SPEC.md §12:

```toml
# ~/.config/atuin/config.toml
[ai]
enabled = true
endpoint = "http://127.0.0.1:8787"
api_token = "local-dev-token"

[ai.opening]
send_cwd = true
send_last_command = true

[ai.capabilities]
enable_history_search = false
enable_file_tools = false
enable_command_execution = false
```

Then in a terminal:
1. Type `?` at an empty prompt.
2. Type a question like "how do I check disk space?".
3. You should see text stream in within the Atuin TUI.
4. You should be able to follow up with another question.

If this works, **the v1 adapter is functionally complete**.

### Verification

All 11a-11f tests produce the expected results. The adapter is working end-to-end.

---

## Step 12: Final Checklist

Before declaring the implementation done, verify every item:

| # | Check | Command / Action | Pass? |
|---|-------|------------------|-------|
| 1 | All unit tests pass | `devenv shell -- pytest -v` | |
| 2 | Coverage is reasonable | `devenv shell -- pytest --cov=atuin_ai_adapter --cov-report=term-missing` | |
| 3 | Lint clean | `devenv shell -- uv run ruff check src/ tests/` | |
| 4 | Format clean | `devenv shell -- uv run ruff format --check src/ tests/` | |
| 5 | Type check clean | `devenv shell -- uv run mypy` | |
| 6 | Health endpoint works | `curl http://127.0.0.1:8787/health` | |
| 7 | Auth rejects bad token | `curl -X POST ... -H "Authorization: Bearer wrong"` → 401 | |
| 8 | Streaming works with vLLM | curl smoke test shows text + done events | |
| 9 | Atuin integration works | `?` at empty prompt streams a response | |
| 10 | Multi-turn works | Follow-up question in same Atuin session works | |
| 11 | No Atuin patches required | Atuin is unmodified, only config changed | |

---

## Summary of Files Created

When complete, the repository should contain these new/modified files:

```text
Modified:
  pyproject.toml                              (Step 0)

Created:
  src/atuin_ai_adapter/__init__.py            (Step 0)
  src/atuin_ai_adapter/config.py              (Step 1)
  src/atuin_ai_adapter/protocol/__init__.py   (Step 0)
  src/atuin_ai_adapter/protocol/atuin.py      (Step 2)
  src/atuin_ai_adapter/protocol/openai.py     (Step 3)
  src/atuin_ai_adapter/sse.py                 (Step 4)
  src/atuin_ai_adapter/translator.py          (Step 5)
  src/atuin_ai_adapter/vllm_client.py         (Step 6)
  src/atuin_ai_adapter/service.py             (Step 7)
  src/atuin_ai_adapter/app.py                 (Step 8)
  tests/__init__.py                           (Step 0)
  tests/conftest.py                           (Step 10)
  tests/test_config.py                        (Step 1)
  tests/test_protocol_atuin.py                (Step 2)
  tests/test_protocol_openai.py               (Step 3)
  tests/test_sse.py                           (Step 4)
  tests/test_translator.py                    (Step 5)
  tests/test_vllm_client.py                   (Step 6)
  tests/test_service.py                       (Step 7)
  tests/test_app.py                           (Step 8)
  tests/fixtures/valid_request_simple.json    (Step 10)
  tests/fixtures/valid_request_conversation.json  (Step 10)
  tests/fixtures/valid_request_with_tools.json    (Step 10)
  tests/fixtures/vllm_stream_simple.txt           (Step 10)
```

Total: 1 modified file, 21 new files.
