# Issues Checklist — Refactored Code Review

Source: `.scratch/projects/05-refactored-code-review/CODE_REVIEW.md`
Date extracted: 2026-05-10

## Critical

- [x] Remove dead production code in `translator.py`: delete `build_openai_messages()` and `OpenAIChatMessage`; migrate/remove tests that only validate this legacy V1 path.

## High

- [x] Add tests for uncovered fallback/warning paths in `_translate_structured` (unexpected content/role/block shapes), covering the currently untested lines noted in review.
- [x] Add a test for `_build_environment_section` when all context fields are `None` (empty `AtuinContext`).
- [x] Refactor guideline dependency mapping in `prompt.py` to avoid index-based coupling (pair each guideline with its dependencies directly).

## Medium

- [ ] Add backend streaming test for text/tool-call interleaving (text deltas arriving between `tool_calls` delta chunks).
- [ ] Add backend streaming test for empty `tool_calls` array (`"tool_calls": []`).
- [ ] Add negative translator tests for malformed `tool_use` blocks (missing `id` and/or `name`) to assert current fallback/default behavior.
- [ ] Add orchestrator test for `enable_tools=True` with empty capabilities, asserting no tools are sent upstream (`tools=None`).
- [ ] Rename shadowed builtins in `protocol.py` signature: `tool_call_event(id, input, ...)` -> e.g. `tool_id`, `tool_input`.
- [ ] Document the single-turn tool-flow contract in orchestration docs/comments (client owns continuation loop after `tool_call`).

## Low

- [ ] In `app.py` auth check, use constant-time comparison (`hmac.compare_digest`) instead of direct string inequality.
- [ ] Consider defining `__all__` in public modules (`protocol.py`, `tools.py`, `backend.py`) for explicit export surface.
- [ ] Consider using `response.is_success` in `backend.py` status check for httpx idiom consistency.
- [ ] Consider tightening tool JSON schemas with `additionalProperties: false` where strict validation is desired.
- [ ] Consider whether upstream error-body forwarding should be sanitized/truncated further for sensitive deployments.
- [ ] Consider CORS middleware only if browser-origin clients are in scope.

## Explicitly Not Issues (no action required)

- [ ] (Optional) Keep as-is: `httpx.HTTPError` catch scope in `backend.py` is acceptable per review.
- [ ] (Optional) Keep as-is: fallback tool-call id behavior (`call_{index}`) is acceptable defensive handling.
- [ ] (Optional) Keep as-is: `None` inside JSON Schema enum is valid (redundant but harmless).
- [ ] (Optional) Keep as-is: `lru_cache` settings pattern is acceptable with existing test fixture cache clearing.
- [ ] (Optional) Keep as-is: no request-level model override (future capability, not current bug).
- [ ] (Optional) Keep as-is: no timeout/cancellation concern identified as a bug in current framework flow.
