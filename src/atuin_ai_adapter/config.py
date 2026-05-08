from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYSTEM_PROMPT_TEMPLATE = """You are a terminal assistant. The user is working in a shell and may ask you
to suggest commands, explain errors, or help with system administration tasks.

Be concise. Prefer direct answers over lengthy explanations.
When suggesting a command, output it directly without markdown code fences
unless you are comparing multiple options.
If you are unsure, say so rather than guessing."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    adapter_host: str = "127.0.0.1"
    adapter_port: int = 8787
    adapter_api_token: str = "local-dev-token"
    vllm_base_url: str = "http://127.0.0.1:8000"
    vllm_model: str
    vllm_timeout: float = 120.0
    generation_temperature: float = 0.7
    generation_max_tokens: int = 2048
    generation_top_p: float = 0.95
    system_prompt_template: str = DEFAULT_SYSTEM_PROMPT_TEMPLATE
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
