# Plan — 06 uv2nix Flake Packaging

## Goal
Add a minimal repository-local `flake.nix` that builds `atuin-ai-adapter` via `uv2nix` and exposes `packages.<system>.default`.

## Steps
1. Create project tracking files and assumptions.
2. Add `flake.nix` with inputs: `nixpkgs`, `pyproject-nix`, `uv2nix`, `pyproject-build-systems`.
3. Implement per-system package output using `workspace.loadWorkspace` and `mkApplication`.
4. Add a small `checks` output to verify the package builds.
5. Update tracking files with status and decisions.

## Acceptance
- `flake.nix` exists and evaluates structurally.
- Exposes `packages.<system>.atuin-ai-adapter` and `packages.<system>.default`.
- Uses `python313` and `sourcePreference = "wheel"`.
