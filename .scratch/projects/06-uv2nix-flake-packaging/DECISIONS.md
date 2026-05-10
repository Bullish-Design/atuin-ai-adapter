# Decisions — 06 uv2nix Flake Packaging

- Use `sourcePreference = "wheel"` with `pyproject-build-systems.overlays.wheel` to reduce build-system override burden.
- Target `python313` to align with `pyproject.toml` (`requires-python = ">=3.13"`).
- Expose both `packages.<system>.atuin-ai-adapter` and `default` for ergonomic consumption.
