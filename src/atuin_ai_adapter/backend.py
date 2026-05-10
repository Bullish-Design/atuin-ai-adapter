from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Backend event types


@dataclass(frozen=True, slots=True)
class BackendTextDelta:
    content: str


@dataclass(frozen=True, slots=True)
class BackendToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BackendDone:
    pass


@dataclass(frozen=True, slots=True)
class BackendError:
    message: str


BackendEvent = BackendTextDelta | BackendToolCall | BackendDone | BackendError


# Exceptions


class BackendConnectionError(Exception):
    """Raised when the upstream model server is unreachable or returns an HTTP error."""


# Backend client


class BackendClient:
    def __init__(
        self,
        base_url: str,
        timeout: float,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
        )

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        top_p: float | None = None,
    ) -> AsyncIterator[BackendEvent]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if top_p is not None:
            body["top_p"] = top_p
        if tools:
            body["tools"] = tools

        try:
            async with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    snippet = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    yield BackendError(message=f"Model server returned {response.status_code}: {snippet}")
                    yield BackendDone()
                    return

                async for event in self._parse_stream(response):
                    yield event

        except httpx.HTTPError as exc:
            raise BackendConnectionError(f"Cannot reach model server at {self._base_url}") from exc

    async def _parse_stream(self, response: httpx.Response) -> AsyncIterator[BackendEvent]:
        """Parse an OpenAI-compatible SSE stream, yielding BackendEvents."""
        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            if line == "data: [DONE]":
                yield BackendDone()
                return
            if not line.startswith("data: "):
                continue

            payload = line.removeprefix("data: ")
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                yield BackendError(message="Failed to parse upstream response")
                yield BackendDone()
                return

            choices = parsed.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})

            content = delta.get("content")
            if content:
                yield BackendTextDelta(content=content)

        yield BackendDone()

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/v1/models")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
