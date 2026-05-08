# Python Linting (Ruff)

Use this skill when changing Python code in this repo and you need linting/formatting to match project requirements.

## Required Tools
- `ruff` for linting and formatting.
- Run through devenv: `devenv shell -- ...`

## Canonical Commands
- Lint: `devenv shell -- uv run lint`
- Lint with autofix: `devenv shell -- uv run lint_fix`
- Format: `devenv shell -- uv run format`
- Format check only: `devenv shell -- uv run format_check`

## Scope
- Lint and format these paths by default: `src` and `tests`.
- If those directories do not exist yet, run Ruff on the Python paths that do exist.

## Configuration Source
- Ruff config lives in `pyproject.toml` under:
  - `[tool.ruff]`
  - `[tool.ruff.lint]`
  - `[tool.ruff.format]`
  - `[tool.uv.scripts]`

## Enforcement
- Before finalizing substantial Python edits, run at least:
  1. `devenv shell -- uv run lint`
  2. `devenv shell -- uv run format_check`
