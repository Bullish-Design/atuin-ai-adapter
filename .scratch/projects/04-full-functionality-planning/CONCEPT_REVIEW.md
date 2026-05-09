# CONCEPT_REVIEW.md

## V2 Concept Document Review

**Reviewed document:** `ATUIN_ADAPTER_V2.md`
**Reviewed against:** current codebase at commit `d0fde37` (clean v1, 78 tests, 99% coverage)
**Date:** 2026-05-09

---

## 1. Overall assessment

The V2 concept document is strong. It correctly diagnoses every limitation of the current v1, proposes a sound three-layer architecture, and defines a phased rollout that delivers visible value early. The analysis of the Atuin protocol, tool taxonomy, and continuation loop is thorough and well-sourced.

That said, several areas would benefit from sharpening before implementation begins. This review identifies what is solid, what needs refinement, and what is missing.

**Verdict:** the document is a reliable foundation for V2 work, with specific improvements recommended below.

---

## 2. What the document gets right

### 2.1 V1 limitation audit (Section 5.2)

The eight named limitations (A through H) are accurate and complete. Each maps to a concrete behavior gap observable in the current code. The audit correctly identifies that the limitations are structural, not incidental — text flattening, missing tool schemas, absent continuation loop, and no `suggest_command` are all real barriers to full Atuin AI parity.

### 2.2 Three-layer architecture (Section 9)

The Atuin-protocol / orchestration / backend-driver split is the right decomposition. It correctly identifies the orchestrator as the heart of V2 and keeps the Atuin-facing contract as the stable boundary. This matches the engineering principle already established in the V1 overview document.

### 2.3 Tool taxonomy (Section 12)

The three-class tool model (client-executed passthrough, UI pseudo-tools, adapter-executed remote tools) is clean and accurate. The distinction matters because each class has different emission, execution, and continuation semantics.

### 2.4 Continuation loop design (Section 15)

The pseudo-code algorithm is correct. Critically, it identifies that the adapter does not need to invent a callback channel — Atuin already drives continuations by sending follow-up requests with tool results in the message history. The adapter just needs to recognize and handle those requests correctly.

### 2.5 Capability-to-tool mapping (Section 13)

The mapping table is complete and matches the Atuin source (`context.rs` capability expansion and `tools/mod.rs` tool definitions).

### 2.6 Permission model (Section 19)

Correctly identifies that Atuin enforces permissions for client-side tools and the adapter should not duplicate that logic. This avoids a significant complexity trap.

### 2.7 Phased rollout (Section 23)

The five-phase plan is well-ordered. Phase 1 (`suggest_command`) delivers the single most visible UX improvement with the least implementation risk. Phase 2 (client-side tool passthrough) is the core milestone. Later phases (status events, remote tools, alternate backends) are correctly deferred.

---

## 3. Issues and recommendations

### 3.1 The canonical IR may be over-specified for the first cut

**Issue:** Section 9.5 proposes ten canonical types (`ConversationMessage`, `ContentBlock`, `ToolDefinition`, `ToolCallRequest`, `ToolCallResult`, `AssistantDelta`, `SessionState`, `TurnState`, `BackendRequest`, `BackendEvent`). This is a large surface area to introduce before any of the V2 behavior is working.

**Risk:** introducing all ten types in Phase 0 before they are exercised by real code paths leads to speculative abstractions that get revised repeatedly during implementation. The current v1 is only ~242 statements. A ten-type IR layer could double the codebase before delivering any new functionality.

**Recommendation:** introduce IR types incrementally, driven by the phase that first needs them.

- Phase 0: `BackendEvent` (text delta, tool call, done, error) and `BackendDriver` protocol — these are needed immediately to abstract the backend.
- Phase 1 (`suggest_command`): `ToolDefinition` and tool call events.
- Phase 2 (continuation): `TurnState` and session awareness.
- Defer `SessionState`, `ContentBlock`, and `BackendRequest` until their use cases are concrete.

This avoids building types that sit unused between phases.

### 3.2 The module layout is over-partitioned

**Issue:** Section 10 proposes 20+ modules across 7 sub-packages (`api/`, `protocol/`, `core/`, `backends/`, `tools/`, `translators/`, `storage/`). The current v1 has 8 source modules. A 3x expansion in module count before the behavior expands proportionally creates navigation overhead and premature structure.

**Examples of over-partitioning:**

- `core/capability_map.py` and `core/tool_registry.py` are likely one module until the registry becomes complex.
- `core/continuation.py` and `core/orchestrator.py` are tightly coupled — continuation logic lives inside the orchestrator.
- `core/status.py` is probably a few constants, not a module.
- `translators/` as four separate files (`atuin_to_core.py`, `core_to_atuin.py`, `core_to_openai.py`, `openai_to_core.py`) when each is likely 30-80 lines.
- `tools/local_passthrough.py` may be trivial — passthrough tools are defined as schemas, not executed by the adapter.
- `api/routes.py` vs `app.py` — the current route definition in `app.py` is ~20 lines and doesn't need extraction yet.

**Recommendation:** start with a flatter layout that consolidates until complexity demands splitting:

```
src/atuin_ai_adapter/
    app.py              # keep as-is, add DI
    config.py           # extend with new settings
    auth.py             # extract from app.py when needed
    protocol/
        atuin.py        # extend with tool_call/tool_result/status models
        openai.py       # extend with tool schemas and tool-call parsing
        sse.py          # move from top-level (rename)
    core/
        models.py       # canonical types, introduced incrementally
        orchestrator.py  # the heart of V2
        tools.py        # registry + schemas + classification
    backends/
        base.py         # BackendDriver protocol
        openai_chat.py  # refactored from vllm_client.py
    translator.py       # keep as single module, extend
```

Split further only when a module exceeds ~200 lines or when independent testability demands it.

### 3.3 The continuation loop design underspecifies adapter-side tool execution timing

**Issue:** Section 15.3's pseudo-code handles client-executed tools cleanly (emit `tool_call`, wait for Atuin to send continuation). But for adapter-executed tools (Class C: `web_search`, `web_fetch`), the flow says "execute locally in adapter → append result to canonical conversation." This is vague about a critical question: does the adapter execute the tool during the same SSE stream and then continue generation, or does it emit a `tool_call` + `tool_result` to the client and start a new backend call?

**This matters because:**
- If the adapter executes inline and continues, the client never sees the tool call happen, which may confuse the Atuin UI.
- If the adapter emits `tool_call` + `tool_result` events for its own execution, Atuin may try to execute the tool locally and fail (since it doesn't recognize `web_search` as a client-side tool).

**Recommendation:** clarify the adapter-side tool execution model explicitly:

1. The adapter should emit `tool_call` with a marker (or rely on Atuin's `remote` field in `tool_result`) so Atuin knows it is server-side.
2. The adapter should then emit `tool_result` with `remote: true` and the result content.
3. The adapter should then internally append the result and either continue the current backend stream or open a new one.

This needs to be validated against Atuin's actual handling of remote tool results. The `test-renders.json` fixture file in the Atuin repo likely contains examples. If Atuin's client simply renders remote tool results without trying to execute them, approach (1-3) works. If not, a different strategy is needed.

### 3.4 The `suggest_command` tool schema needs validation against real Atuin behavior

**Issue:** Section 14 proposes a `suggest_command` function schema with `command`, `description`, `confidence`, `danger`, and `warning` fields. The schema looks reasonable, but the document does not confirm exactly how Atuin's `tui/state.rs` and `driver.rs` parse a `suggest_command` tool call from the stream.

**Key questions:**
- Does Atuin expect `suggest_command` as a standard `tool_call` SSE event with `name` + `input` fields?
- Or does it have a special parsing path?
- Does Atuin validate `confidence`/`danger` enums, or pass them through to UI?
- What happens if `command` is null — does Atuin show an empty suggestion or ignore it?

**Recommendation:** before implementing, capture a real `suggest_command` event from an Atuin Hub interaction (or read `tui/state.rs` more carefully for the parsing logic). Build the schema from observed behavior, not inference. This is the single most user-visible V2 feature and getting its wire format wrong would be immediately noticeable.

### 3.5 Backend tool-call streaming reconstruction is acknowledged but underspecified

**Issue:** Section 24.2 correctly identifies that "OpenAI-style streamed tool calls can arrive in fragmented deltas." This is indeed the hardest parsing problem in V2. But the document provides no detail on the accumulation strategy.

**Key facts about OpenAI-style streamed tool calls:**
- Tool call deltas arrive in `choices[0].delta.tool_calls[i]` with incremental `function.arguments` fragments.
- Multiple tool calls can be interleaved by index.
- The tool call `id` and `function.name` typically arrive in the first delta for that index, with subsequent deltas containing only argument fragments.
- The adapter must accumulate all fragments per tool-call index and emit the complete tool call only when the upstream stream signals completion (via `finish_reason: "tool_calls"` or stream end).

**Recommendation:** add a dedicated section or at minimum a design note covering:
- Accumulation strategy (buffer per tool-call index).
- When to emit: on `finish_reason: "tool_calls"` or on stream end.
- Error handling for incomplete tool-call deltas.
- Whether to emit tool calls individually as they complete or batch them.

The `backends/openai_chat.py` driver should own this logic entirely, and the orchestrator should receive fully-formed `ToolCallRequest` events.

### 3.6 The persistence layer (Section 11.3) is premature

**Issue:** The document suggests four SQLModel tables (`adapter_session`, `adapter_turn`, `adapter_event`, `remote_tool_artifact`). It correctly notes this is "recommended, not mandatory" for the first V2 milestone. But including the full schema design in the concept document creates an expectation of implementation.

**Current reality:** Atuin sends the full conversation history on every request. The adapter does not need to store anything to implement tool passthrough, continuation, or `suggest_command`. Persistence is only needed for:
- Adapter-side remote tool artifacts (Phase 4, not Phase 1-2).
- Debugging/tracing (useful but not a V2 blocker).

**Recommendation:** remove the persistence schema from the V2 concept or explicitly move it to a "Phase 4+" appendix. The adapter should remain stateless for Phases 0-3. If tracing is desired, structured logging to stdout/file is simpler and more operationally friendly than SQLite.

### 3.7 Missing: error recovery and partial-turn semantics

**Issue:** the document specifies the happy-path continuation loop but does not address:

- What happens if the backend returns a malformed tool call (e.g., invalid JSON in `function.arguments`)?
- What happens if the backend requests a tool that is not in the registry?
- What happens if a continuation request arrives but the session context is inconsistent?
- Should the adapter retry on transient backend failures mid-turn, or fail the entire turn?

**Recommendation:** add explicit error policies:
- Malformed tool call → emit `error` SSE event with diagnostic message, end turn.
- Unknown tool → emit `error` SSE event, end turn. Do not silently drop.
- Inconsistent continuation → treat as fresh turn (the message history is self-contained).
- Transient backend failure → fail the turn. No retry. The user can re-prompt.

These are simple policies, but they need to be stated so the implementation doesn't invent ad-hoc behavior.

### 3.8 Missing: system prompt management for tool-aware models

**Issue:** Section 21 recommends explicit policy guidance in the system prompt (when to use `suggest_command`, when to use `read_file`, etc.) and says "this policy belongs in config as a default template that can be overridden." But the document doesn't address:

- How the system prompt changes when tools are present vs. absent (e.g., if no file tools are enabled, don't mention them in the prompt).
- How skill summaries are injected into the system prompt.
- How user contexts are positioned relative to tool instructions.
- Whether the system prompt should be a single template or composed from sections.

**Recommendation:** design the system prompt as composable sections:
1. Base identity/behavior (always present).
2. Environment context (OS, shell, cwd, last command — from Atuin context).
3. Tool usage instructions (dynamically included based on active tool registry).
4. Skill summaries (from Atuin config, if present).
5. User contexts (from Atuin config, if present).

This keeps the prompt focused and avoids confusing the model with instructions about tools it can't use.

### 3.9 Missing: handling of the `invocation_id` field

**Issue:** the document mentions `invocation_id` in the request format but never specifies what V2 should do with it. In v1, it is accepted and ignored.

**Observation from Atuin source:** `invocation_id` appears to be a unique identifier for each top-level user prompt invocation. It is distinct from `session_id` (which spans an entire AI session across multiple prompts).

**Recommendation:** V2 should:
- Log `invocation_id` for tracing.
- Use it to correlate continuation requests within the same invocation (if Atuin sends the same `invocation_id` for continuations, which should be verified).
- Not require it for correctness — the adapter should work without it.

### 3.10 Missing: graceful degradation when the model doesn't support tool calling

**Issue:** not all models served by vLLM support function calling. The V2 concept assumes a tool-capable model but doesn't address what happens when the model ignores tool schemas or produces text instead of tool calls.

**This is a real operational scenario:** a user might serve a small model that can chat but not reliably call tools.

**Recommendation:** the orchestrator should handle this naturally:
- If the model produces only text, the adapter emits only `text` events — identical to v1 behavior.
- If the model produces malformed tool calls, the adapter emits an error.
- A config flag (`enable_tools = true/false`) could allow the user to force text-only mode even in V2, which is equivalent to the v1 behavior.

This ensures backward compatibility: V2 with `enable_tools = false` should behave exactly like v1.

---

## 4. Risks not adequately addressed

### 4.1 vLLM tool-calling reliability varies by model

The document acknowledges this (Section 24.1) but underestimates the practical impact. Tool-calling quality varies dramatically across models. Some models:
- Generate valid JSON arguments reliably; others produce malformed JSON frequently.
- Respect `tool_choice` settings; others ignore them.
- Generate tool calls as structured outputs; others embed them in free text.

**Mitigation beyond what the document suggests:**
- Test with at least 2-3 tool-capable models during V2 development (e.g., Qwen2.5, Llama 3.x, Mistral).
- Consider argument validation + retry (ask the model to fix malformed args) as a future enhancement, but keep V2 strict: valid args or error.

### 4.2 The Atuin protocol may have undocumented quirks

The V2 concept is based on reading the Atuin source code. But source reading can miss runtime behavior that only appears in specific edge cases. For example:
- Does Atuin validate tool-call IDs for uniqueness?
- Does Atuin expect tool-call IDs in a specific format?
- Does Atuin handle interleaved text + tool_call events in a single stream, or does it expect them in a specific order?

**Mitigation:** the CLI E2E test infrastructure from v1 is the best defense here. Expand it to cover tool-call scenarios early in V2 development. Real Atuin binary behavior is the ultimate source of truth.

---

## 5. Specific factual items to verify before implementation

1. **`suggest_command` wire format:** read `tui/state.rs` for the exact `tool_call` event shape Atuin expects.
2. **Remote tool result handling:** read `driver.rs` for how Atuin handles `tool_result` events with `remote: true`.
3. **Continuation request shape:** confirm that Atuin sends the same `invocation_id` and `session_id` on continuation requests.
4. **Tool-call ID format:** confirm whether Atuin generates UUIDs or some other format for tool-call IDs.
5. **Interleaved text + tool_call ordering:** confirm whether Atuin can handle text chunks arriving before, between, or after tool-call events in the same stream.
6. **`status` event payload shape:** confirm the exact JSON shape of status events from the Atuin stream parser.
7. **Skill summary format in `config.skills`:** confirm the exact structure so the adapter can inject them correctly into the system prompt.

---

## 6. Recommended changes to the concept before implementation

| Priority | Change | Section |
|---|---|---|
| High | Reduce initial IR to `BackendEvent` + `BackendDriver` + `ToolDefinition`; add others incrementally | 9.5 |
| High | Flatten initial module layout; split only when complexity demands it | 10 |
| High | Add tool-call delta accumulation strategy to the backend driver design | 16/20.3 |
| High | Verify `suggest_command` wire format against Atuin source | 14 |
| Medium | Specify error policies for malformed tool calls, unknown tools, failed continuations | 15 |
| Medium | Design system prompt as composable sections | 21 |
| Medium | Add `enable_tools` config flag for graceful degradation | Config |
| Medium | Move persistence schema to Phase 4+ appendix | 11 |
| Low | Clarify adapter-side tool execution model (emit to client or execute silently) | 15.3 |
| Low | Specify `invocation_id` handling | 7.2 |

---

## 7. Implementation order refinement

The document's suggested 10-step order (Section 26) is good. One refinement:

**Move "Add OpenAI streamed tool-call reconstruction" (step 4) before "Add `suggest_command` tool support" (step 3).**

Reason: `suggest_command` requires the backend to return a tool call. Parsing tool-call deltas from the OpenAI stream is a prerequisite for `suggest_command` to work. Building `suggest_command` first would require either a mock or an incomplete parser.

Revised order:
1. Introduce `BackendEvent`, `BackendDriver` protocol, and backend abstraction.
2. Refactor v1 path through the new backend driver (text-only, behavior unchanged).
3. Add OpenAI streamed tool-call delta accumulation in the backend driver.
4. Add `suggest_command` tool schema + emission + CLI tests.
5. Add `read_file` and `atuin_history` passthrough + continuation recognition.
6. Add `edit_file`, `write_file` passthrough.
7. Add `execute_shell_command` passthrough.
8. Add `load_skill` passthrough.
9. Add `status` events.
10. Add optional adapter-side remote tools.

---

## 8. Bottom line

The V2 concept document is a thorough and well-reasoned specification. Its core insights are correct:

- The adapter must evolve from a stateless text bridge to a session-aware orchestrator.
- The three-layer architecture (Atuin protocol / orchestration / backend driver) is the right decomposition.
- Tool passthrough and continuation loops are the essential new capabilities.
- `suggest_command` is the highest-impact early win.

The main improvements needed are:
- Less upfront abstraction (introduce IR and modules incrementally).
- More specificity on error handling, tool-call parsing, and system prompt composition.
- Verification of key Atuin protocol details before implementation begins.

With those refinements, this concept is ready to drive implementation.
