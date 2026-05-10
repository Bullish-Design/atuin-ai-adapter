# BRAINSTORMING.md

## V2.1 Architecture Brainstorming

**Date:** 2026-05-09

---

## Key findings from Atuin source investigation

Before brainstorming, these are the verified facts that constrain the design:

### Wire format (confirmed from stream.rs, event_serde.rs, tui/state.rs)

- `tool_call` SSE: `{"id": "...", "name": "...", "input": {...}}`
- `tool_result` SSE: `{"tool_use_id": "...", "content": "...", "is_error": false, "remote": false, "content_length": null}`
- `status` SSE: `{"state": "Thinking..."}`
- `done` SSE: `{"session_id": "..."}`
- `error` SSE: `{"message": "..."}`
- `text` SSE: `{"content": "..."}`

### suggest_command (confirmed from tui/state.rs)

- Arrives as a normal `tool_call` with `name = "suggest_command"`
- The FSM treats it as a **stream terminal** — equivalent to `StreamDone` for turn-completion
- `input` can contain: `command`, `description`, `confidence`, `danger`, `warning`
- Only `command` is read by the TUI's `as_command()` method
- `command` can be null (conversational response with no command)

### Continuation loop (confirmed from fsm/mod.rs, driver.rs)

- Continuations are **new HTTP requests** — not stream resumptions
- `invocation_id` and `session_id` are preserved unchanged across continuations
- Tool-call IDs are **server-assigned** (the adapter generates them)
- `check_turn_completion()` fires when: stream is Done AND all tools are resolved
- If tools were used → new `StartStream` with updated messages including tool results
- If no tools → transition to Idle

### Tool schemas (confirmed from tools/mod.rs)

- `read_file`: `{file_path, offset?, limit?}`
- `edit_file`: `{file_path, old_string, new_string, replace_all?}`
- `write_file`: `{file_path, content, overwrite?}`
- `execute_shell_command`: `{command, shell?, dir?, timeout?, description?}`
- `atuin_history`: `{filter_modes, query, limit?}`
- `load_skill`: `{name}`

### Message format (confirmed from tui/state.rs events_to_messages)

- Anthropic-style content blocks, not OpenAI-style
- Assistant messages merge adjacent text + tool_use blocks into one message
- Tool results are sent as user messages with `type: "tool_result"` blocks

### Remote tool results (confirmed from event_serde.rs, fsm)

- `StreamServerToolResult` events are informational — client doesn't execute them
- They have `remote: true` in the tool_result data
- FSM logs them in conversation events but doesn't gate turn completion on them

### Skills (confirmed from context.rs)

- Only `{name, description}` summaries sent in request
- Budget: 9992 chars total, 1024 per skill
- Overflow names listed in `skills_overflow` string
- Full content loaded on-demand via `load_skill` tool

### Capabilities (confirmed from context.rs, settings.rs)

- Always present: `client_invocations`, `client_v1_load_skill`
- Conditional: `client_v1_atuin_history`, `client_v1_read_file`, `client_v1_edit_file`, `client_v1_write_file`, `client_v1_execute_shell_command`
- Extra: `ATUIN_AI__ADDITIONAL_CAPS` env var

---

## Design question 1: How much internal abstraction?

### Option A: Thin adapter — minimal IR, translate directly

Atuin request → OpenAI request with tools. OpenAI stream → Atuin SSE. No intermediate representation.

**Pros:**
- Fewest lines of code
- Easiest to understand
- Fastest to implement
- Matches the v1 spirit

**Cons:**
- Tightly couples to OpenAI chat completions format
- Adding Anthropic or Responses API backend later requires touching everything
- Tool-call accumulation logic lives in the same place as SSE emission

**Assessment:** this is the right starting point. We can always extract an IR later if we actually add a second backend. YAGNI until then.

### Option B: Full canonical IR — the V2 concept's proposal

Ten+ internal types, four translator modules, backend driver protocol.

**Pros:**
- Clean separation of concerns
- Adding backends is mechanical
- Testable in isolation

**Cons:**
- Massive upfront investment for one backend
- Double the codebase before adding any behavior
- Types will be wrong until exercised by real code
- Over-engineering for a project with one backend target

**Assessment:** too much too soon. The V2 concept's biggest weakness.

### Option C: Pragmatic middle — backend event types only

Define a small `BackendEvent` union type that the backend driver yields. The orchestrator consumes these events and emits Atuin SSE directly. No full conversation IR.

**Pros:**
- Backend is pluggable through one clean interface
- Orchestrator doesn't know about OpenAI JSON shapes
- Small surface area (~5 event types)

**Cons:**
- Slightly more abstraction than Option A
- Backend driver still needs to know tool schemas

**Assessment:** this is the sweet spot. The backend event abstraction is the one that actually matters for extensibility, and it's small.

### Decision: Option C. Define `BackendEvent` types. Everything else stays concrete.

---

## Design question 2: How to handle tool schemas?

The adapter needs to send OpenAI-format tool definitions to the backend and map capability strings to tool availability.

### Option A: Hardcode tool schemas in the backend driver

Each tool schema is a dict literal in the OpenAI backend module.

**Pros:**
- Simple, explicit, no indirection
- Easy to read and debug

**Cons:**
- Can't share schemas across backends
- Duplication if we ever add Anthropic backend

**Assessment:** fine for now. We only have one backend.

### Option B: Tool registry with schema definitions

A module that maps capability names → tool definitions (name, description, parameters schema). The backend driver reads this registry and converts to its own format.

**Pros:**
- Single source of truth for tool schemas
- Registry can be tested independently
- Clean capability → tool mapping

**Cons:**
- More indirection
- Registry is another module to maintain

**Assessment:** actually worth it here, because the capability-to-tool mapping is non-trivial logic that we want to test. The schemas themselves are data, not code. A registry that returns a list of tool definitions given a capability list is a clean, testable function.

### Decision: Tool registry module. Returns capability-filtered tool definitions. Backend driver converts to wire format.

---

## Design question 3: Where does tool-call accumulation live?

OpenAI streams tool calls as fragmented deltas. Someone has to reassemble them.

### Option A: In the backend driver

The driver accumulates fragments internally and only yields complete `ToolCall` events.

**Pros:**
- Orchestrator never sees fragments
- Clean event boundary
- Backend-specific parsing stays in the backend

**Cons:**
- Driver is more complex

**Assessment:** this is clearly right. Fragment accumulation is an OpenAI-specific concern.

### Decision: Backend driver accumulates tool-call fragments. Yields complete tool calls.

---

## Design question 4: How to handle suggest_command?

`suggest_command` is a pseudo-tool — the adapter sends it as an OpenAI function tool, the model calls it, and the adapter emits it as an Atuin `tool_call` SSE event. But the Atuin client doesn't "execute" it — it's a UI signal.

### Option A: Treat it identically to client-side tools in the backend

Define `suggest_command` as a tool in the registry. When the model calls it, emit `tool_call` SSE. Don't expect a continuation.

**Pros:**
- Uniform handling
- The FSM on the Atuin side already handles it specially

**Cons:**
- Need to know that suggest_command doesn't trigger continuation (but Atuin handles this — it treats suggest_command as a stream terminal)

**Assessment:** this is correct. The adapter should emit it like any tool_call. Atuin's FSM knows what to do.

### Decision: suggest_command is just another tool in the registry. Emit as `tool_call` SSE. No special adapter-side handling needed.

---

## Design question 5: How to handle adapter-side remote tools?

Tools like `web_search` and `web_fetch` that the adapter executes itself, not the Atuin client.

### Option A: Don't implement them in V2.1

Defer entirely. Focus on client-side tool passthrough.

**Pros:**
- Simpler scope
- No external dependencies (search APIs, etc.)

**Cons:**
- Missing feature vs Hub

**Assessment:** correct for the first milestone. Remote tools are Phase 4+ in the rollout.

### Option B: Design the orchestrator to support them but don't implement any

Make the orchestrator check if a tool call is "adapter-executed" vs "client-passthrough" and handle accordingly, but don't define any adapter-executed tools yet.

**Pros:**
- Architecture is ready
- No dead code

**Cons:**
- Slight over-engineering

**Assessment:** the orchestrator naturally needs to handle tool calls. Adding a "is this tool adapter-executed?" check is trivial. We can add the hook without adding any actual remote tools. The classification is already in the tool registry (each tool knows whether it's client-side or adapter-side).

### Decision: Design for it. Don't implement any remote tools. The tool registry already classifies tools by execution location.

---

## Design question 6: System prompt composition

The system prompt needs to change based on available tools, context, and skills.

### Option A: Single template with conditional sections

One big template string with if/else blocks.

**Pros:**
- Everything in one place
- Easy to see the full prompt

**Cons:**
- Gets messy as sections grow
- Hard to test individual sections

### Option B: Composable prompt builder

A function that assembles sections: base identity, environment, tool instructions, skills, user contexts.

**Pros:**
- Each section testable independently
- Tool instructions auto-adjust to available tools
- Clean separation

**Cons:**
- Slightly more code than a template

**Assessment:** Option B, but keep it simple — a function that builds a list of strings and joins them. No framework.

### Decision: Prompt builder function with sections. Tool instructions derived from active tool registry.

---

## Design question 7: Module layout

### What we need

1. **app.py** — FastAPI routes, auth, lifespan
2. **config.py** — settings
3. **protocol models** — Atuin request/response models, SSE helpers
4. **tool registry** — capability mapping, tool schemas, classification
5. **orchestrator** — the core bridge logic (translate, stream, emit)
6. **backend driver** — OpenAI chat completions with tool-call accumulation
7. **prompt builder** — system prompt composition
8. **translator** — Atuin messages → OpenAI messages (including tool blocks)

### Proposed layout

```
src/atuin_ai_adapter/
    __init__.py
    app.py               # FastAPI app, routes, auth, lifespan
    config.py            # Settings
    protocol.py          # Atuin models + SSE event builders
    tools.py             # Tool registry, schemas, capability mapping
    orchestrator.py      # Core bridge: request → translate → stream → emit
    backend.py           # OpenAI backend driver + tool-call accumulator
    translator.py        # Atuin messages ↔ OpenAI messages
    prompt.py            # System prompt builder
```

8 modules. Compared to v1's 8 modules (app, config, service, sse, translator, vllm_client, protocol/atuin, protocol/openai). Same count, different responsibilities.

**Key changes from v1:**
- `protocol/atuin.py` + `protocol/openai.py` + `sse.py` → `protocol.py` (Atuin-side models + SSE) + models in `backend.py` (OpenAI-side)
- `service.py` → `orchestrator.py` (much richer)
- `vllm_client.py` → `backend.py` (adds tool-call accumulation)
- New: `tools.py`, `prompt.py`
- `translator.py` stays but gets richer (handles tool blocks properly instead of flattening)

**Why not a `protocol/` sub-package?** Because we only have Atuin-side protocol models. The OpenAI models live with the backend driver. Two files in a package is overhead without benefit.

**Why not a `core/` sub-package?** Because the orchestrator is one module. We don't need a package for one file.

**Why not a `backends/` sub-package?** Because we have one backend. When we add a second, we extract.

### Decision: flat 8-module layout as above.

---

## Design question 8: Error handling strategy

### Malformed tool call from backend

Model returns invalid JSON in tool call arguments.

**Policy:** emit `error` SSE event, then `done`. Don't retry. The user can re-prompt. Log the malformed data for debugging.

### Unknown tool from backend

Model calls a tool that isn't in the registry.

**Policy:** emit `error` SSE event, then `done`. This shouldn't happen if we use `tool_choice` correctly, but models can misbehave.

### Backend connection failure mid-stream

vLLM goes down during streaming.

**Policy:** emit `error` SSE event, then `done`. No retry.

### Continuation with inconsistent history

Atuin sends a follow-up request but the message history doesn't look like a continuation.

**Policy:** treat it as a fresh turn. The message history is self-contained — we don't need to match it against previous state.

### Decision: fail fast, fail cleanly. Error → done. No retry. No silent drops.

---

## Design question 9: enable_tools config flag

Should there be a way to run V2 in text-only mode?

**Yes.** A user with a model that can't do tool calling should still be able to use the adapter for basic chat. `enable_tools = false` should make V2 behave exactly like v1: text streaming only, tool blocks flattened.

This is also useful for debugging and for users who want to run with tools disabled in their Atuin config.

### Decision: `enable_tools: bool = True` in config. When false, no tool schemas sent to backend, tool blocks flattened to text (v1 behavior).

---

## Design question 10: Session tracking

### What the adapter needs to track

Per-request (not persisted):
- Incoming Atuin request
- Translated messages
- Active tool registry for this request
- Session ID to echo back

The adapter does NOT need to persist sessions because:
- Atuin sends the full conversation history on every request
- The adapter doesn't need memory across requests
- Continuations carry their own context

### What about tracing?

Structured logging is sufficient. Log `invocation_id`, `session_id`, tool calls, errors. No database needed.

### Decision: stateless per-request. Structured logging for tracing. No persistence layer.

---

## Design question 11: How to handle interleaved text + tool_call in the stream?

The OpenAI streaming format can have text content AND tool calls in the same response. Atuin supports this — the FSM can receive `StreamChunk` and `StreamToolCall` events in the same stream.

The orchestrator should:
1. Emit `text` SSE events as text deltas arrive.
2. Accumulate tool-call fragments in the backend driver.
3. When the stream ends (or a tool call is complete), emit `tool_call` SSE events for each completed tool call.
4. Emit `done`.

The ordering is: text chunks stream as they arrive, tool calls emit when fully accumulated (typically at stream end).

### Decision: text streams immediately, tool calls emit when complete (after accumulation).

---

## Design question 12: How to generate tool-call IDs?

Atuin expects tool-call IDs to be server-assigned strings. The adapter is the server.

Options:
- UUID4: `"tc_<uuid4>"` — simple, unique
- Match Anthropic format: `"toolu_<base62>"` — cosmetic compatibility
- Use whatever the backend model returns — if the model returns tool-call IDs, forward them

**Assessment:** use the backend model's tool-call IDs. OpenAI-compatible APIs return `id` fields on tool calls (e.g., `"call_abc123"`). Forward those directly to Atuin. If the backend doesn't provide one, generate a UUID.

### Decision: forward backend-provided tool-call IDs. Fallback to generated UUID.

---

## Architecture summary

```
Atuin client
    │
    ▼
app.py (FastAPI)
    │  POST /api/cli/chat
    │  auth, parse AtuinChatRequest
    ▼
orchestrator.py
    │  1. Build tool registry from capabilities
    │  2. Build system prompt (prompt.py)
    │  3. Translate messages (translator.py)
    │  4. Call backend driver (backend.py)
    │  5. Stream events back as Atuin SSE (protocol.py)
    ▼
backend.py (OpenAI chat completions)
    │  POST /v1/chat/completions
    │  stream=true, tools=[...]
    │  Accumulate tool-call fragments
    │  Yield BackendEvent(TextDelta | ToolCall | Done | Error)
    ▼
vLLM / any OpenAI-compatible server
```

For continuations:
- Atuin sends a new request with tool results in message history
- Orchestrator translates the full history (including tool_use + tool_result blocks)
- Backend gets a fresh call with the complete conversation
- Cycle repeats

---

## Implementation phases

### Phase 1: Refactor to new architecture (text-only parity with v1)
- New module layout
- Backend driver with BackendEvent types
- Orchestrator replaces service.py
- Protocol module consolidation
- All existing tests pass

### Phase 2: Tool infrastructure
- Tool registry with capability mapping
- Tool schemas for all 7 tools (6 client + suggest_command)
- Backend driver: tool-call delta accumulation
- Translator: proper Atuin tool_use/tool_result → OpenAI tool messages
- Orchestrator: emit tool_call SSE events
- Protocol: tool_call, tool_result, status SSE models

### Phase 3: Full integration
- End-to-end tool flow testing
- Status events
- Prompt builder with tool-aware instructions
- CLI E2E tests with tool scenarios
- enable_tools config flag

### Phase 4: Polish and remote tools (future)
- Adapter-side remote tools
- Additional backend drivers
- Session tracing improvements

---

## Risks

1. **Model tool-calling quality** — not all models call tools well. Mitigated by `enable_tools` flag and choosing a good model.

2. **Tool-call accumulation edge cases** — fragmented deltas can be tricky. Mitigated by thorough unit tests with real vLLM output fixtures.

3. **Atuin protocol evolution** — Atuin may change. Mitigated by `extra="ignore"` on all models and CLI E2E tests against real Atuin binary.

4. **suggest_command reliability** — the model needs good prompting to use suggest_command correctly. Mitigated by explicit system prompt instructions.
