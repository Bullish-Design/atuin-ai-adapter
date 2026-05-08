from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class AtuinContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    os: str | None = None
    shell: str | None = None
    distro: str | None = None
    pwd: str | None = None
    last_command: str | None = None


class AtuinConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    capabilities: list[str] = []
    user_contexts: list[str] = []
    skills: list[Any] = []
    skills_overflow: str | None = None


class AtuinChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    messages: list[dict[str, Any]]
    context: AtuinContext | None = None
    config: AtuinConfig | None = None
    invocation_id: str
    session_id: str | None = None


class AtuinTextEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str


class AtuinDoneEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str


class AtuinErrorEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str
