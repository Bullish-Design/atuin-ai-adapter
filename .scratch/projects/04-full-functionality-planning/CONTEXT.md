# Context

Starting full V2.1 refactor from V1 baseline. Repository currently has V1 modules: service.py, vllm_client.py, sse.py, protocol/ package. No V2.1 modules exist yet.

Next action: implement Phase 1 step-by-step and commit/push after each step.

Completed Step 1.1: created src/atuin_ai_adapter/protocol.py with unified models and SSE builders.
Completed Step 1.2: added src/atuin_ai_adapter/backend.py with BackendEvent types and text-only stream parsing.
Completed Step 1.3: added src/atuin_ai_adapter/orchestrator.py with handle_chat over BackendEvent stream.
Completed Step 1.4: updated Settings with vllm_api_key and enable_tools.
Completed Step 1.5: migrated app.py imports/wiring to BackendClient + orchestrator + unified protocol module.
Completed Steps 1.6-1.9: translator import migration, removed legacy modules, migrated tests to new architecture, and passed Phase 1 validation (pytest, ruff check, ruff format --check). Note: project does not define `uv run lint`; used ruff commands directly.
