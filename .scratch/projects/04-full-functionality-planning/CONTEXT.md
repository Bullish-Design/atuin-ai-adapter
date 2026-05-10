# Context

Starting full V2.1 refactor from V1 baseline. Repository currently has V1 modules: service.py, vllm_client.py, sse.py, protocol/ package. No V2.1 modules exist yet.

Next action: implement Phase 1 step-by-step and commit/push after each step.

Completed Step 1.1: created src/atuin_ai_adapter/protocol.py with unified models and SSE builders.
Completed Step 1.2: added src/atuin_ai_adapter/backend.py with BackendEvent types and text-only stream parsing.
