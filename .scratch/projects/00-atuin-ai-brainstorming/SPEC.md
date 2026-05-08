# SPEC.md

# Atuin AI Adapter — v1 Technical Specification

**Status:** Draft
**Date:** 2026-05-08
**Scope:** v1 text-only streaming bridge
**Basis:** `ATUIN_AI_OVERVIEW.md`, `CONCEPT_REVIEW.md`

---

## 1. Purpose

Build a Python server that impersonates an Atuin AI backend so that the Atuin CLI can use a local vLLM (or any OpenAI-compatible) inference server for AI-assisted shell usage — without patching Atuin.

```text
Atuin CLI ──POST /api/cli/chat──▶ Adapter ──POST /v1/chat/completions──▶ vLLM
Atuin CLI ◀──SSE (text/done)──── Adapter ◀──SSE (OpenAI chunks)──────── vLLM
```

---

## 2. v1 Scope

### In scope

- Accept Atuin `POST /api/cli/chat` requests.
- Validate a local bearer token.
- Translate Atuin messages + context into OpenAI chat-completions format.
- Stream the upstream response back as Atuin-compatible SSE.
- Handle concurrent requests from multiple terminals.
- Expose configurable model, generation parameters, and system prompt.
- Health check endpoints.

### Explicitly out of scope (deferred to later phases)

- `tool_call` / `tool_result` passthrough or execution.
- Capability-aware model prompting beyond the system prompt.
- Server-side history search.
- Server-side safety classification.
- Server-side session persistence or conversation state.
- CORS / browser access.
- Full parity with Atuin Hub behavior.

---

## 3. Atuin-Facing Protocol Contract

This section defines the **stable external boundary** the adapter must satisfy.

### 3.1 Endpoint

```http
POST /api/cli/chat
```

### 3.2 Request headers

| Header | Value | Required |
|--------|-------|----------|
| `Authorization` | `Bearer <token>` | Yes |
| `Content-Type` | `application/json` | Yes |
| `Accept` | `text/event-stream` | Yes |

The adapter must reject requests with a missing or incorrect bearer token (see §7).

### 3.3 Request body

```jsonc
{
  "messages": [
    // Array of conversation messages (see §3.4)
  ],
  "context": {
    "os": "linux",             // optional
    "shell": "zsh",            // optional
    "distro": "arch",          // optional
    "pwd": "/home/user/proj",  // optional
    "last_command": "git log"  // optional
  },
  "config": {
    "capabilities": ["client_invocations"],  // optional
    "user_contexts": [],                     // optional
    "skills": [],                            // optional
    "skills_overflow": ""                    // optional
  },
  "invocation_id": "uuid-string",  // present on every request
  "session_id": "uuid-string"      // absent on first turn, present on continuations
}
```

**Validation policy:** Use Pydantic models with `model_config = ConfigDict(extra="ignore")`. Required fields: `messages`, `invocation_id`. All others are optional. Log a `WARNING` for any unrecognized top-level keys to aid debugging without breaking forward compatibility.

### 3.4 Message format

Atuin messages follow an Anthropic-like block structure.

#### User messages

```jsonc
{
  "role": "user",
  "content": "how do I find large files?"
}
```

Content may be a plain string or a list of content blocks.

#### Assistant messages (text)

```jsonc
{
  "role": "assistant",
  "content": "You can use the find command..."
}
```

Content may be a plain string or a list of structured blocks.

#### Assistant messages (tool use) — v1 fallback handling

```jsonc
{
  "role": "assistant",
  "content": [
    { "type": "text", "text": "Let me check..." },
    {
      "type": "tool_use",
      "id": "tool-id",
      "name": "execute_shell_command",
      "input": { "command": "du -sh *" }
    }
  ]
}
```

#### Tool result messages — v1 fallback handling

```jsonc
{
  "role": "user",
  "content": [
    {
      "type": "tool_result",
      "tool_use_id": "tool-id",
      "content": "output text...",
      "is_error": false
    }
  ]
}
```

**v1 handling:** Tool-related blocks are flattened to human-readable text (see §5.2).

### 3.5 Response: SSE stream

The response must use `Content-Type: text/event-stream` and emit events in this format:

#### Text chunk

```text
event: text
data: {"content":"partial text..."}

```

#### End of turn

```text
event: done
data: {"session_id":"uuid-string"}

```

#### Error

```text
event: error
data: {"message":"human-readable error description"}

```

Each SSE frame is terminated by a blank line (`\n\n`).

### 3.6 Session ID contract

| Situation | Adapter behavior |
|-----------|-----------------|
| Request contains `session_id` | Echo it back in the `done` event |
| Request omits `session_id` | Generate a new UUID v4, return it in the `done` event |

**Rationale:** Atuin's `driver.rs` persists the session ID from the `done` event into local AI session state. Echoing it preserves continuity. Generating one when absent allows first-turn conversations to establish a session.

### 3.7 Invocation ID handling

The `invocation_id` is treated as a **request trace ID**:

- Logged at `INFO` level when the request arrives.
- Included in all error log entries for that request.
- Not forwarded upstream or returned in the response.
- Not used for deduplication (Atuin does not retry requests).

---

## 4. Upstream (vLLM) Protocol

### 4.1 Endpoint

```http
POST {vllm_base_url}/v1/chat/completions
```

### 4.2 Request body

```jsonc
{
  "model": "configured-model-name",
  "messages": [
    {"role": "system", "content": "..."},  // constructed by adapter
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."},
    // ...
  ],
  "stream": true,
  "temperature": 0.7,   // from adapter config
  "max_tokens": 2048,   // from adapter config
  "top_p": 0.95         // from adapter config
}
```

No `tools` field in v1.

### 4.3 Response: OpenAI SSE stream

Each chunk is a `data:` line containing JSON:

```jsonc
{
  "choices": [
    {
      "delta": {
        "content": "partial text..."  // may be null or absent
      },
      "finish_reason": null           // "stop" on final chunk
    }
  ]
}
```

The stream terminates with `data: [DONE]`.

The adapter consumes `choices[0].delta.content` from each chunk and ignores everything else in v1.

---

## 5. Translation Rules

### 5.1 System prompt construction

The adapter prepends a system message derived from the Atuin request's `context` and `config` fields. This is the primary lever for output quality.

**Default template:**

```text
You are a terminal assistant. The user is working in a shell and may ask you
to suggest commands, explain errors, or help with system administration tasks.

Be concise. Prefer direct answers over lengthy explanations.
When suggesting a command, output it directly without markdown code fences
unless you are comparing multiple options.
If you are unsure, say so rather than guessing.

Environment:
- OS: {os}
- Shell: {shell}
- Distribution: {distro}
- Working directory: {cwd}
- Last command: {last_command}
```

**Construction rules:**

1. Start with the static preamble (the first paragraph).
2. Append an `Environment:` section. Omit any line whose value is absent/empty.
3. If `config.user_contexts` is non-empty, append each entry under a `User context:` heading.
4. The entire system prompt is sent as a single `{"role": "system", "content": "..."}` message — the first message in the upstream array.

**Configurability:** The static preamble is replaceable via the `system_prompt_template` config field (see §6). The environment section is always appended.

### 5.2 Message translation (Atuin → OpenAI)

Messages are translated sequentially, preserving order and role.

| Atuin input | OpenAI output |
|-------------|---------------|
| `role: "user"`, content is string | `role: "user"`, content is that string |
| `role: "user"`, content is list of blocks | Concatenate text from blocks (see below) |
| `role: "assistant"`, content is string | `role: "assistant"`, content is that string |
| `role: "assistant"`, content is list of blocks | Concatenate text from blocks (see below) |
| `role: "system"` (if Atuin ever sends one) | Pass through as `role: "system"` |

#### Block flattening rules (v1)

When content is a list of blocks, each block is rendered to text and the results are concatenated with `\n\n`:

| Block type | Rendering |
|------------|-----------|
| `{"type": "text", "text": "..."}` | Use `text` verbatim |
| `{"type": "tool_use", "id": "...", "name": "...", "input": {...}}` | `[Tool call: {name}({json of input})]` |
| `{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": bool}` | `[Tool result ({tool_use_id}): {content}]` or `[Tool error ({tool_use_id}): {content}]` |
| Unknown block type | `[Unknown block: {json dump}]` — log a WARNING |

This strategy preserves enough conversational meaning for the upstream model to reason about prior tool interactions without the adapter needing to implement tool execution.

### 5.3 Response translation (OpenAI stream → Atuin SSE)

For each upstream SSE chunk:

1. Parse the JSON payload.
2. Extract `choices[0].delta.content`.
3. If content is non-null and non-empty, emit:
   ```
   event: text
   data: {"content":"<escaped content>"}\n\n
   ```
4. If `choices[0].finish_reason == "stop"` or the stream emits `data: [DONE]`, emit:
   ```
   event: done
   data: {"session_id":"<session_id>"}\n\n
   ```
5. If an error occurs at any point during streaming, emit:
   ```
   event: error
   data: {"message":"<description>"}\n\n
   ```

The `data` payloads are JSON-serialized. Content strings within them must be properly JSON-escaped (newlines, quotes, backslashes).

---

## 6. Configuration

Configuration is loaded from environment variables with sensible defaults. A `.env` file is supported via Pydantic settings.

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ADAPTER_HOST` | str | `"127.0.0.1"` | Bind address |
| `ADAPTER_PORT` | int | `8787` | Bind port |
| `ADAPTER_API_TOKEN` | str | `"local-dev-token"` | Bearer token the adapter validates |
| `VLLM_BASE_URL` | str | `"http://127.0.0.1:8000"` | Upstream vLLM server URL |
| `VLLM_MODEL` | str | **required** | Model name to send in upstream requests |
| `VLLM_TIMEOUT` | float | `120.0` | Upstream request timeout in seconds |
| `GENERATION_TEMPERATURE` | float | `0.7` | Sampling temperature |
| `GENERATION_MAX_TOKENS` | int | `2048` | Maximum tokens to generate |
| `GENERATION_TOP_P` | float | `0.95` | Nucleus sampling threshold |
| `SYSTEM_PROMPT_TEMPLATE` | str | *(see §5.1 default)* | Replaceable static preamble for the system prompt |
| `LOG_LEVEL` | str | `"INFO"` | Python logging level |

**Implementation:** Use `pydantic-settings` with `env_prefix=""` (no prefix) and `env_file=".env"`. Expose as a singleton `Settings` instance created at app startup.

**`VLLM_MODEL` is the only required variable** with no default. The adapter must fail to start if it is unset.

---

## 7. Authentication

The adapter implements **simple local bearer-token validation**.

### Flow

1. Extract the `Authorization` header.
2. Verify it matches `Bearer {ADAPTER_API_TOKEN}`.
3. If missing or wrong, return HTTP `401 Unauthorized` with JSON body `{"detail": "Invalid or missing API token"}`. Do **not** open an SSE stream.

### Security notes

- The token is not a cryptographic secret — it is a gate against accidental connections from other local processes.
- The default bind address is `127.0.0.1` (localhost only). Binding to `0.0.0.0` is the user's choice and not recommended without a stronger auth mechanism.
- The token should not appear in log output.

---

## 8. Error Handling Policy

Every failure mode must result in either an HTTP error response (before streaming starts) or an SSE `error` event (after streaming starts). The Atuin TUI must never hang waiting for data that will never arrive.

### 8.1 Pre-stream errors

These occur before the SSE stream begins. Return standard HTTP responses.

| Condition | HTTP status | Response body |
|-----------|-------------|---------------|
| Missing/invalid auth token | 401 | `{"detail": "Invalid or missing API token"}` |
| Malformed request body (JSON parse failure) | 422 | FastAPI default validation error |
| Missing required fields (`messages`, `invocation_id`) | 422 | FastAPI/Pydantic validation error |

### 8.2 In-stream errors

These occur after the SSE stream has started (HTTP 200 already sent). Emit SSE events.

| Condition | SSE event | Behavior |
|-----------|-----------|----------|
| vLLM unreachable (connection refused/timeout) | `error` with message: `"Cannot reach upstream model server at {url}"` | Emit error, then emit `done` |
| vLLM returns HTTP 4xx/5xx | `error` with message: `"Upstream model server returned {status}: {body snippet}"` | Emit error, then emit `done` |
| vLLM stream terminates unexpectedly mid-response | `error` with message: `"Upstream stream terminated unexpectedly"` | Emit error, then emit `done` |
| Upstream chunk parse failure | `error` with message: `"Failed to parse upstream response"` | Emit error, then emit `done` |
| Adapter internal exception | `error` with message: `"Internal adapter error"` | Emit error, then emit `done` |

**Every error path emits a `done` event after the `error` event.** This ensures the Atuin client's stream reader terminates cleanly.

### 8.3 Logging

All errors are logged server-side at `ERROR` level with:
- `invocation_id` (if available)
- Error class and message
- Upstream URL (for connection errors)
- Upstream status code (for HTTP errors)

---

## 9. Module Design

### 9.1 Package layout

```text
src/atuin_ai_adapter/
    __init__.py
    app.py              # FastAPI application, routes, auth middleware
    config.py           # Settings model (pydantic-settings)
    service.py          # Bridge orchestration: translate → call upstream → emit SSE
    translator.py       # Atuin ↔ OpenAI message translation
    vllm_client.py      # Async httpx streaming client for vLLM
    sse.py              # SSE frame formatting utilities
    protocol/
        __init__.py
        atuin.py        # Pydantic models for Atuin request/response shapes
        openai.py       # Pydantic models for OpenAI request/response shapes
```

### 9.2 Module responsibilities

#### `config.py`

- Define `Settings` class using `pydantic-settings`.
- Validate `VLLM_MODEL` is set.
- Provide a module-level `get_settings()` accessor (cached).

#### `protocol/atuin.py`

Pydantic models for the Atuin side of the contract.

```python
class AtuinContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    os: str | None = None
    shell: str | None = None
    distro: str | None = None
    pwd: str | None = None
    last_command: str | None = None

class AtuinConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")
    capabilities: list[str] = []
    user_contexts: list[str] = []
    skills: list[Any] = []
    skills_overflow: str | None = None

class AtuinChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    messages: list[dict[str, Any]]       # loosely typed — translator handles structure
    context: AtuinContext | None = None
    config: AtuinConfig | None = None
    invocation_id: str
    session_id: str | None = None

class AtuinTextEvent(BaseModel):
    content: str

class AtuinDoneEvent(BaseModel):
    session_id: str

class AtuinErrorEvent(BaseModel):
    message: str
```

#### `protocol/openai.py`

Pydantic models for the upstream OpenAI-compatible side.

```python
class OpenAIChatMessage(BaseModel):
    role: str
    content: str

class OpenAIChatRequest(BaseModel):
    model: str
    messages: list[OpenAIChatMessage]
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
```

Streaming response chunks are parsed with lightweight dict access rather than full Pydantic validation, for performance.

#### `translator.py`

Two public functions:

```python
def build_openai_messages(
    request: AtuinChatRequest,
    system_prompt_template: str,
) -> list[OpenAIChatMessage]:
    """Translate Atuin messages + context into OpenAI message array."""

def flatten_content_blocks(content: str | list[dict]) -> str:
    """Flatten Atuin structured content blocks into a plain string."""
```

`build_openai_messages`:
1. Construct the system message per §5.1.
2. Iterate over `request.messages`.
3. For each message, flatten content via `flatten_content_blocks`.
4. Return the full `[system, ...translated]` list.

`flatten_content_blocks`:
- If content is a string, return it.
- If content is a list, apply block flattening rules from §5.2.
- If content is anything else, `str()` it and log a warning.

#### `sse.py`

```python
def format_sse(event: str, data: str) -> str:
    """Format a single SSE frame: 'event: {event}\ndata: {data}\n\n'"""
```

Also convenience functions:

```python
def text_event(content: str) -> str:
def done_event(session_id: str) -> str:
def error_event(message: str) -> str:
```

Each returns a fully formatted SSE frame string (bytes-ready).

#### `vllm_client.py`

```python
class VllmClient:
    def __init__(self, base_url: str, timeout: float):
        # Create httpx.AsyncClient with connection pooling

    async def stream_chat(
        self,
        request: OpenAIChatRequest,
    ) -> AsyncIterator[str | None]:
        """
        Send a streaming chat completion request.
        Yield text deltas (str) or None for non-text chunks.
        Raise on connection/HTTP errors.
        """

    async def health_check(self) -> bool:
        """GET /v1/models — return True if reachable."""

    async def close(self):
        """Close the underlying httpx client."""
```

The `stream_chat` method:
1. POST to `/v1/chat/completions` with `stream=True`.
2. Iterate over SSE lines from the response.
3. Parse each `data:` line as JSON.
4. Skip `data: [DONE]`.
5. Extract `choices[0].delta.content`, yield it.
6. On HTTP error, raise a descriptive exception.

The `AsyncClient` is created once and shared across requests (connection pooling).

#### `service.py`

```python
async def handle_chat(
    request: AtuinChatRequest,
    vllm_client: VllmClient,
    settings: Settings,
) -> AsyncIterator[str]:
    """
    Full bridge pipeline:
    1. Determine session_id (echo or generate).
    2. Translate request.
    3. Stream upstream.
    4. Yield Atuin SSE frames.
    5. Yield done event.
    6. On any error, yield error + done events.
    """
```

This is an async generator that yields SSE frame strings. It wraps all upstream interaction in a try/except to guarantee the error handling policy from §8.

#### `app.py`

```python
app = FastAPI(title="Atuin AI Adapter")

@app.post("/api/cli/chat")
async def chat(request: Request, ...):
    # 1. Validate auth token.
    # 2. Parse body into AtuinChatRequest.
    # 3. Log invocation_id at INFO level.
    # 4. Return StreamingResponse(
    #        service.handle_chat(...),
    #        media_type="text/event-stream",
    #    )

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/health/ready")
async def health_ready(vllm_client: VllmClient = Depends(...)):
    # Probe vLLM, return 200 or 503.
```

**Lifespan management:** Use FastAPI's lifespan context manager to create/close the `VllmClient` and `Settings` at startup/shutdown.

---

## 10. Concurrency Model

- **One async request handler per Atuin call.** No thread pool, no global lock.
- **One upstream HTTP stream per request.** Each request gets its own streaming connection to vLLM.
- **Shared `httpx.AsyncClient`** across all requests for TCP connection pooling.
- **Backpressure** is handled naturally: the async generator in `service.py` only reads the next upstream chunk when the downstream SSE write completes.
- **Cancellation:** If the Atuin client disconnects, Starlette's `StreamingResponse` will raise `asyncio.CancelledError` in the generator, which should close the upstream httpx stream.

---

## 11. Testing Strategy

### 11.1 Unit tests

| Module | Test focus |
|--------|-----------|
| `translator.py` | Message conversion: plain text, structured blocks, tool blocks, mixed content, empty messages, unknown block types |
| `sse.py` | Frame formatting: correct `event:`/`data:` lines, JSON escaping, newline handling |
| `protocol/atuin.py` | Pydantic parsing: valid requests, missing optional fields, extra fields ignored, missing required fields rejected |
| `config.py` | Settings loading: defaults, env overrides, missing required `VLLM_MODEL` raises |

### 11.2 Integration tests

Use `httpx.AsyncClient` with FastAPI's `TestClient` (ASGI transport) to test the full request/response cycle without a real vLLM server.

- **Mock vLLM:** Use `respx` or `pytest-httpx` to mock upstream HTTP responses.
- **Happy path:** Send a valid Atuin request, mock a vLLM streaming response, assert correct SSE output (text events + done event).
- **Multi-turn:** Send a request with conversation history including assistant messages, verify correct upstream translation.
- **Tool block fallback:** Send messages containing tool_use/tool_result blocks, verify they are flattened to text.
- **Error: vLLM unreachable:** Mock a connection error, assert SSE error + done events.
- **Error: vLLM 500:** Mock a 500 response, assert SSE error + done events.
- **Error: mid-stream failure:** Mock a stream that dies after 2 chunks, assert partial text + error + done events.
- **Auth failure:** Send request with wrong token, assert HTTP 401.
- **Session ID echo:** Send request with session_id, verify it appears in done event.
- **Session ID generation:** Send request without session_id, verify a UUID appears in done event.

### 11.3 Smoke test

A standalone script (`tests/smoke.py` or similar) that:

1. Starts the adapter against a configurable upstream.
2. Sends a curl-equivalent HTTP request with a valid Atuin-shaped body.
3. Reads the SSE stream.
4. Prints each event.
5. Exits 0 if it sees `text` and `done` events, exits 1 otherwise.

This can also be used for manual verification during development.

### 11.4 Fixture strategy

Create a `tests/fixtures/` directory containing:

- `valid_request_simple.json` — single user message, minimal context.
- `valid_request_conversation.json` — multi-turn with assistant messages.
- `valid_request_with_tools.json` — messages containing tool_use/tool_result blocks.
- `vllm_stream_simple.txt` — raw SSE lines from a vLLM streaming response.
- `vllm_stream_error.txt` — truncated/malformed vLLM stream.

Tests load these fixtures rather than embedding large JSON in test files.

**Wire capture:** Before or during early development, capture at least one real Atuin request (e.g., via mitmproxy or `RUST_LOG=debug`) and add it as a fixture. This validates the inferred protocol against reality.

---

## 12. Corresponding Atuin Configuration

The user configures Atuin to point at the adapter:

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

Key points:
- `endpoint` is the adapter's base URL. Atuin appends `/api/cli/chat`.
- `api_token` must match `ADAPTER_API_TOKEN`.
- Capabilities are disabled because v1 does not handle tools. Atuin will not send tool-related context if capabilities are off.

---

## 13. Project Packaging

### 13.1 pyproject.toml updates needed

The current `pyproject.toml` is a template placeholder. It must be updated:

- **Package name:** `atuin-ai-adapter`
- **Build target:** `packages = ["src/atuin_ai_adapter"]`
- **Dependencies:**
  - `pydantic>=2.12` (already present)
  - `pydantic-settings>=2.0`
  - `fastapi>=0.115`
  - `uvicorn[standard]>=0.30`
  - `httpx>=0.28`
- **Dev dependencies:**
  - `pytest>=7.0`
  - `pytest-asyncio>=0.24`
  - `pytest-cov>=4.1`
  - `pytest-httpx>=0.35` (or `respx>=0.22`)
  - `mypy>=1.10`
  - `ruff>=0.5.0`
- **Tool config:**
  - mypy `packages` → `["src/atuin_ai_adapter"]`
  - pytest `--cov=atuin_ai_adapter`
  - ruff `src = ["src"]`
- **Entry point (optional):**
  ```toml
  [project.scripts]
  atuin-ai-adapter = "atuin_ai_adapter.app:main"
  ```
  where `main()` calls `uvicorn.run(...)` with settings from config.

### 13.2 Environment

Per `AGENTS.md` and `REPO_RULES.md`:

- All commands run via `devenv shell -- ...`.
- Dependencies synced via `devenv shell -- uv sync --extra dev`.
- Never use `uv pip install`.

---

## 14. Health Checks

### `GET /health` — Liveness

Always returns HTTP 200:

```json
{"status": "ok"}
```

### `GET /health/ready` — Readiness

Probes vLLM by calling `GET {vllm_base_url}/v1/models`.

- If reachable: HTTP 200 `{"status": "ready", "upstream": "reachable"}`
- If unreachable: HTTP 503 `{"status": "not_ready", "upstream": "unreachable", "detail": "..."}`

---

## 15. Logging

Use Python's `logging` module. Configure at startup based on `LOG_LEVEL`.

### Log format

```
%(asctime)s %(levelname)s [%(name)s] %(message)s
```

### What to log

| Level | Content |
|-------|---------|
| `INFO` | Request received (invocation_id, session_id presence, message count) |
| `INFO` | Upstream request sent (model, message count) |
| `INFO` | Stream completed (invocation_id) |
| `WARNING` | Unknown message block type encountered |
| `WARNING` | Unknown top-level request field |
| `ERROR` | All error conditions from §8 |
| `DEBUG` | Full translated message array (for development) |
| `DEBUG` | Raw upstream chunk data (for development) |

### What NOT to log

- The bearer token value.
- Full user message content at INFO level (privacy).
- Full upstream response content at INFO level (volume).

---

## 16. Future Phase Notes

These are not part of v1 but are noted here to inform v1 design decisions — specifically, where to avoid painting into a corner.

### Phase 2: Tool-aware translation

- Define OpenAI tool schemas mirroring Atuin's client-side tools (read_file, write_file, edit_file, execute_shell_command, atuin_history).
- Add `tools` field to the upstream request when capabilities are enabled.
- Translate model `tool_calls` in upstream response into Atuin `event: tool_call` SSE.
- Accept Atuin follow-up requests containing `tool_result` blocks and pass them upstream as tool results.
- This is the **hardest phase** due to multi-turn streaming orchestration.
- **v1 design implication:** `service.py` should be structured so the stream loop can be extended with tool-call handling without a rewrite. Keep it as a simple async generator now; it will need branching logic later.

### Phase 3: Better prompt/context fidelity

- Preserve structured context more precisely.
- Map `skills` and `user_contexts` into the system prompt.
- Distinguish injected context from user-visible system prompt.

### Phase 4: Server-side session state

- Optional adapter-side conversation cache for context-window-aware truncation.
- Session resumption across adapter restarts (e.g., SQLite).

### Phase 5: Alternate backend drivers

- Atuin's message format is Anthropic-like. An Anthropic-compatible backend driver could be added alongside the OpenAI one.
- The Atuin-facing side should remain unchanged.

---

## 17. Success Criteria

v1 is complete when all of the following are true:

1. `atuin` with `[ai].endpoint` pointed at the adapter opens AI mode normally via `?`.
2. Prompts stream text back incrementally into the Atuin TUI.
3. Follow-up conversation turns work (multi-turn).
4. Multiple terminals can use the adapter concurrently without blocking.
5. Auth token validation rejects unauthorized requests.
6. Upstream failures produce visible error messages in Atuin rather than hangs.
7. All unit and integration tests pass.
8. No Atuin source patches are required.

---

## Appendix A: SSE Framing Reference

```text
event: <event-type>\n
data: <json-payload>\n
\n
```

- Each field is on its own line, terminated by `\n`.
- The frame is terminated by a blank line (an additional `\n`).
- The `data` value must be a single line (no embedded newlines). JSON serialization handles this naturally.
- Multiple `data:` lines are technically valid SSE but Atuin's parser expects a single `data:` line per event.

---

## Appendix B: Example End-to-End Flow

### 1. User types `?` in an empty prompt

Atuin shell hook triggers `atuin ai inline --hook`.

### 2. User types "find files larger than 100MB"

Atuin builds a `ChatRequest` and sends:

```http
POST http://127.0.0.1:8787/api/cli/chat
Authorization: Bearer local-dev-token
Content-Type: application/json
Accept: text/event-stream

{
  "messages": [
    {"role": "user", "content": "find files larger than 100MB"}
  ],
  "context": {"os": "linux", "shell": "zsh", "pwd": "/home/user"},
  "config": {"capabilities": ["client_invocations"]},
  "invocation_id": "a1b2c3d4"
}
```

### 3. Adapter receives and translates

Adapter builds upstream request:

```json
{
  "model": "Qwen/Qwen3-32B",
  "messages": [
    {"role": "system", "content": "You are a terminal assistant....\n\nEnvironment:\n- OS: linux\n- Shell: zsh\n- Working directory: /home/user"},
    {"role": "user", "content": "find files larger than 100MB"}
  ],
  "stream": true,
  "temperature": 0.7,
  "max_tokens": 2048,
  "top_p": 0.95
}
```

### 4. vLLM streams response

```text
data: {"choices":[{"delta":{"content":"find"},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":" / -size"},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":" +100M"},"finish_reason":null}]}
data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}
data: [DONE]
```

### 5. Adapter translates to Atuin SSE

```text
event: text
data: {"content":"find"}

event: text
data: {"content":" / -size"}

event: text
data: {"content":" +100M"}

event: done
data: {"session_id":"e5f6g7h8-generated-uuid"}

```

### 6. Atuin TUI renders

User sees `find / -size +100M` appear incrementally and can choose to execute, edit, or continue the conversation.
