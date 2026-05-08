# AGENTS.md

Read `.scratch/CRITICAL_RULES.md` first in every session, then `.scratch/REPO_RULES.md`.

Operational reminders:
- Never use subagents.
- Keep project tracking files in `.scratch/projects/<num>-<name>/` up to date.
- Use `devenv shell -- ...` for all environment-dependent CLI commands.
- This includes tests, project scripts, demos, linters/formatters/typecheckers, dependency sync, and app/runtime commands.
- Before the first test run in each session, sync dependencies:
  - `devenv shell -- uv sync --extra dev`

Available local skills:
- `.scratch/skills/python-linting-ruff/SKILL.md`
- `.scratch/skills/python-typecheck-ty/SKILL.md`

Quality gate checklist before finalizing Python changes:
1. Run `devenv shell -- uv run lint` for any Python edit.
2. Run `devenv shell -- uv run format_check` for any Python edit.
3. Run `devenv shell -- uv run typecheck` when changing signatures, typed models, protocol/contracts, or public interfaces.
4. Run targeted tests for changed behavior; run the full suite when changes are cross-cutting.
5. If checks fail, fix and rerun until clean; mention any unresolved blockers explicitly.
