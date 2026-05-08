from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from atuin_ai_adapter.protocol.openai import OpenAIChatRequest


class VllmError(Exception):
    """Raised when the upstream vLLM server returns an error or is unreachable."""


class VllmClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self._base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def stream_chat(self, request: OpenAIChatRequest) -> AsyncIterator[str | None]:
        body = request.model_dump(exclude_none=True)
        try:
            async with self._client.stream("POST", "/v1/chat/completions", json=body) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    snippet = (await response.aread()).decode("utf-8", errors="replace")[:500]
                    raise VllmError(f"Upstream model server returned {response.status_code}: {snippet}")

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line == "data: [DONE]":
                        return
                    if not line.startswith("data: "):
                        continue
                    payload = line.removeprefix("data: ")
                    try:
                        parsed = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise VllmError("Failed to parse upstream response") from exc

                    choices = parsed.get("choices", [])
                    delta = choices[0].get("delta", {}) if choices else {}
                    yield delta.get("content")
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            raise VllmError(f"Cannot reach upstream model server at {self._base_url}") from exc

    async def health_check(self) -> bool:
        try:
            response = await self._client.get("/v1/models")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self._client.aclose()
