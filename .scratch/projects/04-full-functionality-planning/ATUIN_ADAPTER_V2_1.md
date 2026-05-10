# ATUIN_ADAPTER_V2_1.md

## Atuin AI Adapter V2.1 — Full Concept

**Date:** 2026-05-09

---

## 1. What this is

A complete design for refactoring `atuin-ai-adapter` from a text-only streaming bridge into a full Atuin AI backend that supports tool calling, command suggestions, continuation loops, skills, and capability-driven behavior — all without patching Atuin.

This document supersedes the original `ATUIN_ADAPTER_V2.md` with a leaner architecture informed by direct investigation of the Atuin source code.

---

## 2. Design principles

1. **Lean first.** No abstraction until it earns its place. One backend means no backend abstraction framework — just a clean interface.
2. **Atuin is the contract.** The adapter exists to speak Atuin's SSE protocol. Everything else is implementation detail.
3. **Stateless per-request.** Atuin sends full conversation history on every request, including continuations. The adapter needs no persistence.
4. **Fail fast, fail cleanly.** Errors produce `error` + `done` SSE events. No retries, no silent drops.
5. **Backward compatible config.** `enable_tools = false` makes V2 behave like V1.

---

## 3. Verified Atuin protocol contract

All details below were confirmed against the Atuin source code (stream.rs, fsm/mod.rs, driver.rs, tui/state.rs, tools/mod.rs, context.rs, event_serde.rs).

### 3.1 Request

```http
POST /api/cli/chat
Authorization: Bearer <token>
Accept: text/event-stream
Content-Type: application/json
```

```json
{
  "messages": [
    {"role": "user", "content": "how do I find large files?"},
    {"role": "assistant", "content": [
      {"type": "text", "text": "Let me check..."},
      {"type": "tool_use", "id": "tc_001", "name": "execute_shell_command",
       "input": {"command": "du -sh * | sort -rh | head -5"}}
    ]},
    {"role": "user", "content": [
      {"type": "tool_result", "tool_use_id": "tc_001",
       "content": "1.2G\tnode_modules\n500M\t.git", "is_error": false}
    ]}
  ],
  "context": {
    "os": "linux",
    "shell": "zsh",
    "distro": "arch",
    "pwd": "/home/user/projects",
    "last_command": "ls -la"
  },
  "config": {
    "capabilities": [
      "client_invocations",
      "client_v1_load_skill",
      "client_v1_atuin_history",
      "client_v1_read_file",
      "client_v1_edit_file",
      "client_v1_write_file",
      "client_v1_execute_shell_command"
    ],
    "user_contexts": ["Always use sudo for system commands"],
    "skills": [
      {"name": "release", "description": "Orchestrate a multi-step release..."}
    ],
    "skills_overflow": null
  },
  "invocation_id": "01926a3b-...",
  "session_id": "01926a3a-..."
}
```

**Required:** `messages`, `invocation_id`.
**Optional:** `context`, `config`, `session_id`.

On continuation requests: same `invocation_id` and `session_id`, updated `messages` including tool_use + tool_result blocks.

### 3.2 SSE response events

Six event types. The adapter must emit all of them.

#### text
```
event: text
data: {"content":"chunk of text"}
```

#### tool_call
```
event: tool_call
data: {"id":"call_abc123","name":"read_file","input":{"file_path":"/etc/hosts"}}
```

Fields: `id` (string, server-assigned), `name` (string), `input` (object).

#### tool_result (adapter-executed remote tools only)
```
event: tool_result
data: {"tool_use_id":"call_abc123","content":"...","is_error":false,"remote":true,"content_length":1234}
```

For client-side tools, Atuin handles execution locally. The adapter only emits `tool_result` SSE events for tools it executes itself (remote tools).

#### status
```
event: status
data: {"state":"Thinking..."}
```

#### done
```
event: done
data: {"session_id":"01926a3a-..."}
```

#### error
```
event: error
data: {"message":"Failed to connect to model server"}
```

### 3.3 Continuation protocol

This is the most important behavior V2 adds.

```
1. Adapter receives request with messages
2. Adapter streams response: text chunks + tool_call events
3. Adapter emits done
4. Atuin client executes tools locally (permissions, file I/O, shell)
5. Atuin sends NEW request with tool results appended to messages
6. Adapter streams continuation response
7. Repeat until response has no tool calls
```

The adapter does not maintain state between requests. Each request carries the full conversation history. The adapter just needs to translate the full history correctly each time.

### 3.4 suggest_command

The FSM treats `suggest_command` as a stream terminal — equivalent to `StreamDone`. It is emitted as a `tool_call` SSE event. The Atuin TUI reads `input.command` and offers insert/execute actions.

The adapter emits it like any other `tool_call`. No special handling needed on the adapter side.

### 3.5 Skills

Only `{name, description}` summaries arrive in the request. The model can call `load_skill` to get the full content. The Atuin client handles skill loading locally and sends the result back in a continuation.

### 3.6 Remote tool results

When the adapter executes a tool itself (e.g., future `web_search`), it emits both `tool_call` and `tool_result` SSE events. The `tool_result` has `remote: true`. Atuin's FSM treats these as informational — it displays them but doesn't try to execute them.

---

## 4. Architecture

```
Atuin client
    │
    ▼
app.py ─── POST /api/cli/chat
    │       auth, parse request
    ▼
orchestrator.py
    │  1. tools.py: build tool registry from capabilities
    │  2. prompt.py: compose system prompt
    │  3. translator.py: Atuin messages → OpenAI messages
    │  4. backend.py: stream chat completion with tools
    │  5. protocol.py: emit Atuin SSE events
    ▼
backend.py ─── POST /v1/chat/completions
    │           stream=true, tools=[...]
    │           accumulate tool-call fragments
    │           yield BackendEvent
    ▼
vLLM / OpenAI-compatible server
```

Eight modules, flat layout, no sub-packages beyond what already exists:

```
src/atuin_ai_adapter/
    __init__.py
    app.py            # FastAPI app, routes, auth, lifespan
    config.py         # Settings (extended)
    protocol.py       # Atuin request/response models + SSE builders
    tools.py          # Tool registry, schemas, capability mapping
    orchestrator.py   # Core bridge logic
    backend.py        # OpenAI backend driver + tool-call accumulator
    translator.py     # Atuin ↔ OpenAI message translation
    prompt.py         # System prompt composition
```

---

## 5. Module specifications

### 5.1 protocol.py

Consolidates `protocol/atuin.py`, `protocol/openai.py`, and `sse.py` into one module. Contains all Atuin-facing models and SSE frame builders.

#### Models

```python
class AtuinContext(BaseModel):
    """model_config = ConfigDict(extra="ignore")"""
    os: str | None = None
    shell: str | None = None
    distro: str | None = None
    pwd: str | None = None
    last_command: str | None = None

class AtuinSkillSummary(BaseModel):
    name: str
    description: str

class AtuinConfig(BaseModel):
    """model_config = ConfigDict(extra="ignore")"""
    capabilities: list[str] = []
    user_contexts: list[str] = []
    skills: list[AtuinSkillSummary] = []
    skills_overflow: str | None = None

class AtuinChatRequest(BaseModel):
    """model_config = ConfigDict(extra="ignore")"""
    messages: list[dict[str, Any]]
    context: AtuinContext | None = None
    config: AtuinConfig | None = None
    invocation_id: str
    session_id: str | None = None
```

#### SSE event models

```python
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
```

#### SSE frame builders

```python
def format_sse(event: str, data: str) -> str: ...
def text_event(content: str) -> str: ...
def tool_call_event(id: str, name: str, input: dict[str, Any]) -> str: ...
def tool_result_event(tool_use_id: str, content: str, is_error: bool = False,
                      remote: bool = False, content_length: int | None = None) -> str: ...
def status_event(state: str) -> str: ...
def done_event(session_id: str) -> str: ...
def error_event(message: str) -> str: ...
```

### 5.2 tools.py

Maps Atuin capabilities to tool definitions. Classifies tools by execution location.

#### Types

```python
class ToolExecution(Enum):
    CLIENT = "client"       # Atuin executes locally
    PSEUDO = "pseudo"       # UI signal, no execution
    ADAPTER = "adapter"     # Adapter executes (future)

class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    execution: ToolExecution
```

#### Capability → tool mapping

```python
CAPABILITY_TOOL_MAP: dict[str, list[str]] = {
    "client_invocations": ["suggest_command"],
    "client_v1_load_skill": ["load_skill"],
    "client_v1_atuin_history": ["atuin_history"],
    "client_v1_read_file": ["read_file"],
    "client_v1_edit_file": ["edit_file"],
    "client_v1_write_file": ["write_file"],
    "client_v1_execute_shell_command": ["execute_shell_command"],
}
```

#### Core function

```python
def build_tool_registry(capabilities: list[str]) -> list[ToolDefinition]:
    """Return tool definitions for the given capability list."""
```

#### Tool schemas

All schemas match the Atuin source (tools/mod.rs):

**suggest_command** (pseudo-tool):
```json
{
  "name": "suggest_command",
  "description": "Suggest a shell command for the user to run or edit. Use this when the best answer is a command.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {"type": ["string", "null"], "description": "The shell command to suggest"},
      "description": {"type": ["string", "null"], "description": "Brief description of what the command does"},
      "confidence": {"type": ["string", "null"], "enum": ["low", "medium", "high", null]},
      "danger": {"type": ["string", "null"], "enum": ["low", "medium", "high", null]},
      "warning": {"type": ["string", "null"], "description": "Warning message for dangerous commands"}
    },
    "required": ["command"]
  }
}
```

**read_file** (client):
```json
{
  "name": "read_file",
  "description": "Read the contents of a file.",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": {"type": "string"},
      "offset": {"type": "integer", "default": 0},
      "limit": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000}
    },
    "required": ["file_path"]
  }
}
```

**edit_file** (client):
```json
{
  "name": "edit_file",
  "description": "Edit a file by replacing a specific string with a new string.",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": {"type": "string"},
      "old_string": {"type": "string"},
      "new_string": {"type": "string"},
      "replace_all": {"type": "boolean", "default": false}
    },
    "required": ["file_path", "old_string", "new_string"]
  }
}
```

**write_file** (client):
```json
{
  "name": "write_file",
  "description": "Write content to a file. Creates the file if it doesn't exist.",
  "parameters": {
    "type": "object",
    "properties": {
      "file_path": {"type": "string"},
      "content": {"type": "string"},
      "overwrite": {"type": "boolean", "default": false}
    },
    "required": ["file_path", "content"]
  }
}
```

**execute_shell_command** (client):
```json
{
  "name": "execute_shell_command",
  "description": "Execute a shell command and return the output.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {"type": "string"},
      "shell": {"type": "string", "default": "bash"},
      "dir": {"type": ["string", "null"]},
      "timeout": {"type": "integer", "default": 30, "minimum": 1, "maximum": 600},
      "description": {"type": ["string", "null"]}
    },
    "required": ["command"]
  }
}
```

**atuin_history** (client):
```json
{
  "name": "atuin_history",
  "description": "Search the user's shell command history.",
  "parameters": {
    "type": "object",
    "properties": {
      "filter_modes": {
        "type": "array",
        "items": {"type": "string", "enum": ["global", "host", "session", "directory", "workspace"]}
      },
      "query": {"type": "string"},
      "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50}
    },
    "required": ["filter_modes", "query"]
  }
}
```

**load_skill** (client):
```json
{
  "name": "load_skill",
  "description": "Load the full content of a skill by name.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {"type": "string"}
    },
    "required": ["name"]
  }
}
```

#### OpenAI tool format conversion

```python
def to_openai_tools(registry: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Convert tool definitions to OpenAI function-calling format."""
    # Returns [{"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}]
```

### 5.3 backend.py

Replaces `vllm_client.py`. Handles OpenAI-compatible streaming with tool-call accumulation.

#### Event types

```python
class BackendTextDelta:
    content: str

class BackendToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

class BackendDone:
    pass

class BackendError:
    message: str

BackendEvent = BackendTextDelta | BackendToolCall | BackendDone | BackendError
```

These are the only types the orchestrator sees from the backend. The backend driver owns all OpenAI-specific parsing.

#### Tool-call accumulation

The driver internally maintains a `dict[int, ToolCallAccumulator]` keyed by tool-call index. Each accumulator collects:
- `id`: from the first delta for that index
- `name`: from the first delta (in `function.name`)
- `arguments_buffer`: concatenated argument string fragments

When the stream ends or `finish_reason == "tool_calls"`, each completed accumulator is validated (JSON-parse arguments) and yielded as a `BackendToolCall` event.

If argument parsing fails, a `BackendError` is yielded instead.

#### Client interface

```python
class BackendClient:
    def __init__(self, base_url: str, timeout: float, api_key: str | None = None): ...

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> AsyncIterator[BackendEvent]:
        """Stream a chat completion request, yielding BackendEvents."""

    async def health_check(self) -> bool: ...
    async def close(self) -> None: ...
```

When `tools` is None or empty, the request is sent without tool schemas (text-only mode).

#### API key forwarding

New config field `vllm_api_key: str | None = None`. When set, sent as `Authorization: Bearer <key>` to the backend. This enables remote OpenAI-compatible APIs (OpenRouter, OpenAI, etc.).

### 5.4 translator.py

Translates Atuin-format messages to OpenAI-format messages and back.

#### Key behavioral change from V1

V1 flattens all structured content to text. V2 preserves tool structure when `enable_tools` is true.

#### Atuin → OpenAI message translation

Atuin messages use Anthropic-style content blocks. OpenAI uses a different format for tool calls and results.

**Atuin assistant message with tool_use:**
```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "Let me read that file."},
    {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "foo.rs"}}
  ]
}
```

**Translated to OpenAI format:**
```json
{
  "role": "assistant",
  "content": "Let me read that file.",
  "tool_calls": [
    {"id": "tc_001", "type": "function", "function": {"name": "read_file", "arguments": "{\"file_path\": \"foo.rs\"}"}}
  ]
}
```

**Atuin user message with tool_result:**
```json
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "tc_001", "content": "file contents...", "is_error": false}
  ]
}
```

**Translated to OpenAI format:**
```json
{
  "role": "tool",
  "tool_call_id": "tc_001",
  "content": "file contents..."
}
```

Note: OpenAI uses `role: "tool"` for tool results, not `role: "user"`. Each tool result is a separate message.

#### Translation functions

```python
def translate_messages(
    messages: list[dict[str, Any]],
    *,
    flatten_tools: bool = False,
) -> list[dict[str, Any]]:
    """Translate Atuin-format messages to OpenAI-format messages.

    When flatten_tools is True, tool blocks are converted to text (v1 behavior).
    When False, tool_use becomes tool_calls and tool_result becomes role=tool messages.
    """
```

The `flatten_tools` parameter is driven by `config.enable_tools`.

#### Fallback flattening (v1 compatibility)

When `flatten_tools=True`, the v1 behavior is preserved exactly:
- `tool_use` → `[Tool call: name(json_input)]`
- `tool_result` → `[Tool result (id): content]`
- Everything becomes text content

### 5.5 prompt.py

Composes the system prompt from sections.

```python
def build_system_prompt(
    context: AtuinContext | None,
    config: AtuinConfig | None,
    tools: list[ToolDefinition],
    base_prompt: str,
) -> str:
    """Build the full system prompt from context, config, tools, and base template."""
```

#### Sections (in order)

1. **Base identity** — from `config.system_prompt_template`. Describes the assistant's role as a terminal helper.

2. **Environment context** — from `AtuinContext`:
   ```
   ## Environment
   - OS: linux
   - Shell: zsh
   - Distribution: arch
   - Working directory: /home/user/projects
   - Last command: ls -la
   ```
   Only includes non-None fields.

3. **Tool instructions** — dynamically generated from the active tool registry:
   ```
   ## Available tools
   You have the following tools available. Use them when appropriate:
   - suggest_command: Use this when the best response is a shell command...
   - read_file: Read file contents before editing...
   - execute_shell_command: Run commands when you need to inspect the system...
   ...

   ## Guidelines
   - When the user asks for a command, use suggest_command rather than just writing it in text.
   - Use read_file before edit_file to understand current file contents.
   - Prefer suggest_command over execute_shell_command when the user should review first.
   - For dangerous operations, set danger to "high" and include a warning.
   ```
   Only included when tools are enabled. Instructions are specific to which tools are available.

4. **Skill summaries** — from `config.skills`:
   ```
   ## Available skills
   The user has the following skills installed. Use load_skill to load the full content when relevant:
   - release: Orchestrate a multi-step release...
   - deploy: Deploy to production environment
   ```
   Only included when skills are present and `load_skill` is in the tool registry.

5. **User contexts** — from `config.user_contexts`:
   ```
   ## User preferences
   - Always use sudo for system commands
   ```

### 5.6 orchestrator.py

The core bridge. Replaces `service.py`.

```python
async def handle_chat(
    request: AtuinChatRequest,
    backend: BackendClient,
    settings: Settings,
) -> AsyncIterator[str]:
    """Handle an Atuin chat request, yielding SSE frames."""
```

#### Algorithm

```python
async def handle_chat(request, backend, settings):
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
        openai_messages = [
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

                case BackendToolCall(id=id, name=name, arguments=args):
                    yield tool_call_event(id, name, args)

                case BackendDone():
                    pass  # handled below

                case BackendError(message=msg):
                    yield error_event(msg)
                    yield done_event(session_id)
                    return

        # 6. Done
        yield done_event(session_id)

    except Exception as exc:
        logger.error("Adapter error: %s", exc, exc_info=True)
        msg = str(exc) if isinstance(exc, BackendConnectionError) else "Internal adapter error"
        yield error_event(msg)
        yield done_event(session_id)
```

This is intentionally simple. The orchestrator doesn't need to manage continuations — Atuin handles that by sending a new request. Each request is self-contained.

The orchestrator doesn't need to know which tools are client-side vs pseudo vs adapter-side for the core flow. It just emits `tool_call` events and Atuin/the adapter decides what to do. When adapter-side remote tools are added later, the orchestrator will gain a branch that executes them inline and emits `tool_result` events.

### 5.7 config.py

Extended with new fields:

```python
class Settings(BaseSettings):
    # Adapter server
    adapter_host: str = "127.0.0.1"
    adapter_port: int = 8787
    adapter_api_token: str = "local-dev-token"

    # Backend
    vllm_base_url: str = "http://127.0.0.1:8000"
    vllm_model: str  # required
    vllm_timeout: float = 120.0
    vllm_api_key: str | None = None  # NEW: for remote APIs

    # Generation
    generation_temperature: float = 0.7
    generation_max_tokens: int = 2048
    generation_top_p: float = 0.95

    # Tools
    enable_tools: bool = True  # NEW: false = v1 text-only behavior

    # Prompt
    system_prompt_template: str = DEFAULT_SYSTEM_PROMPT

    # Logging
    log_level: str = "INFO"
```

### 5.8 app.py

Minimal changes from v1:
- Lifespan creates `BackendClient` instead of `VllmClient`
- Passes `settings.vllm_api_key` to backend client
- Route handler calls `handle_chat` from `orchestrator` instead of `service`
- Health/readiness endpoints unchanged

---

## 6. Translation rules — complete specification

### 6.1 Simple text messages

Atuin:
```json
{"role": "user", "content": "how do I list files?"}
```

OpenAI (unchanged):
```json
{"role": "user", "content": "how do I list files?"}
```

### 6.2 Assistant text (simple string)

Atuin:
```json
{"role": "assistant", "content": "Use the ls command."}
```

OpenAI (unchanged):
```json
{"role": "assistant", "content": "Use the ls command."}
```

### 6.3 Assistant with tool_use blocks

Atuin:
```json
{
  "role": "assistant",
  "content": [
    {"type": "text", "text": "Let me read that."},
    {"type": "tool_use", "id": "tc_001", "name": "read_file", "input": {"file_path": "foo.rs"}},
    {"type": "tool_use", "id": "tc_002", "name": "read_file", "input": {"file_path": "bar.rs"}}
  ]
}
```

OpenAI:
```json
{
  "role": "assistant",
  "content": "Let me read that.",
  "tool_calls": [
    {"id": "tc_001", "type": "function", "function": {"name": "read_file", "arguments": "{\"file_path\": \"foo.rs\"}"}},
    {"id": "tc_002", "type": "function", "function": {"name": "read_file", "arguments": "{\"file_path\": \"bar.rs\"}"}}
  ]
}
```

If no text blocks exist alongside tool_use blocks, `content` is `null` or omitted.

### 6.4 User with tool_result blocks

Atuin sends tool results as user messages with structured content:
```json
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "tc_001", "content": "file contents...", "is_error": false},
    {"type": "tool_result", "tool_use_id": "tc_002", "content": "other file...", "is_error": false}
  ]
}
```

OpenAI uses separate `role: "tool"` messages, one per result:
```json
{"role": "tool", "tool_call_id": "tc_001", "content": "file contents..."}
{"role": "tool", "tool_call_id": "tc_002", "content": "other file..."}
```

One Atuin user message with N tool_result blocks becomes N OpenAI tool messages.

If `is_error` is true, the content is still passed as-is — the model sees the error output.

### 6.5 Mixed user messages (text + tool_result)

Atuin:
```json
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "tc_001", "content": "result..."},
    {"type": "text", "text": "Also, can you check this?"}
  ]
}
```

OpenAI: split into tool message(s) + user message:
```json
{"role": "tool", "tool_call_id": "tc_001", "content": "result..."}
{"role": "user", "content": "Also, can you check this?"}
```

### 6.6 Remote tool results in history

Atuin:
```json
{"type": "tool_result", "tool_use_id": "tc_003", "content": "search results...", "remote": true, "content_length": 5000}
```

Translated the same as any tool result — `remote` and `content_length` are Atuin metadata, not relevant to the OpenAI API. The content is forwarded as-is.

### 6.7 Unknown content block types

Any unrecognized block type is serialized to text with a marker:
```
[Unknown block (type=foo): {"key": "value"}]
```

Logged as a warning. This preserves forward compatibility.

---

## 7. Tool-call accumulation — detailed specification

This is the trickiest parsing in the adapter. It lives entirely in `backend.py`.

### 7.1 OpenAI streaming tool-call format

Tool call deltas arrive in `choices[0].delta.tool_calls`, which is an array of partial objects:

**First delta for tool call index 0:**
```json
{
  "choices": [{
    "delta": {
      "tool_calls": [{
        "index": 0,
        "id": "call_abc123",
        "type": "function",
        "function": {"name": "read_file", "arguments": ""}
      }]
    }
  }]
}
```

**Subsequent deltas (argument fragments):**
```json
{
  "choices": [{
    "delta": {
      "tool_calls": [{
        "index": 0,
        "function": {"arguments": "{\"file_"}
      }]
    }
  }]
}
```

```json
{
  "choices": [{
    "delta": {
      "tool_calls": [{
        "index": 0,
        "function": {"arguments": "path\": \"foo.rs\"}"}
      }]
    }
  }]
}
```

**Multiple tool calls use different indices:**
```json
{
  "choices": [{
    "delta": {
      "tool_calls": [
        {"index": 0, "function": {"arguments": "..."}},
        {"index": 1, "id": "call_def456", "type": "function", "function": {"name": "read_file", "arguments": ""}}
      ]
    }
  }]
}
```

### 7.2 Accumulator design

```python
@dataclass
class _ToolCallAccumulator:
    id: str = ""
    name: str = ""
    arguments: str = ""
```

The backend driver maintains `dict[int, _ToolCallAccumulator]` keyed by index.

On each delta:
1. For each entry in `delta.tool_calls`:
   - Get or create accumulator for `entry["index"]`
   - If `id` present, set `accumulator.id`
   - If `function.name` present, set `accumulator.name`
   - If `function.arguments` present, append to `accumulator.arguments`

### 7.3 Emission

When the stream ends (`data: [DONE]` or `finish_reason` in `["stop", "tool_calls"]`):
1. For each accumulator (in index order):
   - Parse `arguments` as JSON
   - If valid: yield `BackendToolCall(id=accumulator.id, name=accumulator.name, arguments=parsed)`
   - If invalid JSON: yield `BackendError(message=f"Malformed tool call arguments for {accumulator.name}")`
2. Yield `BackendDone()`

### 7.4 Edge cases

- **Text + tool calls in same response:** text deltas arrive in `delta.content`, tool call deltas in `delta.tool_calls`. Both can appear in the same chunk. Text is yielded immediately as `BackendTextDelta`. Tool calls are accumulated.
- **Empty arguments:** valid — some tools have no required params. Parse as `{}`.
- **No tool calls:** just text deltas + done. Normal text-only flow.
- **finish_reason "tool_calls":** indicates the model stopped specifically to request tool execution. Handled same as normal stream end.

---

## 8. Error handling specification

### 8.1 Error categories

| Scenario | Action |
|---|---|
| Backend connection refused | `BackendError("Cannot reach model server")` |
| Backend HTTP error (non-2xx) | `BackendError("Model server returned {status}")` |
| Malformed tool-call JSON | `BackendError("Malformed tool call arguments for {name}")` |
| Malformed SSE from backend | `BackendError("Failed to parse upstream response")` |
| Unexpected exception in orchestrator | `error_event("Internal adapter error")` + `done_event` |
| Auth failure | HTTP 401 (before SSE stream starts) |
| Invalid request body | HTTP 422 (before SSE stream starts) |

### 8.2 Guarantees

- Every SSE stream ends with a `done` event.
- Every `error` event is followed by `done`.
- No silent failures — all errors are surfaced to the client.
- No retries. The user can re-prompt.

---

## 9. Config for Atuin client

```toml
[ai]
enabled = true
endpoint = "http://127.0.0.1:8787"
api_token = "local-dev-token"

[ai.opening]
send_cwd = true
send_last_command = true

# Enable all capabilities for full tool support:
# (These are all enabled by default in Atuin)
# [ai.capabilities]
# enable_history_search = true
# enable_file_tools = true
# enable_command_execution = true
```

When `enable_tools = false` in the adapter config, it doesn't matter what capabilities Atuin sends — tools won't be forwarded to the model.

---

## 10. Testing plan

### 10.1 Unit tests

**protocol.py:**
- All SSE frame builders produce correct format
- All models parse/serialize correctly
- Extra fields ignored (forward compatibility)
- tool_call_event, tool_result_event, status_event formatting

**tools.py:**
- `build_tool_registry` with all capabilities → all 7 tools
- `build_tool_registry` with no capabilities → empty (or just suggest_command if client_invocations present)
- `build_tool_registry` with partial capabilities → correct subset
- `to_openai_tools` produces valid OpenAI tool format
- Each tool schema validates against expected shape

**backend.py:**
- Text-only stream → TextDelta events + Done
- Single tool call → accumulated correctly → BackendToolCall + Done
- Multiple tool calls → all accumulated → multiple BackendToolCall + Done
- Interleaved text + tool calls → TextDeltas + BackendToolCalls + Done
- Malformed tool-call JSON → BackendError
- Backend HTTP error → BackendError
- Backend unreachable → BackendError
- Health check success/failure
- Role-only chunks (no content) → skipped
- Empty content deltas → skipped

**translator.py:**
- Simple text messages pass through
- Tool_use blocks → OpenAI tool_calls format
- Tool_result blocks → role=tool messages
- Multiple tool_results in one message → multiple tool messages
- Mixed text + tool_result → split correctly
- Unknown block types → text with marker
- flatten_tools=True → v1 flattening behavior
- Full multi-turn conversation with tools translates correctly

**prompt.py:**
- System prompt includes context fields
- Missing context fields omitted
- Tool instructions included when tools present
- Tool instructions omitted when no tools
- Skill summaries included when present
- User contexts included when present

**orchestrator.py:**
- Text-only flow: status + text events + done
- Tool call flow: status + text + tool_call + done
- Multiple tool calls in one response
- Backend error mid-stream
- Session ID echo
- Session ID auto-generation
- enable_tools=False → no tools in backend request, flattened messages

### 10.2 Integration tests (FastAPI TestClient + mocked backend)

- Happy path: text-only request → SSE stream
- Tool call: request with capabilities → tool_call SSE event
- Continuation: request with tool_result in history → proper translation
- Auth rejection
- Invalid body → 422
- Health/readiness
- Concurrent requests
- All fixture files

### 10.3 CLI E2E tests (real Atuin binary + dummy backend)

- Text response appears in Atuin TUI
- suggest_command tool_call received by Atuin (verify with dummy backend that returns a tool call)
- Multiple turns (if automatable)

### 10.4 Live model tests (opt-in, real vLLM)

- Text generation works
- Tool calling works (model selects suggest_command)
- Multi-tool response
- Continuation after tool result

### 10.5 Fixture strategy

New fixtures needed:
- `calls/continuation.json` — request with tool_use + tool_result in message history
- `calls/with_skills.json` — request with skill summaries
- `streams/with_tool_call.txt` — OpenAI stream with tool-call deltas
- `streams/with_multiple_tool_calls.txt` — multiple tools in one stream
- `streams/text_and_tool_call.txt` — mixed text + tool call
- `streams/malformed_tool_args.txt` — invalid JSON in tool arguments

---

## 11. Implementation phases

### Phase 1: Architecture refactor (text-only parity)

**Goal:** new module layout, same v1 behavior, all tests pass.

1. Create `protocol.py` from `protocol/atuin.py` + `sse.py` (add new SSE event models/builders for tool_call, tool_result, status — but don't use them yet)
2. Create `backend.py` from `vllm_client.py` (add BackendEvent types, refactor stream_chat to yield events — text-only for now)
3. Create `orchestrator.py` from `service.py` (consume BackendEvents instead of raw deltas)
4. Create `tools.py` (tool registry, schemas, capability mapping — built but not wired)
5. Create `prompt.py` (prompt builder — used but without tool sections)
6. Update `config.py` with new fields
7. Update `app.py` for new imports
8. Remove old modules: `service.py`, `vllm_client.py`, `protocol/atuin.py`, `protocol/openai.py`, `sse.py`, `protocol/` directory
9. Update all tests
10. Verify: all existing tests pass, behavior unchanged

### Phase 2: Tool infrastructure

**Goal:** tool schemas sent to backend, tool-call deltas accumulated, tool_call SSE emitted.

1. Wire tool registry into orchestrator (build from capabilities, pass to backend)
2. Add tool-call accumulation to backend driver
3. Add status event emission
4. Wire translator for Atuin tool_use/tool_result → OpenAI format
5. Wire `enable_tools` flag (False = v1 behavior, True = tools active)
6. Add tool-related test fixtures
7. Unit test all new paths
8. Integration test tool_call SSE emission

### Phase 3: Full integration and testing

**Goal:** end-to-end tool flows work with real Atuin.

1. Prompt builder: add tool-specific instructions
2. Prompt builder: add skill summary section
3. CLI E2E tests with tool scenarios
4. Live model tests (opt-in)
5. Edge case hardening (malformed tools, unknown tools, etc.)

---

## 12. What this design explicitly defers

- **Adapter-side remote tools** (web_search, web_fetch). The architecture supports them — the tool registry has an `ADAPTER` execution class and the orchestrator can be extended to execute them inline. But no remote tools are implemented.
- **Persistence layer.** No SQLite, no SQLModel. Structured logging is sufficient.
- **Alternative backend drivers.** No Anthropic Messages driver, no Responses API driver. The BackendEvent abstraction makes adding one straightforward, but we don't build it until we need it.
- **Second backend driver abstraction.** No `BackendDriver(Protocol)` base class. `BackendClient` in `backend.py` is the only implementation. When a second backend is needed, extract the interface.

---

## 13. Acceptance criteria

V2.1 is successful when:

1. Atuin points at the adapter with `[ai].endpoint` and `[ai].api_token`.
2. Plain conversational text streams correctly (v1 parity).
3. `enable_tools=False` reproduces exact v1 behavior.
4. With `enable_tools=True`, tool schemas are sent to the backend based on request capabilities.
5. Backend tool-call deltas are accumulated and emitted as Atuin `tool_call` SSE events.
6. `suggest_command` tool calls are emitted correctly — Atuin renders them as actionable commands.
7. Continuation requests (with tool_use + tool_result in history) are translated correctly and generation continues.
8. `status` events are emitted during generation.
9. All client-side tools (`read_file`, `edit_file`, `write_file`, `execute_shell_command`, `atuin_history`, `load_skill`) have correct schemas and are emitted as tool_call events.
10. Skills summaries are injected into the system prompt when present.
11. The adapter passes CLI E2E tests against the real Atuin binary.
12. The adapter passes unit and integration tests with >95% coverage.
13. No Atuin source patches required.

---

## 14. Summary

V2.1 is a focused refactor that takes the proven v1 architecture and adds three things:

1. **Tool-call passthrough** — capability-driven tool schemas sent to the backend, tool-call deltas accumulated and emitted as Atuin SSE events.
2. **Proper message translation** — Atuin's Anthropic-style tool_use/tool_result blocks translated to OpenAI format instead of flattened to text.
3. **System prompt composition** — dynamic prompt sections based on available tools, skills, and context.

The module count stays at 8. The architecture stays flat. The adapter stays stateless. Complexity is added only where it earns its place.
