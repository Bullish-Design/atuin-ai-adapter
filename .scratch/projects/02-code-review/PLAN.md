# PLAN

1. Apply production code cleanups from review (service + vllm_client) and helpers package init.
2. Restructure fixtures into `calls/`, `streams/`, `responses/` and add new fixtures.
3. Rewrite `tests/conftest.py` with shared fixture loaders, SSE parsers, and request helper.
4. Rewrite/refactor target tests (`test_vllm_client.py`, `test_service.py`, `test_app.py`, `test_real_world_remora.py`, `test_atuin_cli_e2e.py`) and extend translator fixture coverage.
5. Run dependency sync, lint, format check, typecheck, and full pytest coverage; fix any failures.
6. Update project tracking docs with final results.

NO SUBAGENTS.
