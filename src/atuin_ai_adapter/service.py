from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.protocol.openai import OpenAIChatRequest
from atuin_ai_adapter.sse import done_event, error_event, text_event
from atuin_ai_adapter.translator import build_openai_messages
from atuin_ai_adapter.vllm_client import VllmClient, VllmError

logger = logging.getLogger(__name__)


async def handle_chat(
    request: AtuinChatRequest,
    vllm_client: VllmClient,
    settings: Settings,
) -> AsyncIterator[str]:
    session_id = request.session_id or str(uuid.uuid4())

    try:
        translated = build_openai_messages(request, settings.system_prompt_template)
        openai_request = OpenAIChatRequest(
            model=settings.vllm_model,
            messages=translated,
            temperature=settings.generation_temperature,
            max_tokens=settings.generation_max_tokens,
            top_p=settings.generation_top_p,
        )

        async for delta in vllm_client.stream_chat(openai_request):
            if delta:
                yield text_event(delta)

        yield done_event(session_id)
    except Exception as exc:
        logger.error("Chat handling failed invocation_id=%s error=%s", request.invocation_id, exc)
        yield error_event(str(exc) if isinstance(exc, VllmError) else "Internal adapter error")
        yield done_event(session_id)
