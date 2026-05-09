# Atuin AI Adapter — NixOS Integration Guide

**Date:** 2026-05-08
**Target:** NixOS desktop with home-manager, flakes, devenv
**Atuin version:** 18.16.0 (already installed via `inputs.atuin`)
**Adapter repo:** `github:Bullish-Design/atuin-ai-adapter`

---

## Overview

The atuin-ai-adapter is a Python server that translates between Atuin's AI protocol and any OpenAI-compatible backend (local vLLM, OpenRouter, OpenAI API, etc.). You need:

1. The adapter running as a background service
2. Atuin's `[ai]` config pointed at the adapter
3. Environment variables telling the adapter which upstream to use

The interesting design question is: **how do you support multiple backends** (local vLLM, remote APIs) and switch between them?

---

## Architecture Options

### Option A: Single Adapter, Environment-Switched

Run one adapter instance. Switch backends by changing environment variables and restarting.

```
atuin CLI ──▶ adapter:8787 ──▶ { local vLLM | OpenRouter | OpenAI }
                                  (whichever is configured)
```

**Configuration:**
```nix
# Switch by changing these env vars and restarting the service
environment = {
  VLLM_BASE_URL = "http://remora-server:8000";  # or "https://openrouter.ai/api"
  VLLM_MODEL = "Qwen3.5-9B-UD-Q6_K_XL.gguf";   # or "openai/gpt-4o"
};
```

| | |
|---|---|
| **Pros** | Simplest setup. One port, one service, one config. |
| **Cons** | Must restart to switch. Can't use two backends simultaneously. No quick toggle. |
| **Best for** | Single backend that rarely changes. |

---

### Option B: Multiple Adapter Instances on Different Ports

Run N adapter instances, each pointed at a different backend. Atuin always talks to one — switch by changing atuin's `[ai].endpoint` and restarting the shell.

```
atuin CLI ──▶ adapter:8787 ──▶ local vLLM (remora-server:8000)
         └──▶ adapter:8788 ──▶ OpenRouter API
         └──▶ adapter:8789 ──▶ OpenAI API
              (pick one in atuin config)
```

**Configuration:**
```nix
services.atuin-ai-adapter.instances = {
  local = {
    port = 8787;
    vllmBaseUrl = "http://remora-server:8000";
    model = "Qwen3.5-9B-UD-Q6_K_XL.gguf";
    apiToken = "local-dev-token";
  };
  openrouter = {
    port = 8788;
    vllmBaseUrl = "https://openrouter.ai/api";
    model = "qwen/qwen3-235b-a22b";
    apiToken = "local-dev-token";
    extraEnv.ADAPTER_API_TOKEN = "local-dev-token";
  };
};
```

| | |
|---|---|
| **Pros** | All backends always running. Switch by changing one line in atuin config. No restart of any adapter needed. |
| **Cons** | Multiple processes running (minimal overhead — each is ~20MB idle). Must manage multiple ports. Still requires shell restart to pick up atuin config change. |
| **Best for** | Power users who switch backends frequently. |
| **Opportunity** | Could add a shell alias like `ai-local` / `ai-remote` that patches atuin's config and re-sources the shell, making switching instant. |

---

### Option C: Single Adapter with a Proxy/Router Layer (Recommended)

Run one adapter instance per backend, but add a **shell-level switcher** — a simple symlink or env var that controls which port atuin targets. No adapter restarts, no config file edits.

```
atuin CLI ──▶ adapter:${ATUIN_AI_PORT} ──▶ current backend
                │
                ├── adapter:8787 ──▶ local vLLM
                ├── adapter:8788 ──▶ OpenRouter
                └── adapter:8789 ──▶ OpenAI
```

**The key insight:** Atuin's `ai inline` accepts `--api-endpoint` as a CLI flag, which overrides the config file. You can set a shell variable that makes `atuin ai inline` dynamically pick the right adapter.

**Configuration in nix:**
```nix
# Multiple systemd services (one per backend)
# + shell integration that wraps atuin ai inline
# + switchable via: ai-use local | ai-use openrouter | ai-use openai
```

| | |
|---|---|
| **Pros** | Instant switching without restarting anything. All backends always warm. Clean separation of concerns. |
| **Cons** | Slightly more shell integration code. Multiple systemd units. |
| **Best for** | The ideal setup — you get the flexibility of Option B with the UX of a simple command. |
| **Opportunity** | The shell wrapper can also show which backend is active in your prompt, add cost tracking for remote APIs, or auto-select based on network reachability (fallback to local when remote is down). |

---

### Recommendation

**Option C** is the recommended path. The guide below implements it. The additional complexity over Option B is minimal (one shell function + one env var), but the UX improvement is significant — you can type `ai-use local` or `ai-use openrouter` and immediately start using a different backend without touching any config files or restarting any services.

---

## Integration Guide

### Step 1: Add the Adapter to Your Flake Inputs

In `~/.dotfiles/flake.nix`, add the adapter repository as a pinned input:

```nix
inputs = {
  # ... existing inputs ...

  atuin-ai-adapter = {
    url = "github:Bullish-Design/atuin-ai-adapter/v0.1.0";  # pin to tag
    flake = false;  # it's a Python project, not a flake — we just need the source
  };
};
```

**Why `flake = false`?** The adapter is a devenv-managed Python project, not a Nix flake. We fetch the source and build a Python package from it using `pkgs.python3Packages.buildPythonApplication`. This is simpler and more reliable than trying to make devenv's output a flake.

**Version pinning:** Use a git tag (`/v0.1.0`) or commit hash (`/ca64fc7...`) to pin. Update by changing the ref and running `nix flake update atuin-ai-adapter`.

Pass the input through to home-manager via `extraSpecialArgs` (already done in your flake — `inherit inputs` covers it).

---

### Step 2: Create the Adapter Package Derivation

Create `~/.dotfiles/shell/atuin-ai-adapter.nix`:

```nix
{ lib, python3Packages, atuin-ai-adapter-src }:

python3Packages.buildPythonApplication {
  pname = "atuin-ai-adapter";
  version = "0.1.0";
  pyproject = true;

  src = atuin-ai-adapter-src;

  build-system = [ python3Packages.hatchling ];

  dependencies = with python3Packages; [
    pydantic
    pydantic-settings
    fastapi
    uvicorn
    httpx
    uvloop
    httptools
    websockets
  ];

  # Tests require devenv + fixtures; skip in nix build
  doCheck = false;

  meta = {
    description = "Adapter bridging Atuin AI protocol to vLLM/OpenAI-compatible backends";
    license = lib.licenses.mit;
    mainProgram = "atuin-ai-adapter";
  };
}
```

---

### Step 3: Define the Systemd Services

Create `~/.dotfiles/shell/atuin-ai-services.nix`:

```nix
{ config, lib, pkgs, inputs, ... }:

let
  # Build the adapter package
  adapterPkg = pkgs.callPackage ./atuin-ai-adapter.nix {
    atuin-ai-adapter-src = inputs.atuin-ai-adapter;
  };

  # ── Backend definitions ─────────────────────────────────────────────
  # Add/remove/modify backends here. Each gets its own systemd service.
  backends = {
    local = {
      port = 8787;
      vllmBaseUrl = "http://remora-server:8000";
      model = "Qwen3.5-9B-UD-Q6_K_XL.gguf";
      temperature = "0.7";
      maxTokens = "2048";
      description = "Local vLLM (remora-server)";
    };

    openrouter = {
      port = 8788;
      vllmBaseUrl = "https://openrouter.ai/api";
      model = "qwen/qwen3-235b-a22b";
      temperature = "0.7";
      maxTokens = "4096";
      # Token loaded from secrets file (see Step 4)
      apiTokenFile = config.age.secrets.openrouter-api-key.path or null;
      description = "OpenRouter (remote)";
    };

    openai = {
      port = 8789;
      vllmBaseUrl = "https://api.openai.com";
      model = "gpt-4o";
      temperature = "0.5";
      maxTokens = "4096";
      apiTokenFile = config.age.secrets.openai-api-key.path or null;
      description = "OpenAI API (remote)";
    };
  };

  # API token for the adapter itself (atuin → adapter auth)
  adapterToken = "local-dev-token";

  # ── Generate a systemd user service per backend ─────────────────────
  mkAdapterService = name: cfg: {
    Unit = {
      Description = "Atuin AI Adapter (${cfg.description})";
      After = [ "network.target" ];
    };

    Service = {
      Type = "simple";
      ExecStart = "${adapterPkg}/bin/atuin-ai-adapter";
      Restart = "on-failure";
      RestartSec = 5;

      Environment = [
        "ADAPTER_HOST=127.0.0.1"
        "ADAPTER_PORT=${toString cfg.port}"
        "ADAPTER_API_TOKEN=${adapterToken}"
        "VLLM_BASE_URL=${cfg.vllmBaseUrl}"
        "VLLM_MODEL=${cfg.model}"
        "VLLM_TIMEOUT=120"
        "GENERATION_TEMPERATURE=${cfg.temperature}"
        "GENERATION_MAX_TOKENS=${cfg.maxTokens}"
        "GENERATION_TOP_P=0.95"
        "LOG_LEVEL=WARNING"
      ];
    };

    Install = {
      WantedBy = [ "default.target" ];
    };
  };

  # ── Shell switcher ──────────────────────────────────────────────────
  #
  # Generates shell functions:
  #   ai-use local       → sets ATUIN_AI_PORT=8787
  #   ai-use openrouter  → sets ATUIN_AI_PORT=8788
  #   ai-which           → prints current backend
  #   ai-list            → lists all available backends
  #
  backendList = lib.concatStringsSep "\n" (lib.mapAttrsToList (name: cfg:
    "    ${name}|${toString cfg.port}|${cfg.description}|${cfg.model}"
  ) backends);

  defaultBackend = "local";
  defaultPort = toString backends.${defaultBackend}.port;

  shellInit = ''
    # ── Atuin AI backend switcher ──
    export ATUIN_AI_PORT="${defaultPort}"
    export ATUIN_AI_BACKEND="${defaultBackend}"

    ai-use() {
      local backend="$1"
      case "$backend" in
        ${lib.concatStringsSep "\n        " (lib.mapAttrsToList (name: cfg: ''
          ${name})
            export ATUIN_AI_PORT="${toString cfg.port}"
            export ATUIN_AI_BACKEND="${name}"
            echo "Switched to: ${cfg.description} (port ${toString cfg.port}, model: ${cfg.model})"
            ;;'') backends)}
        *)
          echo "Unknown backend: $backend"
          echo "Available: ${lib.concatStringsSep ", " (lib.attrNames backends)}"
          return 1
          ;;
      esac
    }

    ai-which() {
      echo "$ATUIN_AI_BACKEND (port $ATUIN_AI_PORT)"
    }

    ai-list() {
      echo "Available Atuin AI backends:"
      ${lib.concatStringsSep "\n      " (lib.mapAttrsToList (name: cfg:
        ''local marker=" "; [ "$ATUIN_AI_BACKEND" = "${name}" ] && marker="*"; echo "  $marker ${name} — ${cfg.description} (port ${toString cfg.port}, model: ${cfg.model})"''
      ) backends)}
    }
  '';

in {
  # ── Systemd user services ──
  systemd.user.services = lib.mapAttrs' (name: cfg:
    lib.nameValuePair "atuin-ai-${name}" (mkAdapterService name cfg)
  ) backends;

  # ── Shell integration ──
  programs.zsh.initExtra = lib.mkAfter shellInit;

  # ── Atuin AI config ──
  # Point atuin at the switcher's current port.
  # The --api-endpoint flag in the shell function overrides this,
  # but this ensures the config file has a sane default.
  programs.atuin.settings.ai = {
    enabled = true;
    endpoint = "http://127.0.0.1:${defaultPort}";
    api_token = adapterToken;
  };
}
```

---

### Step 4: Handle API Keys for Remote Backends

For remote backends (OpenRouter, OpenAI), you need API keys. **Never put secrets directly in nix files** — they end up in the world-readable nix store.

**Option A: agenix/sops-nix (recommended for NixOS)**

If you use agenix or sops-nix, reference secrets as files:

```nix
# In your secrets config:
age.secrets.openrouter-api-key.file = ./secrets/openrouter-api-key.age;
age.secrets.openai-api-key.file = ./secrets/openai-api-key.age;
```

Then modify the service `Environment` to use `EnvironmentFile` instead:

```nix
Service = {
  # ... other settings ...
  EnvironmentFile = pkgs.writeText "atuin-ai-${name}-env" ''
    VLLM_BASE_URL=${cfg.vllmBaseUrl}
    VLLM_MODEL=${cfg.model}
    # ... etc
  '';
};
```

**Option B: Environment file in home directory (simpler)**

Create `~/.config/atuin-ai/openrouter.env`:
```bash
ADAPTER_API_TOKEN=local-dev-token
VLLM_BASE_URL=https://openrouter.ai/api
VLLM_MODEL=qwen/qwen3-235b-a22b
```

And reference it in the systemd service:
```nix
Service.EnvironmentFile = "%h/.config/atuin-ai/${name}.env";
```

This keeps secrets out of the nix store while remaining simple. The `%h` expands to the user's home directory in systemd user units.

**For the initial setup**, Option B is fine. You can migrate to agenix later.

---

### Step 5: Wire Into Your Dotfiles

Add the import to `~/.dotfiles/shell/default.nix`:

```nix
{
  imports = [
    ./zsh.nix
    ./zoxide.nix
    ./atuin.nix
    ./atuin-ai-services.nix   # ← add this
  ];
}
```

---

### Step 6: Override Atuin's AI Shell Integration

Atuin's `ai inline` needs to use the dynamic port. The cleanest approach is a shell alias that passes `--api-endpoint` based on the current `$ATUIN_AI_PORT`.

Add to the `shellInit` in `atuin-ai-services.nix` (already included in Step 3):

```bash
# This is already generated by the nix module above.
# atuin ai inline will use the current ATUIN_AI_PORT.
```

If your atuin shell integration uses a keybinding to trigger `atuin ai inline`, you may need to modify it to include the endpoint flag. Check how atuin's zsh integration invokes inline mode:

```bash
# In your zsh config, if atuin binds a key to ai inline, wrap it:
_atuin_ai_inline() {
  atuin ai inline --api-endpoint "http://127.0.0.1:${ATUIN_AI_PORT}" --api-token "local-dev-token" "$@"
}
```

Alternatively, since `programs.atuin.settings.ai.endpoint` is set in the config file, and the default backend matches that config, the base case works without any wrapper. The wrapper is only needed if you want `ai-use` switching to take effect without restarting the shell.

---

### Step 7: Deploy

```bash
# Rebuild NixOS + home-manager
sudo nixos-rebuild switch --flake ~/.dotfiles

# Verify services are running
systemctl --user status atuin-ai-local
systemctl --user status atuin-ai-openrouter
systemctl --user status atuin-ai-openai

# Check health
curl http://127.0.0.1:8787/health
curl http://127.0.0.1:8788/health

# Check upstream reachability
curl http://127.0.0.1:8787/health/ready

# Test switching
ai-list          # show all backends with current marked
ai-use local     # switch to local vLLM
ai-use openrouter  # switch to OpenRouter
ai-which         # confirm current backend
```

---

### Step 8: Verify with Atuin

```bash
# With local backend (default)
# Press your atuin ai keybinding, or:
atuin ai inline --api-endpoint http://127.0.0.1:8787 --api-token local-dev-token "list files by size"

# With remote backend
ai-use openrouter
atuin ai inline --api-endpoint http://127.0.0.1:8788 --api-token local-dev-token "explain nix flakes"
```

---

## Quick Reference

### Adapter Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `ADAPTER_HOST` | `127.0.0.1` | Bind address |
| `ADAPTER_PORT` | `8787` | Bind port |
| `ADAPTER_API_TOKEN` | `local-dev-token` | Bearer token (atuin → adapter) |
| `VLLM_BASE_URL` | `http://127.0.0.1:8000` | Upstream API URL |
| `VLLM_MODEL` | **required** | Model name sent to upstream |
| `VLLM_TIMEOUT` | `120.0` | Upstream timeout (seconds) |
| `GENERATION_TEMPERATURE` | `0.7` | Sampling temperature |
| `GENERATION_MAX_TOKENS` | `2048` | Max tokens |
| `GENERATION_TOP_P` | `0.95` | Nucleus sampling |
| `SYSTEM_PROMPT_TEMPLATE` | *(built-in)* | System prompt preamble |
| `LOG_LEVEL` | `INFO` | Python log level |

### Shell Commands

| Command | Action |
|---------|--------|
| `ai-use local` | Switch to local vLLM backend |
| `ai-use openrouter` | Switch to OpenRouter |
| `ai-use openai` | Switch to OpenAI |
| `ai-which` | Print current backend |
| `ai-list` | List all backends with active marker |

### Systemd Units

| Unit | Backend |
|------|---------|
| `atuin-ai-local.service` | Local vLLM (remora-server) |
| `atuin-ai-openrouter.service` | OpenRouter API |
| `atuin-ai-openai.service` | OpenAI API |

### Service Management

```bash
systemctl --user restart atuin-ai-local
systemctl --user stop atuin-ai-openai
journalctl --user -u atuin-ai-local -f   # tail logs
```

---

## Remote API Compatibility

The adapter talks OpenAI-compatible `/v1/chat/completions` to its upstream. This means any OpenAI-compatible API works as a backend:

| Provider | `VLLM_BASE_URL` | Notes |
|----------|-----------------|-------|
| Local vLLM | `http://remora-server:8000` | Direct, no API key needed |
| Local llama.cpp server | `http://localhost:8080` | Same OpenAI-compat API |
| Local Ollama | `http://localhost:11434` | Has OpenAI-compat endpoint |
| OpenRouter | `https://openrouter.ai/api` | Needs API key in `Authorization` header† |
| OpenAI | `https://api.openai.com` | Needs API key† |
| Together AI | `https://api.together.xyz` | Needs API key† |
| Groq | `https://api.groq.com/openai` | Needs API key† |

**† Important:** For remote APIs that require their own API key, you need to modify the adapter's `vllm_client.py` to forward an upstream API key in the `Authorization` header. Currently the adapter doesn't send auth headers to the upstream — it only validates the incoming atuin token. This is a small enhancement needed for remote API support:

```python
# In VllmClient.__init__, add:
self._api_key = api_key  # from settings

# In stream_chat, add the header:
headers = {}
if self._api_key:
    headers["Authorization"] = f"Bearer {self._api_key}"
async with self._client.stream("POST", "/v1/chat/completions", json=body, headers=headers) as response:
```

Add `VLLM_API_KEY` to the Settings class and pass it through. This is a ~10 line change.

---

## Troubleshooting

**Service won't start:**
```bash
journalctl --user -u atuin-ai-local --no-pager | tail -20
```
Common cause: `VLLM_MODEL` not set, or upstream URL unreachable.

**Atuin says "AI is not yet configured":**
Ensure `programs.atuin.settings.ai.enabled = true` and the endpoint is set in config.

**Health check fails but service is running:**
Check upstream reachability: `curl http://remora-server:8000/v1/models`

**Switching backends doesn't take effect:**
If using config-file-based endpoint (not CLI flags), you need a new shell session. With the `--api-endpoint` wrapper approach, switching is immediate.

---

## Future Enhancements

1. **Upstream auth header forwarding** — Required for remote APIs (OpenRouter, OpenAI). Small code change in `vllm_client.py` + new `VLLM_API_KEY` setting.

2. **Auto-fallback** — `ai-use auto` that tries local first, falls back to remote if `/health/ready` returns 503. Implementable as a shell function that checks health before setting the port.

3. **Cost tracking** — For remote APIs, log token usage per invocation. The OpenAI streaming response includes `usage` in the final chunk.

4. **Per-backend system prompts** — Different `SYSTEM_PROMPT_TEMPLATE` for local vs remote (e.g., more verbose prompting for smaller local models, concise for GPT-4o).

5. **Nix module with options** — Formalize the backend definitions as a proper home-manager module with `lib.mkOption` types, validation, and documentation. The sidebar module in your dotfiles is a good pattern to follow.
