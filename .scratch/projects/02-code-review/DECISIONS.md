# DECISIONS

- Decision: Use structured fixture layout (`calls/`, `streams/`, `responses/`) and centralize test helpers in `tests/conftest.py`.
  - Rationale: Addresses fixture underuse, removes duplicated parsing logic, and enables consistent fixture-driven testing across module/service/integration/E2E layers.

- Decision: Keep CLI-driven PTY E2E as opt-in (`RUN_CLI_E2E=1`) and make HTTP-level E2E always-on.
  - Rationale: PTY/TUI automation is inherently flaky while HTTP-level full-stack tests provide stable CI coverage for adapter behavior.

- Decision: Use direct tooling commands (`uv run ruff ...`, `uv run mypy`) instead of non-existent convenience scripts.
  - Rationale: Repository does not define `lint`, `format_check`, or `typecheck` entrypoints in `pyproject.toml`; direct commands are equivalent and reproducible.
