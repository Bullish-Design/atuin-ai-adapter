from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from atuin_ai_adapter.config import Settings, get_settings
from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.service import handle_chat
from atuin_ai_adapter.vllm_client import VllmClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    vllm_client = VllmClient(base_url=settings.vllm_base_url, timeout=settings.vllm_timeout)
    app.state.settings = settings
    app.state.vllm_client = vllm_client
    try:
        yield
    finally:
        await vllm_client.close()


app = FastAPI(title="Atuin AI Adapter", lifespan=lifespan)


async def verify_token(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    expected = f"Bearer {request.app.state.settings.adapter_api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")


@app.post("/api/cli/chat")
async def chat(
    chat_request: AtuinChatRequest,
    request: Request,
    _: None = Depends(verify_token),
) -> StreamingResponse:
    settings: Settings = request.app.state.settings
    vllm_client: VllmClient = request.app.state.vllm_client
    logging.getLogger(__name__).info("request invocation_id=%s", chat_request.invocation_id)
    return StreamingResponse(
        handle_chat(chat_request, vllm_client, settings),
        media_type="text/event-stream",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request):
    vllm_client: VllmClient = request.app.state.vllm_client
    if await vllm_client.health_check():
        return {"status": "ready", "upstream": "reachable"}
    return JSONResponse(
        {"status": "not_ready", "upstream": "unreachable"},
        status_code=503,
    )


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "atuin_ai_adapter.app:app",
        host=settings.adapter_host,
        port=settings.adapter_port,
        log_level=settings.log_level.lower(),
    )
