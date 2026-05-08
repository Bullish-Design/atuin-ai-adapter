# OVERVIEW.md

# Atuin ↔ vLLM Adapter Overview

## Purpose

This document explains:

1. **How Atuin AI works today**
2. **Which parts of the Atuin codebase matter for an adapter**
3. **Why an adapter is needed for a local vLLM/OpenAI-compatible backend**
4. **What the initial Python library should do**
5. **How the current skeleton is organized**
6. **How the library can evolve from a text-only bridge into a fuller Atuin-compatible AI backend**

The immediate goal is a **minimum viable adapter**:

- accept **Atuin AI client requests**
- translate them to a **vLLM OpenAI-compatible chat request**
- stream the model response back as **Atuin-compatible SSE**
- support **multiple concurrent terminals**
- remain simple enough to extend later for tools, richer session handling, and full protocol fidelity

---

## High-level summary

Atuin AI is **not** a thin OpenAI client.

The Atuin client:

- builds its own conversation state
- serializes messages in an **Atuin-specific / Anthropic-like structured format**
- posts to an Atuin AI endpoint at:

```text
POST {base_url}/api/cli/chat
```

- expects a **Server-Sent Events (SSE)** stream back
- interprets event types such as:
  - `text`
  - `tool_call`
  - `tool_result`
  - `status`
  - `done`
  - `error`

That means a raw vLLM OpenAI-compatible endpoint is **not directly compatible** with Atuin AI.

The adapter exists to bridge:

```text
Atuin client  <->  Adapter  <->  vLLM OpenAI-compatible server
```

The first implementation intentionally supports only:

- text requests
- text streaming responses
- no tool execution passthrough
- no server-side Atuin-specific behaviors beyond what the client strictly requires

---

## What Atuin AI does in general

According to the Atuin docs, Atuin AI is a terminal subcommand for command generation and information lookup through an LLM, integrated into the shell prompt and triggered by `?` when the prompt is empty. It supports command generation, follow-up refinement, conversational usage, and safety/low-confidence handling. The docs also expose settings for a custom AI `endpoint`, `api_token`, capability flags, and opening context such as current working directory and previous command.

Relevant docs:

- [Atuin AI introduction](https://docs.atuin.sh/cli/ai/introduction/)
- [Atuin AI settings](https://docs.atuin.sh/cli/ai/settings/)
- [Atuin AI tools & permissions](https://docs.atuin.sh/cli/ai/tools-permissions/)

A useful nuance for this adapter effort:

- the docs still describe Atuin AI primarily as a Hub-backed experience
- but the current code path supports a custom `endpoint` and `api_token`
- that is the hook the adapter will use

---

## Why an adapter is needed

vLLM provides an HTTP server implementing OpenAI-compatible APIs, including chat-completions and related streaming behaviors:

- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/)

However, Atuin AI does **not** speak that protocol directly.

### The incompatibilities

#### 1. Different URL shape

Atuin expects:

```text
POST /api/cli/chat
```

vLLM expects OpenAI-style endpoints such as:

```text
POST /v1/chat/completions
```

#### 2. Different request body shape

Atuin sends a request containing fields like:

- `messages`
- `context`
- `config`
- `invocation_id`
- optional `session_id`

vLLM expects an OpenAI-style body such as:

- `model`
- `messages`
- `stream`
- optional `tools`
- optional generation parameters

#### 3. Different message representation

Atuin uses a structured internal conversation format with concepts like:

- plain text assistant content
- `tool_use`
- `tool_result`

This is closer to Anthropic-style content blocks than classic OpenAI chat messages.

#### 4. Different streaming contract

Atuin expects named SSE events like:

- `event: text`
- `event: done`
- `event: error`

vLLM streams OpenAI-style incremental JSON chunks.

So the adapter must translate both:

- **request format**
- **streaming response format**

---

## The Atuin codebase: what matters

The most important Atuin code paths for this integration are all in the `atuin-ai` and `atuin-client` crates.

Repository root:

- [atuinsh/atuin](https://github.com/atuinsh/atuin)

### 1. `crates/atuin-ai/src/commands/init.rs`

This is the shell integration generator.

Why it matters:

- it binds `?` at an empty prompt to `atuin ai inline --hook`
- that means the adapter is not involved until **after the shell hook launches the AI binary**
- the shell integration also defines how Atuin interprets the result:
  - execute command
  - insert command
  - cancel

Reference:

- [crates/atuin-ai/src/commands/init.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/commands/init.rs)

### 2. `crates/atuin-ai/src/commands/inline.rs`

This is the runtime entrypoint for Atuin AI.

Why it matters:

- it resolves the AI endpoint
- it resolves the token
- it opens the history DB
- it chooses opening context
- it launches the inline TUI/driver loop

Most important behavior:

- endpoint resolution order:
  1. CLI arg
  2. `settings.ai.endpoint`
  3. fallback to `https://hub.atuin.sh`
- token resolution order:
  1. CLI arg
  2. `settings.ai.api_token`
  3. Hub session fallback

This is the critical reason the adapter is viable without modifying Atuin itself.

Reference:

- [crates/atuin-ai/src/commands/inline.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/commands/inline.rs)

### 3. `crates/atuin-client/src/settings.rs`

This defines the AI config schema.

Why it matters:

- the adapter depends on Atuin’s custom endpoint support
- this file defines:
  - `ai.endpoint`
  - `ai.api_token`
  - `ai.capabilities`
  - `ai.opening`
  - AI session DB location
  - session continuation behavior

Reference:

- [crates/atuin-client/src/settings.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-client/src/settings.rs)

### 4. `crates/atuin-ai/src/context.rs`

This builds the client/environment context sent to the AI endpoint.

Why it matters:

Atuin sends context such as:

- operating system
- shell
- optional distro
- optional current working directory
- optional last command

It also expands capability flags into strings like:

- `client_invocations`
- `client_v1_atuin_history`
- `client_v1_read_file`
- `client_v1_edit_file`
- `client_v1_write_file`
- `client_v1_execute_shell_command`
- `client_v1_load_skill`

The adapter does not need to invent this context; it receives it from Atuin and can choose how much of it to forward into the upstream model prompt.

Reference:

- [crates/atuin-ai/src/context.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/context.rs)

### 5. `crates/atuin-ai/src/tui/state.rs`

This file defines Atuin’s canonical conversation event model and converts events into outbound messages.

Why it matters:

This is one of the most important files for understanding the adapter.

It defines conversation events such as:

- `UserMessage`
- `Text`
- `ToolCall`
- `ToolResult`
- `SystemContext`
- `SkillInvocation`

Then `events_to_messages()` converts them into the message payload sent to the endpoint.

That conversion is a major clue about Atuin’s wire protocol:

- assistant text may be sent as plain string content
- tool calls are represented as blocks with:
  - `type: "tool_use"`
  - `id`
  - `name`
  - `input`
- tool results are represented as blocks with:
  - `type: "tool_result"`
  - `tool_use_id`
  - `content`
  - `is_error`

That means Atuin’s outbound format is **not** ordinary OpenAI chat history.

Reference:

- [crates/atuin-ai/src/tui/state.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/tui/state.rs)

### 6. `crates/atuin-ai/src/stream.rs`

This is the single most important file for the adapter.

Why it matters:

It defines the actual request and response behavior for the AI network call.

Key facts from this file:

- Atuin builds a `ChatRequest`
- it posts to:

```text
{hub_or_custom_base}/api/cli/chat
```

- it uses `Authorization: Bearer <token>`
- it sends JSON containing:
  - `messages`
  - `context`
  - `config`
  - `invocation_id`
  - optional `session_id`
- it requests:

```text
Accept: text/event-stream
```

It then parses specific SSE event types:

- `text`
- `tool_call`
- `tool_result`
- `status`
- `done`
- `error`

This file defines the contract our adapter must satisfy.

Reference:

- [crates/atuin-ai/src/stream.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/stream.rs)

### 7. `crates/atuin-ai/src/driver.rs`

This is the orchestration layer between the UI state machine, network stream, and client-side tool execution.

Why it matters:

- it starts the stream request
- it reacts to stream events
- it triggers permission checks
- it executes client-side tools
- it persists AI session state locally

For the minimum adapter, we only need to satisfy the text-streaming side of this loop.

Later, when tool support is added, this file becomes much more important because it defines how the client reacts to `tool_call` and `tool_result`.

Reference:

- [crates/atuin-ai/src/driver.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/driver.rs)

### 8. `crates/atuin-ai/src/tools/mod.rs`

This file defines the client-side tools Atuin can execute locally.

Examples include:

- `read_file`
- `edit_file`
- `write_file`
- `execute_shell_command`
- `atuin_history`
- `load_skill`

Why it matters:

This explains which capabilities Atuin can act on if the server asks for tools.

For the minimum adapter, we will **not** request or emit tools.

Later, this file becomes the basis for a richer bridge where model tool calls are translated back into Atuin `tool_call` SSE events.

Reference:

- [crates/atuin-ai/src/tools/mod.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/tools/mod.rs)

### 9. `crates/atuin-client/src/hub.rs`

This file implements Hub authentication/session handling.

Why it matters:

- it explains the default Hub-oriented Atuin AI flow
- it is the code path Atuin falls back to if no custom API token is configured
- it is **not** the path we want for the adapter

The adapter approach should use a custom local API token so that Atuin never needs to log into Hub for this integration.

Reference:

- [crates/atuin-client/src/hub.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-client/src/hub.rs)

---

## The Atuin AI request contract the adapter must accept

From the Atuin client perspective, the adapter must look like a valid Atuin AI backend.

### Request

The adapter must accept:

```http
POST /api/cli/chat
Authorization: Bearer <token>
Accept: text/event-stream
Content-Type: application/json
```

with a JSON body containing approximately:

```json
{
  "messages": [...],
  "context": {
    "os": "...",
    "shell": "...",
    "pwd": "...",
    "last_command": "...",
    "distro": "..."
  },
  "config": {
    "capabilities": [...],
    "user_contexts": [...],
    "skills": [...],
    "skills_overflow": "..."
  },
  "invocation_id": "...",
  "session_id": "..."
}
```

Not every field is always present, but this is the general shape.

### Response

The adapter must respond as an SSE stream.

For the minimum implementation, it only needs to emit:

#### Streaming text chunks

```text
event: text
data: {"content":"..."}
```

#### End-of-turn marker

```text
event: done
data: {"session_id":"..."}
```

#### Errors

```text
event: error
data: {"message":"..."}
```

That is enough for a text-only Atuin experience.

---

## The vLLM side of the bridge

vLLM’s OpenAI-compatible server can accept chat-completion requests such as:

```json
{
  "model": "your-model",
  "messages": [...],
  "stream": true
}
```

References:

- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/)
- [vLLM tool calling](https://docs.vllm.ai/en/stable/features/tool_calling/)

For the minimum adapter, we use:

- `/v1/chat/completions`
- `stream=true`

and we only consume:

- assistant text deltas

We do **not yet** expose tools to vLLM.

That keeps the integration simple and deterministic.

---

## Current Python library concept

The Python library is intended to be a small server-side bridge that is:

- **async**
- **streaming**
- **stateless enough to scale**
- **safe to run locally**
- **easy to extend**

### Core architecture

```text
Atuin client
   |
   v
FastAPI /api/cli/chat
   |
   v
Atuin request parser
   |
   v
Translator (Atuin -> OpenAI chat)
   |
   v
Async vLLM client (httpx)
   |
   v
Translator (OpenAI stream -> Atuin SSE)
   |
   v
StreamingResponse back to Atuin
```

### Why async matters

A single workstation may have multiple terminals invoking Atuin AI at the same time.

The adapter should therefore:

- keep each terminal request isolated
- stream each response independently
- avoid blocking threads per connection
- let the upstream vLLM server observe multiple inflight requests, which may improve batching and throughput

That is why the skeleton uses:

- FastAPI / Starlette streaming
- `httpx.AsyncClient`
- async generators
- SSE framing helpers

---

## The current skeleton layout

The current prototype package is organized like this:

```text
src/atuin_vllm_adapter/
  app.py
  config.py
  service.py
  sse.py
  translator.py
  vllm_client.py
  protocol/
    atuin.py
    openai.py
```

### `protocol/atuin.py`

Defines the Atuin-facing request/response models.

Responsibilities:

- parse incoming `/api/cli/chat` requests
- model Atuin-style SSE payloads
- preserve enough protocol fidelity for the adapter to behave like a real backend

### `protocol/openai.py`

Defines the upstream vLLM-facing models.

Responsibilities:

- build chat-completions request payloads
- parse OpenAI-compatible streaming chunks

### `translator.py`

Defines translation rules between the two protocols.

Initial policy:

- flatten Atuin content into OpenAI-compatible messages
- treat tool blocks as human-readable fallback text instead of true tools
- preserve enough meaning for useful command generation and follow-up

This translation is intentionally lossy in v1.

### `vllm_client.py`

Implements the async client for the upstream vLLM server.

Responsibilities:

- send streaming chat-completion requests
- parse streamed data frames
- yield text deltas incrementally

### `service.py`

Owns the main bridge logic.

Responsibilities:

- accept an Atuin request object
- translate to vLLM request
- call upstream
- convert text deltas into Atuin SSE `text` events
- emit `done`
- handle exceptions as Atuin `error`

### `sse.py`

A small utility layer that formats SSE frames correctly.

Responsibilities:

- serialize `event:` + `data:` lines
- ensure proper chunk boundaries
- reduce duplication elsewhere

### `config.py`

Defines runtime configuration such as:

- adapter bind host/port
- adapter API token
- vLLM base URL
- default model
- request timeout

### `app.py`

Creates the FastAPI application and wiring.

Responsibilities:

- token validation
- route definition
- health check
- `StreamingResponse`

---

## Minimal supported behavior in v1

The v1 adapter should support:

### Supported

- Atuin text requests
- Atuin follow-up conversation turns
- opening context passthrough
- streaming text back to Atuin
- concurrent requests from multiple terminals
- local bearer-token auth
- configuration of upstream model

### Explicitly not yet supported

- Atuin `tool_call` passthrough
- Atuin `tool_result` passthrough
- true Atuin capability-aware model prompting
- file tool execution through the model
- shell tool execution through the model
- server-side history search
- server-side safety classification beyond upstream model behavior
- full parity with Hub behavior

That is intentional.

The first success criterion is:

> “Atuin can send a prompt to the adapter, the adapter can stream a response from vLLM, and Atuin behaves normally for text/chat/command suggestion usage.”

---

## Translation strategy in v1

### Atuin -> OpenAI

Atuin messages may contain:

- simple strings
- structured blocks
- tool-related content

For v1, the translator should:

1. preserve role (`user` / `assistant`) where possible
2. flatten structured content into text
3. optionally inject contextual system text derived from Atuin `context` and `config`

Example strategy:

- prepend a hidden system instruction describing:
  - OS
  - shell
  - cwd
  - previous command
- convert Atuin text messages directly
- stringify `tool_use` / `tool_result` blocks instead of trying to execute them

This is a compatibility-first approach.

### OpenAI stream -> Atuin SSE

For each upstream assistant delta containing text:

- emit:

```text
event: text
data: {"content":"<delta>"}
```

When upstream finishes:

- emit:

```text
event: done
data: {"session_id":"<session-id>"}
```

On failure:

- emit:

```text
event: error
data: {"message":"<error text>"}
```

---

## Concurrency and streaming model

The adapter should assume multiple terminals may call it simultaneously.

### Desired properties

- each request is isolated
- no shared mutable per-request state
- shared upstream HTTP client for connection reuse
- backpressure handled naturally by async iteration
- cancellation propagated when the downstream client disconnects

### Why this fits vLLM well

vLLM is designed to serve many concurrent inference requests and can batch internally at the serving layer. The adapter should therefore avoid imposing a synchronous bottleneck.

That means:

- one async request handler per Atuin call
- one async upstream stream per request
- no global serialization lock
- no thread-per-stream design

---

## Expected Atuin configuration for adapter usage

Atuin should be configured to talk to the adapter, not directly to vLLM:

```toml
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

Important detail:

- `endpoint` should be the **adapter base URL**
- Atuin itself appends `/api/cli/chat`

For the minimum integration, disabling the higher-level capabilities is the cleanest path because the adapter is not yet implementing tool-aware behavior.

---

## Roadmap after v1

Once the basic streaming bridge works, the next stages are clear.

### Phase 2: tool-aware translation

Add translation for Atuin tool-capable turns.

Possible strategy:

- define OpenAI tools for:
  - file read
  - file write/edit
  - shell execution
  - history lookup
- call vLLM with those tools defined
- translate model tool calls back into Atuin `tool_call` SSE
- let Atuin execute the client-side tools
- feed resulting tool output back into the conversation loop

The relevant vLLM capability is documented here:

- [vLLM tool calling](https://docs.vllm.ai/en/stable/features/tool_calling/)

When schema correctness matters, named function calling or `tool_choice="required"` is generally preferable to `auto`.

### Phase 3: better prompt/context fidelity

Improve how Atuin context is injected upstream:

- preserve structured context more cleanly
- distinguish user context from system prompt
- model skills/user contexts more explicitly

### Phase 4: richer session semantics

Support:

- server-side session mapping
- explicit adapter-side conversation state if needed
- resumable session IDs with stronger continuity guarantees

### Phase 5: optional Anthropic-style backend mode

Atuin’s message shape is already relatively close to an Anthropic-style block format. If a future upstream is easier to target through an Anthropic-compatible interface than OpenAI chat-completions, the adapter can expose an alternate backend driver while keeping the Atuin-facing side unchanged.

---

## Main engineering principle

The adapter should treat the Atuin-facing protocol as the **stable boundary**.

That means:

- the external contract is “pretend to be an Atuin AI endpoint”
- the upstream model backend is pluggable
- translation lives in one place
- streaming semantics are preserved end-to-end

This is what makes the library useful beyond vLLM alone.

If later you want to swap:

- vLLM
- Ollama
- LM Studio
- OpenAI-compatible hosted backend
- Anthropic-compatible backend

the Atuin-facing side should not need to change.

---

## Key implementation risks

### 1. Message flattening may lose meaning

Because Atuin messages can contain structured blocks, a naive text flattening strategy may degrade multi-turn quality.

Mitigation:

- keep the translator explicit and testable
- preserve roles and structured markers
- evolve toward richer translation when needed

### 2. Atuin may emit more protocol shapes than v1 handles

Mitigation:

- validate and log unknown message content
- fail gracefully with Atuin-style `error` SSE
- add protocol tests using captured real requests

### 3. Streaming chunk handling can be subtle

Mitigation:

- keep SSE framing in its own module
- keep upstream chunk parsing in its own module
- test cancellation and partial-chunk behavior

### 4. Future tool support can become protocol-heavy

Mitigation:

- keep tool support out of v1
- design the translator and service layers so tools can be added without rewriting the whole stack

---

## Practical success criteria for the first milestone

The first milestone is successful if all of the following are true:

1. Atuin can point `[ai].endpoint` at the adapter
2. Atuin authenticates with a configured local `api_token`
3. `?` at an empty prompt opens Atuin AI normally
4. prompts are sent to the adapter
5. the adapter forwards them to vLLM
6. text streams back incrementally into Atuin
7. multiple terminals can do this concurrently without blocking each other
8. no Atuin source patches are required

---

## Suggested references

### Atuin documentation

- [Atuin AI introduction](https://docs.atuin.sh/cli/ai/introduction/)
- [Atuin AI settings](https://docs.atuin.sh/cli/ai/settings/)
- [Atuin AI tools & permissions](https://docs.atuin.sh/cli/ai/tools-permissions/)
- [Atuin AI agent hooks](https://docs.atuin.sh/cli/guide/agent-hooks/)

### vLLM documentation

- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/)
- [vLLM tool calling](https://docs.vllm.ai/en/stable/features/tool_calling/)

### Atuin codebase references

- [crates/atuin/Cargo.toml](https://github.com/atuinsh/atuin/blob/main/crates/atuin/Cargo.toml)
- [crates/atuin-ai/src/lib.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/lib.rs)
- [crates/atuin-ai/src/commands/init.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/commands/init.rs)
- [crates/atuin-ai/src/commands/inline.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/commands/inline.rs)
- [crates/atuin-ai/src/context.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/context.rs)
- [crates/atuin-ai/src/tui/state.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/tui/state.rs)
- [crates/atuin-ai/src/stream.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/stream.rs)
- [crates/atuin-ai/src/driver.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/driver.rs)
- [crates/atuin-ai/src/tools/mod.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-ai/src/tools/mod.rs)
- [crates/atuin-client/src/settings.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-client/src/settings.rs)
- [crates/atuin-client/src/hub.rs](https://github.com/atuinsh/atuin/blob/main/crates/atuin-client/src/hub.rs)

---

## Bottom line

The adapter is necessary because **Atuin AI speaks its own endpoint + SSE protocol**, while vLLM speaks an **OpenAI-compatible protocol**.

The initial Python library should therefore be treated as a **protocol bridge**, not merely an HTTP reverse proxy.

The most important Atuin files to understand are:

- `inline.rs`
- `stream.rs`
- `tui/state.rs`
- `context.rs`
- `driver.rs`
- `tools/mod.rs`
- `settings.rs`

Together, those files define:

- how Atuin launches AI
- what it sends
- what it expects back
- how it streams
- how it handles tools
- how it stores AI session state
- how a custom endpoint can be used

The current skeleton is the correct first step: a text-only, async, streaming bridge that can later be extended into a fuller Atuin-compatible local AI backend.
