from __future__ import annotations

from pydantic import BaseModel


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatRequest(BaseModel):
    model: str
    messages: list[OpenAIChatMessage]
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
