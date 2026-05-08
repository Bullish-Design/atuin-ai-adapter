# CONCEPT_REVIEW.md

# Concept Review: Atuin <-> vLLM Adapter

**Reviewed document:** `ATUIN_AI_OVERVIEW.md`
**Date:** 2026-05-08

---

## Executive Summary

The concept is **well-founded and clearly scoped**. It identifies a real interoperability gap between Atuin AI's custom SSE protocol and vLLM's OpenAI-compatible API, proposes a narrowly targeted bridge, and sequences future work sensibly. The document demonstrates strong understanding of both codebases and makes defensible architectural choices.

The main risks are not in the concept itself but in **protocol edge cases** that will only surface during integration testing with a real Atuin client. The document acknowledges this honestly.

**Overall assessment: Ready to build.** The concept is sound enough to proceed to implementation. The notes below highlight areas to sharpen, potential gaps, and a few design questions worth resolving early.

---

## Strengths

### 1. Correct problem identification

The document correctly identifies that Atuin AI is **not** a thin OpenAI wrapper. The four incompatibilities (URL shape, request body shape, message representation, streaming contract) are precisely the right ones to enumerate. This prevents the common mistake of assuming a reverse proxy or URL rewrite would suffice.

### 2. Strong codebase grounding

The overview references nine specific Atuin source files and explains *why* each one matters for the adapter. This is unusual for a concept document and significantly de-risks implementation because it shows the author has traced the actual code paths, not just read docs.

### 3. Well-chosen v1 scope

The decision to exclude tools in v1 is correct. Tool support involves a multi-turn protocol loop (server emits `tool_call`, client executes, client sends `tool_result`, server continues) that adds substantial complexity. Deferring it keeps the first milestone achievable and testable.

### 4. Async-first design

Choosing FastAPI + httpx + async generators is the right call for a streaming bridge that must handle concurrent terminals. The document explains *why* async matters (vLLM batching, no global serialization) rather than just asserting it.

### 5. Clean separation of concerns

The module layout (protocol models, translator, vLLM client, SSE framing, service orchestration, app wiring) follows a logical decomposition. Each module has a single clear responsibility. This will make the codebase easy to test and extend.

### 6. Honest risk enumeration

The four risks (message flattening, unknown protocol shapes, streaming subtlety, future tool complexity) are realistic. The mitigations are practical rather than hand-wavy.

---

## Gaps and Concerns

### 1. No captured wire-format samples

**Issue:** The document describes the Atuin request/response shapes from code reading, but does not include any captured real request/response payloads. The JSON and SSE examples are reconstructed from source analysis.

**Risk:** Subtle differences between the inferred contract and actual wire behavior could cause silent failures. For example:
- Are `messages` always present, or can the first turn omit them?
- Does Atuin send `Content-Length` or use chunked encoding for requests?
- Are there undocumented headers (e.g., `X-Atuin-Version`) the client sends?

**Recommendation:** Before or during early implementation, capture at least one real Atuin request/response pair (e.g., via mitmproxy against the Hub endpoint, or by instrumenting Atuin with `RUST_LOG=debug`). Add representative samples to a `fixtures/` directory and use them as test inputs.

### 2. Session ID handling is underspecified

**Issue:** The document mentions `session_id` in the request and in the `done` event, but does not define what the adapter should *do* with it.

Open questions:
- Should the adapter echo back the client-provided `session_id`?
- Should it generate one if none is provided?
- Does the Atuin client use the `session_id` from the `done` event to persist local AI session state?
- If the adapter generates a random `session_id`, will Atuin's session resumption logic break?

**Risk:** Getting this wrong could cause Atuin to lose conversation continuity between turns, or to silently create duplicate sessions.

**Recommendation:** Trace `session_id` through `stream.rs` and `driver.rs` to determine exact client expectations. Document the contract explicitly. At minimum, the adapter should:
- Echo back the `session_id` from the request if present.
- Generate a stable UUID if absent.
- Include it in the `done` event payload.

### 3. System prompt strategy is vague

**Issue:** The document says the translator should "optionally inject contextual system text derived from Atuin `context` and `config`" but does not specify:
- What the system prompt should actually say.
- Whether it should be a `system` role message or prepended to the first `user` message.
- How context fields (OS, shell, cwd, last command) should be formatted.
- Whether `user_contexts` and `skills` from `config` should be included.

**Risk:** The system prompt is arguably the most important part of the adapter for output quality. A poorly constructed system prompt will produce generic LLM output instead of shell-aware command suggestions.

**Recommendation:** Draft a concrete v1 system prompt template. Something like:

```
You are a shell assistant. The user is working in a terminal.

Environment:
- OS: {os}
- Shell: {shell}
- Working directory: {cwd}
- Last command: {last_command}

Respond concisely. When suggesting commands, output the command directly.
Do not wrap commands in markdown code blocks unless explaining multiple options.
```

This should be configurable but have a sensible default. Consider making it a separate config field or a Jinja2/f-string template.

### 4. Error mapping is incomplete

**Issue:** The document covers the happy path well but only briefly mentions error handling. Several failure modes need explicit design:

| Failure | Current coverage |
|---------|-----------------|
| vLLM unreachable | Not specified |
| vLLM returns 4xx/5xx | Not specified |
| vLLM stream terminates mid-response | Not specified |
| Atuin sends malformed request | Mentioned (fail gracefully) |
| Auth token mismatch | Mentioned (token validation) |
| Request timeout | Config field exists, behavior not specified |

**Risk:** Poor error handling will make debugging painful. Atuin's TUI may hang or show cryptic messages if the adapter doesn't emit proper `error` SSE events for every failure mode.

**Recommendation:** Define an explicit error-handling policy:
- All upstream failures must be caught and converted to `event: error` SSE.
- Include enough detail in the error message for the user to diagnose (e.g., "upstream vLLM server at http://localhost:8000 returned 503").
- If the upstream stream dies mid-response, emit an `error` event followed by a `done` event (or just `error` -- determine which Atuin expects).
- Log all errors server-side with request context.

### 5. No consideration of request validation depth

**Issue:** The document says the adapter should "accept" the Atuin request shape but does not discuss how strictly it should validate.

Options:
- **Strict:** Reject requests missing required fields. Pro: catches misconfiguration early. Con: fragile if Atuin changes its request shape.
- **Lenient:** Accept any JSON, extract what's needed, ignore the rest. Pro: forward-compatible. Con: harder to debug when things go wrong.

**Recommendation:** Use Pydantic models with `model_config = ConfigDict(extra="ignore")` -- this validates the fields you care about while silently accepting unknown extras. Log a warning (not an error) for unexpected top-level fields to aid debugging without breaking compatibility.

### 6. No mention of CORS or preflight

**Issue:** If anyone ever wants to test the adapter from a browser-based tool (or if Atuin ever adds a web frontend), CORS headers would be needed. This is unlikely for v1 but worth a one-line note.

**Recommendation:** Not a blocker. Ignore for v1 but consider adding a `--cors` flag later if needed.

### 7. Invocation ID is not discussed

**Issue:** The `invocation_id` field appears in the request schema but its purpose and handling are not discussed. Is it a trace ID? Should it be logged? Forwarded? Used for deduplication?

**Recommendation:** At minimum, log it for request tracing. If Atuin uses it for deduplication or idempotency, the adapter should be aware.

### 8. No testing strategy

**Issue:** The document mentions "protocol tests using captured real requests" as a risk mitigation but does not outline a testing approach.

**Recommendation:** Define at minimum:
- **Unit tests:** translator (Atuin message -> OpenAI message), SSE formatter, request parser.
- **Integration tests:** mock vLLM upstream, send a synthetic Atuin request, assert correct SSE output.
- **Smoke test:** script that sends a real Atuin-shaped HTTP request with curl and verifies the SSE stream.

Pytest + httpx's `AsyncClient` (for testing FastAPI apps) is a natural fit.

---

## Design Questions Worth Resolving Early

### Q1: Should the adapter maintain any conversation state?

The document says "stateless enough to scale" but Atuin sends a `messages` array containing full conversation history on each turn. This means the adapter *can* be stateless (each request is self-contained). But:
- Is there value in the adapter caching conversation context for efficiency?
- Should the adapter truncate long histories to fit model context windows?

**Recommendation:** Start stateless. Add context-window-aware truncation as an early enhancement if models have shorter context limits than conversation histories.

### Q2: How should model selection work?

The document mentions a "default model" in config. But:
- Should different Atuin sessions be able to use different models?
- Should the adapter support a model mapping (e.g., "if Atuin sends capability X, use model Y")?

**Recommendation:** Single configurable model for v1. Add model routing later if needed.

### Q3: What about generation parameters?

The document does not discuss temperature, top_p, max_tokens, or other generation parameters.

- Should these be configurable in the adapter?
- Should Atuin be able to pass them through?

**Recommendation:** Expose temperature, max_tokens, and top_p as adapter config with sensible defaults. Do not try to pass them through from Atuin (Atuin's request schema doesn't include them).

### Q4: Health check semantics

The document mentions a health check endpoint but doesn't specify what it should verify.

Options:
- **Shallow:** adapter is running (always 200).
- **Deep:** adapter can reach vLLM (probe upstream).

**Recommendation:** Implement both:
- `GET /health` -- shallow, always 200.
- `GET /health/ready` -- deep, verifies vLLM connectivity.

This supports deployment scenarios where a load balancer needs readiness checks.

---

## Architecture Assessment

### Module layout verdict: Good

The proposed layout is clean and follows separation of concerns. One minor suggestion:

Consider whether `service.py` will grow to need splitting. It currently owns: translation dispatch, upstream call, SSE construction, error handling. If tool support is added later, this module will need to orchestrate multi-turn loops. It may be worth noting that `service.py` is the most likely module to need refactoring in phase 2.

### Technology choices: Appropriate

| Choice | Assessment |
|--------|-----------|
| FastAPI | Good fit for async SSE streaming |
| httpx | Good fit for async streaming HTTP client |
| Pydantic | Natural for request/response validation |
| Python | Pragmatic -- fast to iterate, good async ecosystem |

No concerns with the stack.

### Scalability: Adequate for use case

A single-user workstation with multiple terminals is not a high-scale scenario. The async design is appropriate and avoids unnecessary complexity. No need for worker pools, message queues, or distributed state.

---

## Phased Roadmap Assessment

| Phase | Assessment |
|-------|-----------|
| **v1: text bridge** | Well-scoped, achievable, correct first milestone |
| **Phase 2: tools** | Logical next step, will be the hardest phase |
| **Phase 3: prompt fidelity** | Good refinement, lower risk |
| **Phase 4: sessions** | Depends on how much Atuin evolves |
| **Phase 5: Anthropic backend** | Speculative but architecturally sound given Atuin's message format |

The roadmap is realistic. Phase 2 will be the biggest lift because tool calling involves:
- Defining OpenAI-compatible tool schemas that mirror Atuin's tools.
- Translating model tool calls to Atuin `tool_call` SSE events.
- Handling the Atuin client's tool execution and `tool_result` submission.
- Managing multi-turn streaming where the model may issue multiple tool calls.

This is worth calling out explicitly as the "hardest phase" so expectations are set.

---

## Minor Nits

1. The document says "Atuin client <-> Adapter <-> vLLM OpenAI-compatible server" but uses arrows in both directions. The actual data flow is request-downstream, response-upstream. A one-directional diagram might be clearer for someone unfamiliar with the architecture.

2. The phrase "safe to run locally" in the core architecture section is undefined. If this means "no network exposure beyond localhost," that should be the default bind address. If it means "no secrets leak," the token validation section should note that the local token is not a secret per se but a gate against accidental connections.

3. The document references Atuin docs URLs but does not pin to a version or commit. If the Atuin protocol changes, these links may point to docs that no longer match the adapter's assumptions. Consider noting the Atuin version the analysis was based on.

---

## Verdict

| Criterion | Rating |
|-----------|--------|
| Problem definition | Excellent |
| Scope discipline | Excellent |
| Codebase understanding | Excellent |
| Architecture | Good |
| Protocol specification | Good (needs wire samples) |
| Error handling | Needs work |
| Testing strategy | Needs work |
| System prompt design | Needs work |
| Session semantics | Needs clarification |
| Roadmap realism | Good |

**Overall: Strong concept, ready for implementation with the caveats above addressed during development.** The document is well above average for a concept/design overview. The gaps identified are all addressable during implementation and do not require rethinking the approach.

**Recommended next step:** Begin v1 implementation. Address the session_id contract, system prompt template, and error handling policy as the first items during development rather than as a separate design phase.
