# REPO RULES — Atuin AI Adapter

## ABSOLUTE RULES — READ FIRST

1. **NO SUBAGENTS** — NEVER use the Task tool. Do ALL work directly.
2. **KEEP TRACKING CURRENT** — Maintain `.scratch/projects/<num>-<name>/` files while working.

---

Repo-specific standards and conventions. Loaded after `CRITICAL_RULES.md`.

## Project Scope

This repository builds a Python adapter that bridges:
- Atuin AI client protocol (`POST /api/cli/chat` with Atuin-shaped JSON + SSE)
- vLLM OpenAI-compatible chat-completions streaming (`/v1/chat/completions`)

Current priority is the v1 text-only bridge:
- accept Atuin requests
- translate to OpenAI chat messages
- stream `text` SSE events back to Atuin
- emit `done` and `error` correctly
- support concurrent requests

## Environment and Tooling (MANDATORY)

Use `devenv shell --` for commands that execute project code or tooling.
You do not need it for read-only inspection commands (`ls`, `cat`, `rg`, `git show`, etc.).

Before the first test run in every session:
```bash
devenv shell -- uv sync --extra dev
```

Never use `uv pip install` in this repo.

Preferred quality commands:
```bash
devenv shell -- uv run lint
devenv shell -- uv run lint_fix
devenv shell -- uv run format
devenv shell -- uv run format_check
devenv shell -- uv run typecheck
devenv shell -- pytest -q
```

## Protocol and Architecture Rules

- Treat the Atuin-facing protocol as the stable external boundary.
- Keep request and stream translation explicit and testable.
- Preserve SSE framing semantics (`text`, `done`, `error`) and avoid ad-hoc event names.
- Keep v1 implementation text-only; do not introduce partial tool-call behavior unless explicitly requested.
- Prefer stateless per-request handling for concurrency unless a task requires session-state features.

## Testing Expectations

- Add or update tests with every behavior change.
- Prioritize translator, protocol-model, SSE-framing, and stream error-path tests.
- Include integration coverage for Atuin-shaped request -> streamed SSE response where feasible.

## Key Reference Files

| Document | Path |
|----------|------|
| Concept review | `.scratch/projects/00-atuin-ai-brainstorming/CONCEPT_REVIEW.md` |
| Architecture overview | `.scratch/projects/00-atuin-ai-brainstorming/ATUIN_AI_OVERVIEW.md` |
| Agent operating instructions | `AGENTS.md` |

