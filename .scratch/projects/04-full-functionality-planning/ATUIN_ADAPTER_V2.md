# ATUIN_ADAPTER_V2.md

## Atuin AI Adapter V2

**Purpose:** analysis, specification, and concept document for refactoring the existing `Bullish-Design/atuin-ai-adapter` codebase into a full-featured local backend adapter that preserves Atuin AI behavior while using a local vLLM server.

**Date:** 2026-05-08

---

## 1. Executive summary

The existing adapter is a strong **v1 text-streaming bridge**. It already proves the most important narrow point: Atuin can be pointed at a local custom endpoint, that endpoint can expose `POST /api/cli/chat`, and the adapter can translate Atuin requests into an OpenAI-compatible streaming call to vLLM and return Atuin-compatible SSE.

However, the current adapter is intentionally not a full Atuin AI implementation. It only supports a **text/error/done** subset of the Atuin stream, flattens Atuin tool blocks into plain text, ignores capability-driven tool execution, and does not implement the **continuation loop** required for client-side tool execution.

To achieve the desired V2 behavior, the adapter must evolve from a stateless text proxy into a **stateful Atuin protocol orchestrator**.

That orchestrator must:

1. preserve Atuin’s public custom-endpoint contract unchanged,
2. preserve Atuin’s client-side tools unchanged,
3. preserve skills and user-context behavior,
4. preserve command suggestion UX (`suggest_command`) unchanged,
5. preserve multi-step agent turns where the model requests tools, the Atuin client executes them locally, and the adapter continues generation,
6. optionally add adapter-side remote tools for parity with Atuin-hosted features such as web search.

The most important design decision for V2 is this:

> **The adapter must treat Atuin as the source of truth for the agent protocol, and vLLM as an inference engine behind a backend driver.**

The adapter should not flatten Atuin’s protocol into plain text anymore. Instead, it should implement an internal canonical representation of Atuin conversations, tools, sessions, and stream events, and then map that representation onto one or more backend drivers.

For the initial full-featured local implementation, the best refactor path is:

- keep the current FastAPI + Python stack,
- keep OpenAI-compatible vLLM as the **primary backend driver**,
- add a full **tool orchestration loop** on top of it,
- preserve room for future **Anthropic Messages** and **Responses API** drivers.

---

## 2. Scope of this document

This document answers four questions:

1. **What the existing adapter does today**
2. **Why it is insufficient for full Atuin AI parity**
3. **What V2 must implement to provide full local functionality**
4. **How to refactor the existing adapter library into that V2 design**

This is not a code patch. It is a detailed concept/spec intended to guide a real implementation.

---

## 3. Desired end state

The target behavior is:

- The user keeps using Atuin AI normally.
- Atuin is configured with a custom endpoint and API token.
- Atuin still owns:
  - shell integration,
  - local history access,
  - file reads/writes/edits,
  - shell execution,
  - permissions,
  - skill discovery/loading,
  - local AI session persistence.
- The adapter owns:
  - the Atuin custom endpoint server,
  - protocol translation,
  - session-aware orchestration across continuations,
  - backend model invocation,
  - optional adapter-side remote tools.
- vLLM owns:
  - model inference,
  - streaming token output,
  - tool-call generation when tool schemas are supplied.

The final user experience should be as close as possible to Atuin Hub-backed AI, except that inference happens locally.

---

## 4. Source basis

This specification is based on:

### Atuin public docs

- AI introduction
- AI settings
- tools & permissions
- skills

### Atuin open-source client code

Most importantly:

- `crates/atuin-ai/src/commands/inline.rs`
- `crates/atuin-ai/src/context.rs`
- `crates/atuin-ai/src/stream.rs`
- `crates/atuin-ai/src/tui/state.rs`
- `crates/atuin-ai/src/tools/mod.rs`
- `crates/atuin-ai/src/fsm/tests.rs`
- `crates/atuin-ai/src/event_serde.rs`
- `crates/atuin-ai/test-renders.json`
- `crates/atuin-client/src/settings.rs`
- `crates/atuin-client/src/hub.rs`

### Existing adapter repo

- `src/atuin_ai_adapter/app.py`
- `src/atuin_ai_adapter/config.py`
- `src/atuin_ai_adapter/service.py`
- `src/atuin_ai_adapter/translator.py`
- `src/atuin_ai_adapter/vllm_client.py`
- `src/atuin_ai_adapter/protocol/atuin.py`
- `src/atuin_ai_adapter/protocol/openai.py`
- `src/atuin_ai_adapter/sse.py`
- tests and internal v1 spec documents

### vLLM docs

- OpenAI-compatible server
- tool calling
- Responses API support
- Anthropic serving modules

---

## 5. Audit of the existing adapter (current V1)

### 5.1 What V1 gets right

The current adapter already has the correct outer shell:

- exposes `POST /api/cli/chat`
- validates a local bearer token
- accepts Atuin-shaped requests with `messages`, `context`, `config`, `invocation_id`, `session_id`
- calls an OpenAI-compatible `/v1/chat/completions` endpoint
- streams upstream deltas back as Atuin `text` events
- emits `done` with a session ID
- emits `error` on failure
- includes health and readiness endpoints
- has meaningful unit/integration/E2E coverage

That means the V1 repo is **not** throwaway work. It is a solid base for V2.

### 5.2 Where V1 is intentionally limited

The current V1 adapter does **not** implement full Atuin AI behavior. Its key limitations are structural, not incidental:

#### Limitation A: Atuin tool blocks are flattened

Current `translator.py` turns structured content blocks into plain strings, for example:

- `tool_use` becomes `[Tool call: ...]`
- `tool_result` becomes `[Tool result (...): ...]`

This preserves semantic context for a text-only model pass, but it destroys protocol structure.

#### Limitation B: no tool schemas are sent upstream

The adapter constructs a plain OpenAI chat completion request and does not define tools or `tool_choice`.

That means the upstream model cannot request:

- `read_file`
- `edit_file`
- `write_file`
- `execute_shell_command`
- `atuin_history`
- `load_skill`
- `suggest_command`

#### Limitation C: no `tool_call` SSE is emitted to Atuin

Atuin’s client understands stream frames beyond plain text:

- `text`
- `tool_call`
- `tool_result`
- `status`
- `done`
- `error`

V1 only emits:

- `text`
- `error`
- `done`

That is not enough for agentic flows.

#### Limitation D: no continuation loop exists

Atuin’s client-side tool execution is not one-shot.

The actual Atuin FSM flow is:

1. user submits prompt,
2. adapter/server streams initial assistant text and/or tool calls,
3. Atuin executes approved client-side tools locally,
4. tool results are added back into the conversation,
5. another stream request begins,
6. the model continues from the new context,
7. the loop repeats until the turn is complete.

V1 does not implement this orchestration pattern.

#### Limitation E: command suggestion UX is not preserved

Atuin’s command-generation UX is not just “assistant text that looks like a command.”
It is represented in the conversation as a `tool_call` with `name = "suggest_command"`, and the TUI uses that event to enable insert/execute-at-prompt behavior.

V1 does not produce this event type.

#### Limitation F: skills are not truly tool-loadable

Atuin only sends skill summaries initially and expects the model to request `load_skill` when relevant.
V1 does not implement that.

#### Limitation G: capabilities are not acted upon

Atuin request config contains capabilities and Atuin client code turns those capabilities into real tool availability.
V1 ignores them for execution purposes.

#### Limitation H: no remote/server-side tools

Atuin’s public docs mention web search when necessary, and Atuin’s internal render fixtures show remote tools such as `web_search` and `web_fetch` as possible event types.
V1 does not implement any adapter-side tools.

---

## 6. What “full Atuin AI functionality” means in practice

For this project, “full functionality” should be defined precisely.

### 6.1 Minimum acceptable parity (required)

These features must work unchanged from the user’s perspective:

1. **Conversational text responses**
2. **Command suggestion UX** using `suggest_command`
3. **Atuin history search** via `atuin_history`
4. **File reading** via `read_file`
5. **File editing/writing** via `edit_file` and `write_file`
6. **Shell command execution** via `execute_shell_command`
7. **Skills** via `load_skill`
8. **Permission prompts** driven by Atuin client behavior
9. **Multi-step continuation turns**
10. **Session continuity** across one Atuin AI session
11. **Status updates** during generation/tool phases

### 6.2 Strict parity target (extended)

To approach Hub-style parity, the adapter should also support or provide extension points for:

1. **adapter-side remote tools** such as `web_search` / `web_fetch`
2. **remote tool results** with `remote = true` and `content_length`
3. **status semantics** rich enough for Atuin’s UI
4. **future protocol evolution without breaking compatibility**

### 6.3 Explicit non-goals for the first V2 cut

These can remain out of scope if needed:

- perfect reproduction of any undocumented Hub-only reasoning policy,
- server-side persistence of the entire Atuin session DB,
- remote browser automation,
- multimodal support.

---

## 7. Atuin protocol details that V2 must honor

### 7.1 Endpoint contract

Atuin targets a base endpoint and appends `/api/cli/chat`.
So V2 must continue to expose:

```text
POST /api/cli/chat
```

with bearer-token auth and `text/event-stream` response bodies.

### 7.2 Request payload shape

The request contains:

- `messages`
- `context`
- `config`
- `invocation_id`
- optional `session_id`

The adapter must keep `extra="ignore"`-style forward compatibility.

### 7.3 Context fields

Atuin may send:

- `os`
- `shell`
- `distro`
- `pwd`
- `last_command`

V1 already injects these into the system prompt. V2 should retain that behavior, but treat it as one piece of a richer request translation pipeline.

### 7.4 Capability names

Atuin’s client always includes some capabilities and conditionally includes others.
The ones relevant here are:

Always present:

- `client_invocations`
- `client_v1_load_skill`

Conditionally present:

- `client_v1_atuin_history`
- `client_v1_read_file`
- `client_v1_edit_file`
- `client_v1_write_file`
- `client_v1_execute_shell_command`

V2 must read these capability names and build the effective tool registry from them.

### 7.5 Stream event types

Atuin’s client stream parser understands these event types:

- `text`
- `tool_call`
- `tool_result`
- `status`
- `done`
- `error`

V2 must be able to emit all of them correctly.

### 7.6 Client-side tool names

Atuin client-side tool parsing recognizes these tool names:

- `read_file`
- `edit_file`
- `write_file`
- `execute_shell_command`
- `atuin_history`
- `load_skill`

If V2 emits these as `tool_call` stream events, Atuin can handle them locally.

### 7.7 Command suggestion pseudo-tool

Atuin’s UI also treats a `tool_call` named `suggest_command` specially.
This is not a client-side tool from `tools/mod.rs`; it is a UI-level semantic event.

V2 must preserve it.

Recommended payload fields for `suggest_command` in V2:

- `command: str | null`
- `description: str | null`
- `confidence: "low" | "medium" | "high" | null`
- `danger: "low" | "medium" | "high" | null`
- `warning: str | null`

V2 should generate these via structured tool calling rather than parsing plain text.

### 7.8 Skills model

Atuin sends skill summaries first. The model is expected to request `load_skill` if it wants full content.
The adapter must therefore:

- pass skill summaries to the backend in a structured or promptable way,
- expose `load_skill` as a tool when the client capability allows it,
- preserve skill invocation semantics in follow-up turns.

### 7.9 Permissions model

Permissions for local tools are enforced by Atuin itself, not by the adapter.
That is good news: V2 does **not** need to duplicate Atuin’s permission engine for client-side tools.

However, if V2 adds adapter-side remote tools, it will need its own safety and permission policy for those tools.

### 7.10 Continuation loop

This is the single most important protocol behavior V2 must honor.

Based on Atuin FSM tests, the lifecycle is:

1. initial stream starts,
2. stream may emit tool calls,
3. Atuin checks permission and executes local tools,
4. when tool execution completes and the stream is already done, Atuin starts a **continuation** stream,
5. the continuation request includes tool results in the message history,
6. the model continues generation,
7. this repeats until a stream completes without pending tools.

Therefore, the adapter cannot be designed as a single “request in, one model stream out” stateless proxy. It must be designed for **turn continuations**.

---

## 8. vLLM realities that shape the design

### 8.1 OpenAI-compatible serving exists and is stable enough

vLLM exposes an OpenAI-compatible server, including Chat Completions, and current docs show support for Chat, Responses, and other APIs.

### 8.2 Tool calling is supported

vLLM documents support for named function calling and `tool_choice` modes including `auto`, `required`, and `none` in the Chat Completions API.

### 8.3 Responses API exists

Current vLLM docs also show `/v1/responses` support.
This may become useful later, but it is not necessary for a V2 refactor built on the current adapter codebase.

### 8.4 Anthropic support exists in vLLM internals/docs

vLLM also has Anthropic-serving modules.
Since Atuin’s internal message model is Anthropic-like (`tool_use`, `tool_result` blocks), this is architecturally relevant.

### 8.5 Practical conclusion

The V2 refactor should:

- use **OpenAI Chat Completions with tools** as the primary backend path first,
- keep a backend driver abstraction so an Anthropic Messages driver can be added later,
- avoid coupling the core orchestration logic to any one upstream API shape.

---

## 9. Recommended V2 architecture

### 9.1 Architectural principle

Use a three-layer design:

1. **Atuin protocol layer**
2. **orchestration layer**
3. **backend driver layer**

This is the right replacement for the V1 “translator + one request” architecture.

### 9.2 Layer 1: Atuin protocol layer

Responsibilities:

- parse Atuin request models,
- validate auth,
- emit Atuin SSE frames,
- understand Atuin session and message semantics,
- preserve wire compatibility.

This layer should know nothing about vLLM specifics.

### 9.3 Layer 2: orchestration layer

Responsibilities:

- maintain canonical conversation state for a turn,
- derive the effective tool registry from capabilities,
- create backend requests,
- consume backend stream events,
- emit Atuin stream events,
- decide when a turn is complete,
- handle continuations after tool execution,
- optionally manage adapter-side tools.

This layer is the heart of V2.

### 9.4 Layer 3: backend driver layer

Responsibilities:

- translate canonical conversation IR into backend-specific request shapes,
- translate backend-specific streamed deltas/tool calls into canonical events,
- hide differences between Chat Completions / Responses / Anthropic drivers.

### 9.5 Canonical IR

V2 should introduce a canonical internal representation, for example:

- `ConversationMessage`
- `ContentBlock`
- `ToolDefinition`
- `ToolCallRequest`
- `ToolCallResult`
- `AssistantDelta`
- `SessionState`
- `TurnState`
- `BackendRequest`
- `BackendEvent`

This prevents the entire application from being hard-coded to OpenAI’s wire format.

---

## 10. Concrete V2 module layout

Suggested package layout:

```text
src/atuin_ai_adapter/
    app.py
    config.py
    auth.py
    api/
        routes.py
    protocol/
        atuin.py
        sse.py
    core/
        models.py
        session.py
        orchestrator.py
        tool_registry.py
        capability_map.py
        continuation.py
        status.py
    backends/
        base.py
        openai_chat.py
        openai_responses.py      # optional in first cut
        anthropic_messages.py    # optional later
    tools/
        base.py
        local_passthrough.py     # client-executed tools
        suggest_command.py
        remote_web.py            # optional later
    translators/
        atuin_to_core.py
        core_to_atuin.py
        core_to_openai.py
        openai_to_core.py
    storage/
        models.py
        engine.py
    tests/
        ...
```

This is intentionally a refactor, not a rewrite-from-scratch in spirit.

---

## 11. Data model recommendations

Because this project is in Python and the requested coding style prefers Pydantic classes and SQLModel for DB interaction, the V2 implementation should lean into that.

### 11.1 Pydantic for protocol and runtime models

Use Pydantic v2 models for:

- request/response models,
- canonical conversation IR,
- tool definitions,
- tool call payload schemas,
- backend event types,
- status events,
- runtime config.

### 11.2 SQLModel for optional adapter persistence

A full V2 can work without durable persistence for the core continuation loop because Atuin itself sends the conversation history on each request.

However, an adapter-side session store becomes very useful for:

- tracing and debugging,
- idempotency,
- remote tool result references,
- future long-running adapter-side tools,
- audit trails.

If persistence is added, use:

- SQLite for local default deployment,
- SQLModel for schema and access.

### 11.3 Suggested persistence tables

If implemented:

- `adapter_session`
  - `session_id`
  - `created_at`
  - `updated_at`
  - `last_invocation_id`
  - `backend_driver`
  - `model_name`
- `adapter_turn`
  - `turn_id`
  - `session_id`
  - `phase`
  - `created_at`
  - `completed_at`
- `adapter_event`
  - `event_id`
  - `turn_id`
  - `event_type`
  - `payload_json`
  - `created_at`
- `remote_tool_artifact`
  - `artifact_id`
  - `session_id`
  - `tool_name`
  - `content`
  - `content_length`
  - `created_at`

This persistence is recommended, not mandatory, for the first V2 milestone.

---

## 12. Tool model for V2

V2 should distinguish three tool classes.

### 12.1 Class A: client-executed passthrough tools

These are emitted to Atuin as `tool_call` SSE events and executed by the Atuin client unchanged.

Required tools:

- `read_file`
- `edit_file`
- `write_file`
- `execute_shell_command`
- `atuin_history`
- `load_skill`

The adapter does **not** run these tools. It delegates them to Atuin.

### 12.2 Class B: UI-semantic pseudo-tools

These are not true client tools, but they are represented as tool calls because that is how Atuin’s UI expects to render and act on them.

Required pseudo-tool:

- `suggest_command`

The adapter should define this as a tool schema for the model and emit it as an Atuin `tool_call` event when chosen.

### 12.3 Class C: adapter-executed remote tools

These are optional for strict parity / future expansion.

Potential tools:

- `web_search`
- `web_fetch`
- possibly `http_fetch`
- possibly `repo_search` or other local/remote helpers

These would be executed by the adapter itself and then reinserted into the conversation as `tool_result` events.

This class is the place to restore functionality that Atuin docs allude to but which is not strictly client-side.

---

## 13. Capability-to-tool registry mapping

The adapter must build tools dynamically from the incoming capability list.

Recommended mapping:

| Capability | Tool(s) exposed upstream |
|---|---|
| `client_invocations` | `suggest_command` |
| `client_v1_load_skill` | `load_skill` |
| `client_v1_atuin_history` | `atuin_history` |
| `client_v1_read_file` | `read_file` |
| `client_v1_edit_file` | `edit_file` |
| `client_v1_write_file` | `write_file` |
| `client_v1_execute_shell_command` | `execute_shell_command` |

Skill summaries should be passed as context even when `load_skill` is exposed as a tool.

If remote adapter tools are enabled in config, they can be added independently of client capabilities.

---

## 14. Command suggestion design

### 14.1 Why this needs special handling

Atuin’s UI does not treat a suggested shell command as merely free text.
It expects a `suggest_command` tool call in the event stream so that:

- the suggestion is visible as a command artifact,
- Enter can execute it,
- Tab can insert it,
- dangerous commands can require confirmation,
- confidence/warning metadata can drive UX.

### 14.2 Recommended tool schema

Expose a backend tool like:

```json
{
  "type": "function",
  "function": {
    "name": "suggest_command",
    "description": "Use this when the best answer is a shell command to run or a command template to edit.",
    "parameters": {
      "type": "object",
      "properties": {
        "command": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
        "confidence": {"type": ["string", "null"], "enum": ["low", "medium", "high", null]},
        "danger": {"type": ["string", "null"], "enum": ["low", "medium", "high", null]},
        "warning": {"type": ["string", "null"]}
      },
      "required": ["command"]
    }
  }
}
```

### 14.3 Emission behavior

When the model chooses `suggest_command`, the adapter should emit an Atuin `tool_call` SSE event with the same fields in `input`.

That event should be persisted in the conversation history representation just like any other assistant tool call.

### 14.4 Final-text interaction

Two patterns should be allowed:

1. pure command suggestion with no assistant text,
2. assistant explanation text plus `suggest_command`.

The adapter must preserve both.

---

## 15. Continuation/orchestration loop design

This is the center of the V2 implementation.

### 15.1 Core rule

A turn is not complete just because the upstream model stream ended.
A turn is complete when:

- the upstream stream has ended, and
- there are no pending client or adapter tool calls remaining.

### 15.2 Required state machine

The adapter needs a turn-state model such as:

- `CONNECTING`
- `STREAMING`
- `WAITING_ON_CLIENT_TOOLS`
- `WAITING_ON_ADAPTER_TOOLS`
- `CONTINUING`
- `DONE`
- `ERROR`

### 15.3 Recommended algorithm

Pseudo-flow:

```text
receive Atuin request
parse canonical conversation
build effective tool registry
build backend request
open upstream stream
emit status("Thinking")

while upstream stream active:
    on text delta:
        emit Atuin text
        accumulate assistant text

    on backend tool call:
        if tool is client-executed:
            emit Atuin tool_call
            mark pending client tool
        elif tool is suggest_command:
            emit Atuin tool_call
            mark no local execution needed
        elif tool is adapter-executed:
            execute locally in adapter
            emit optional tool_call/tool_result
            append result to canonical conversation
        else:
            emit error and fail turn safely

on upstream stream done:
    if pending client tools or pending adapter tools:
        emit done(session_id)
        stop this response
        await next Atuin continuation request
    else:
        emit done(session_id)
        end turn
```

### 15.4 How continuations are triggered

Atuin already performs the continuation logic on the client side. The adapter does not need to invent a new callback channel. Instead, it must make the next request meaningful.

That means:

- when Atuin sends a follow-up request after tool execution,
- the adapter must reconstruct the conversation from the message history it receives,
- recognize that it is a continuation of the same session,
- continue generation from the updated message list.

### 15.5 Key implication

The adapter must be **session-aware**, but it does not need to keep authoritative conversation state if the incoming request already contains enough history.

Session awareness is still useful for:

- tracing,
- remote tool artifacts,
- status and debugging,
- protection against malformed continuation loops.

---

## 16. Backend driver strategy

### 16.1 Primary backend driver: OpenAI Chat Completions with tools

This should be the primary V2 target because:

- the current adapter already talks to `/v1/chat/completions`,
- vLLM documents tool calling in Chat Completions,
- this is the smallest refactor from the current codebase,
- the repo’s tests and fixtures are already centered on this path.

### 16.2 Secondary backend driver: OpenAI Responses API

This should be optional/future.
It may become attractive later, especially if the local model stack or surrounding ecosystem moves there.

### 16.3 Tertiary backend driver: Anthropic Messages

This is strategically interesting because Atuin’s internal message model is Anthropic-like.
However, it should remain behind the backend abstraction until there is a strong reason to make it the default path.

### 16.4 Recommendation

Design the backend interface so adding a new driver is straightforward:

```python
class BackendDriver(Protocol):
    async def stream_turn(
        self,
        request: CoreTurnRequest,
    ) -> AsyncIterator[BackendEvent]:
        ...
```

where `BackendEvent` can represent:

- `TextDelta`
- `ToolCall`
- `StatusDelta`
- `TurnDone`
- `BackendError`

---

## 17. Translation rules for V2

### 17.1 Stop flattening tool blocks by default

Flattening should no longer be the primary behavior.
Instead:

- canonical Atuin structured content should be preserved internally,
- backend drivers should translate that structure to their own expected format.

Flattening can remain as a **fallback** for unknown blocks or unsupported drivers.

### 17.2 System prompt strategy

Keep the useful environmental prompt injection from V1:

- OS
- shell
- distro
- current working directory
- last command
- user contexts

Add explicit behavior instructions, such as:

- when to respond with `suggest_command`,
- when to use client tools,
- when to load a skill,
- when to avoid unsafe assumptions.

### 17.3 Skill summaries

Skill summaries should be provided to the model in a structured and compact way.
Do not eagerly inline full skill bodies.

### 17.4 Tool-result reinsertion

When a tool result is present in the Atuin request history, the backend driver must preserve it as a structured tool-result message when the upstream protocol supports that. Only fall back to text flattening when necessary.

---

## 18. Status event design

Atuin supports a `status` SSE event. V2 should use it.

Recommended minimal statuses:

- `Thinking` while waiting on model generation before first text delta
- `Processing` while waiting on tool execution / continuation steps
- optional `Searching` or `Loading skill` if adapter-side tools are added

This is not strictly required for correctness, but it is part of the “unchanged Atuin AI feel” target.

---

## 19. Safety and permission model for V2

### 19.1 Client-side tools

For:

- file access,
- shell execution,
- history access,
- skill loading,

Atuin remains the enforcer of permissions.
The adapter should **not** second-guess or duplicate that permission logic.

### 19.2 Adapter-side remote tools

If V2 introduces adapter-executed tools such as web search, then V2 must define:

- which tools are enabled,
- whether they are safe-by-default,
- whether they are disabled unless explicitly configured,
- timeout and output-size limits,
- redaction/logging rules.

### 19.3 Suggested config approach

Add adapter-level feature toggles such as:

```toml
adapter_enable_remote_tools = false
adapter_enable_web_search = false
adapter_max_remote_result_chars = 12000
adapter_remote_tool_timeout_seconds = 20
```

Default them all off for a conservative local-first deployment.

---

## 20. Required refactors to the current codebase

### 20.1 `service.py`

Current role:
- build translated messages,
- send one OpenAI request,
- yield text deltas.

V2 role:
- become a thin wrapper around `orchestrator.run_turn()`.

Refactor target:
- no direct request translation or streaming logic should remain here.

### 20.2 `translator.py`

Current role:
- flatten Atuin messages into plain OpenAI messages.

V2 role:
- split into multiple translators:
  - Atuin → core IR
  - core IR → backend-specific
  - backend-specific → core events
  - core events → Atuin SSE

Keep plain-text flattening only as a fallback strategy.

### 20.3 `vllm_client.py`

Current role:
- handle plain streamed chat completions.

V2 role:
- become backend-driver plumbing or be replaced by `backends/openai_chat.py`.

Must add:
- tool schema support,
- streamed tool-call parsing,
- better structured event yield types,
- configurable parser behavior.

### 20.4 `protocol/openai.py`

Current models are too narrow.
Need to add:

- tool definitions,
- tool-choice fields,
- streamed tool-call delta parsing models or typed wrappers,
- possibly Responses API request/response models.

### 20.5 `protocol/atuin.py`

Needs expansion for:

- `tool_call` stream payloads,
- `tool_result` stream payloads,
- `status` payloads,
- richer validation helpers.

### 20.6 new `core/orchestrator.py`

This is the key new module.
Responsibilities:

- tool registry construction,
- continuation control,
- event accumulation,
- backend invocation,
- client-tool passthrough,
- adapter-tool execution,
- session bookkeeping.

### 20.7 new `core/tool_registry.py`

This should answer:

- what tools are exposed for this request?
- what schema does each tool have?
- is the tool client-executed, pseudo-tool, or adapter-executed?
- how should results be represented?

### 20.8 new `core/session.py`

Even if persistence stays light, the adapter needs a first-class session abstraction.

### 20.9 `app.py`

Should remain relatively stable.
Primary refactor needs:

- dependency injection for orchestrator/session store/backend driver,
- optional startup validation for remote tool backends,
- improved structured logging.

---

## 21. Suggested backend prompt/tool policy

### 21.1 Why tool prompting matters

vLLM can expose tools, but model behavior will only be good if the system prompt clearly describes when to:

- answer in text,
- suggest a command,
- inspect files,
- search history,
- load a skill,
- run a shell command.

### 21.2 Required policy guidance

The system prompt for V2 should explicitly say things like:

- Use `suggest_command` when the best response is a shell command or command template.
- Use `read_file` before editing a file.
- Use `edit_file` for targeted replacements and `write_file` for whole-file creation/overwrite.
- Use `atuin_history` to recall previously run commands or diagnose previous failures.
- Use `load_skill` when a skill summary appears relevant.
- Do not execute shell commands when inspection or command suggestion is sufficient.
- Prefer concise explanations after tool usage.

This policy belongs in config as a default template that can be overridden.

---

## 22. Testing plan for V2

V2 requires a much larger test matrix than V1.

### 22.1 Unit tests

#### Protocol and parsing

- Atuin `tool_call` frame formatting
- Atuin `tool_result` frame formatting
- status frame formatting
- OpenAI streamed tool-call parsing
- core event translation

#### Tool registry

- capabilities → correct tool list
- absent capabilities → tools not exposed
- suggest_command always/conditionally exposed per design

#### Orchestrator

- text-only turn completes
- tool call emitted to client and continuation recognized
- multiple tool calls in one turn
- skill load continuation
- shell execution continuation
- command suggestion event generation
- error propagation

### 22.2 Integration tests with mocked upstream

Required scenarios:

1. text-only turn
2. one `read_file` tool call then continuation text
3. two tool calls in one turn
4. `suggest_command` with no follow-up tool execution
5. `suggest_command` plus assistant explanation text
6. `load_skill` then continuation text
7. shell execution followed by continuation text
8. mid-stream upstream failure
9. malformed tool-call delta
10. status events interleaved with text
11. unsupported tool requested by model

### 22.3 CLI E2E tests with real Atuin binary

The V1 repo already has a CLI smoke test. V2 needs stronger CLI E2E tests that validate real agent behavior.

Required scenarios:

- command suggestion appears and can be inserted/executed,
- file read tool invocation is requested and continuation completes,
- shell tool invocation is requested and continuation completes,
- history tool invocation is requested and continuation completes,
- skill load is requested and continuation completes.

### 22.4 Live vLLM tests

Opt-in real-model tests should validate:

- tool-call generation for a tool-capable model,
- stable structured argument generation,
- good `suggest_command` behavior,
- continuation fidelity.

### 22.5 Fixture strategy

Add fixtures for:

- Atuin request histories before and after tool execution,
- streamed backend tool-call deltas,
- multi-turn command suggestion flows,
- malformed tool deltas,
- remote tool results.

---

## 23. Rollout plan

### Phase 0: internal cleanup

- introduce core IR
- split translators
- introduce backend abstraction
- keep V1 text path working

### Phase 1: command suggestion parity

- add `suggest_command` tool schema
- parse tool calls from vLLM
- emit Atuin `tool_call` for `suggest_command`
- preserve text + command mixed outputs

This gets the UX much closer to Atuin AI immediately.

### Phase 2: client-side tool passthrough parity

- expose `read_file`
- expose `edit_file`
- expose `write_file`
- expose `execute_shell_command`
- expose `atuin_history`
- expose `load_skill`
- implement continuation-aware orchestration

This is the core milestone for your stated goal.

### Phase 3: status events and richer session tracing

- add `status` SSE events
- optional adapter-side session/event tracing
- better logging and observability

### Phase 4: optional adapter-side remote tools

- add `web_search`
- add `web_fetch`
- add remote tool result references

### Phase 5: alternate backend drivers

- Responses API
- Anthropic Messages

---

## 24. Risks and open questions

### 24.1 Model/tool-call quality risk

Not all locally served models will call tools reliably.
This is not an adapter bug, but it affects perceived functionality.

Mitigation:

- choose a tool-capable model,
- allow backend-specific prompt templates,
- validate tool args strictly,
- build robust fallback behavior.

### 24.2 Streaming tool-call parsing complexity

OpenAI-style streamed tool calls can arrive in fragmented deltas.
The backend driver must accumulate and reconstruct them correctly before emitting Atuin `tool_call` events.

### 24.3 Session ambiguity across restarts

Because Atuin is client-driven and sends messages each time, strict adapter persistence is optional.
But if adapter-side remote tools are added, persistence becomes more valuable.

### 24.4 Undefined Hub-only semantics

Some Atuin Hub behavior may not be fully documented in the public repo.
That means V2 should target **client-observable parity**, not reverse-engineering every internal Hub behavior.

### 24.5 Tool schema evolution

Atuin’s protocol may evolve. V2 must remain tolerant of unknown keys and unknown remote tool names.

---

## 25. Acceptance criteria for V2

V2 should be considered successful when all of the following are true:

1. Atuin can point at the adapter with `[ai].endpoint` and `[ai].api_token`.
2. Plain conversational responses stream correctly.
3. Command suggestions appear as `suggest_command` tool calls and preserve insert/execute UX.
4. Atuin can request and execute `read_file`, `edit_file`, `write_file`, `execute_shell_command`, `atuin_history`, and `load_skill` against local vLLM-driven reasoning.
5. Continuation turns work until no pending tools remain.
6. Skills are discoverable and model-loadable.
7. Permissions continue to be enforced by Atuin for client-side tools.
8. Status events are visible during generation/tool phases.
9. The adapter passes CLI E2E tests against the real Atuin binary.
10. The adapter passes opt-in live tests against a real vLLM server with a tool-capable model.

---

## 26. Recommended implementation order

If I were implementing this refactor, I would do it in this exact order:

1. **Introduce core IR and backend abstraction** without changing behavior.
2. **Refactor V1 path to run through the new orchestrator** in text-only mode.
3. **Add `suggest_command` tool support** and corresponding CLI tests.
4. **Add OpenAI streamed tool-call reconstruction**.
5. **Add client-tool passthrough for `read_file` and `atuin_history` first**.
6. **Add edit/write tools**.
7. **Add shell execution tool**.
8. **Add skill loading**.
9. **Add status events**.
10. **Add optional adapter-side remote tools**.

That order minimizes risk while giving visible UX wins early.

---

## 27. Bottom line

The existing `atuin-ai-adapter` repo is a good base, but it is a **protocol shim**, not yet a full Atuin AI backend substitute.

To make it a true local backend for Atuin AI, the required leap is not “better prompting” or “a little more translation.”
It is a shift from a stateless text bridge to a **session-aware Atuin protocol orchestrator with tool semantics and continuation control**.

The good news is that this is feasible without patching Atuin.
The Atuin client already exposes the right custom endpoint, capability signals, tool names, and continuation behavior.
The adapter simply needs to honor them fully.

The correct V2 architecture is:

- Atuin-compatible protocol on the outside,
- canonical Atuin IR in the middle,
- pluggable backend drivers underneath,
- client-side tool passthrough preserved,
- command suggestion semantics preserved,
- continuation loops implemented correctly,
- optional remote tools added as a separate layer.

That design will get you from “Atuin can talk to local vLLM” to “Atuin AI works like Atuin AI, but locally.”

---

## 28. Reference inventory

### Existing adapter repo

- `https://github.com/Bullish-Design/atuin-ai-adapter`
- `https://github.com/Bullish-Design/atuin-ai-adapter/blob/main/src/atuin_ai_adapter/app.py`
- `https://github.com/Bullish-Design/atuin-ai-adapter/blob/main/src/atuin_ai_adapter/service.py`
- `https://github.com/Bullish-Design/atuin-ai-adapter/blob/main/src/atuin_ai_adapter/translator.py`
- `https://github.com/Bullish-Design/atuin-ai-adapter/blob/main/src/atuin_ai_adapter/vllm_client.py`
- `https://github.com/Bullish-Design/atuin-ai-adapter/blob/main/.scratch/projects/00-atuin-ai-brainstorming/SPEC.md`

### Atuin docs

- `https://docs.atuin.sh/cli/ai/introduction/`
- `https://docs.atuin.sh/cli/ai/settings/`
- `https://docs.atuin.sh/cli/ai/tools-permissions/`
- `https://docs.atuin.sh/cli/ai/skills/`

### Atuin source

- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/commands/inline.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/context.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/stream.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/tui/state.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/tools/mod.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/fsm/tests.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/event_serde.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-client/src/settings.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-client/src/hub.rs`
- `https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/test-renders.json`

### vLLM docs

- `https://docs.vllm.ai/en/stable/serving/openai_compatible_server/`
- `https://docs.vllm.ai/en/stable/features/tool_calling.html`
- `https://docs.vllm.ai/en/stable/api/vllm/entrypoints/anthropic/index.html`

