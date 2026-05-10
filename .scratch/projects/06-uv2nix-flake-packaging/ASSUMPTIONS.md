# Assumptions — 06 uv2nix Flake Packaging

- The user wants a repo-local flake that can be consumed from Home Manager/NixOS config.
- Keep implementation minimal and focused on packaging only.
- `uv.lock` in repo is the dependency source of truth.
- Linux targets are primary (`x86_64-linux`, `aarch64-linux`).
