from __future__ import annotations

import pytest
from pydantic import ValidationError

from atuin_ai_adapter.config import Settings


def test_defaults_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_MODEL", "test-model")

    settings = Settings()

    assert settings.adapter_host == "127.0.0.1"
    assert settings.adapter_port == 8787
    assert settings.generation_temperature == 0.7
    assert settings.generation_max_tokens == 2048
    assert settings.generation_top_p == 0.95


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_MODEL", "test-model")
    monkeypatch.setenv("ADAPTER_PORT", "9999")

    settings = Settings()

    assert settings.adapter_port == 9999


def test_missing_required_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VLLM_MODEL", raising=False)

    with pytest.raises(ValidationError):
        Settings()


def test_system_prompt_default_non_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_MODEL", "test-model")

    settings = Settings()

    assert "terminal assistant" in settings.system_prompt_template
