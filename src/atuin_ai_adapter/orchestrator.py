from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

from atuin_ai_adapter.backend import (
    BackendClient,
    BackendConnectionError,
    BackendDone,
    BackendError,
    BackendTextDelta,
)
from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.protocol import AtuinChatRequest, done_event, error_event, text_event
from atuin_ai_adapter.translator import build_openai_messages

logger = logging.getLogger(__name__)


async def handle_chat(
    request: AtuinChatRequest,
    backend: BackendClient,
    settings: Settings,
) -> AsyncIterator[str]:
    session_id = request.session_id or str(uuid.uuid4())

    try:
        openai_messages = build_openai_messages(request, settings.system_prompt_template)

        async for event in backend.stream_chat(
            messages=[{"role": m.role, "content": m.content} for m in openai_messages],
            model=settings.vllm_model,
            temperature=settings.generation_temperature,
            max_tokens=settings.generation_max_tokens,
            top_p=settings.generation_top_p,
        ):
            match event:
                case BackendTextDelta(content=content):
                    yield text_event(content)
                case BackendDone():
                    pass
                case BackendError(message=msg):
                    yield error_event(msg)
                    yield done_event(session_id)
                    return

        yield done_event(session_id)

    except BackendConnectionError as exc:
        logger.error("Backend connection error: %s", exc)
        yield error_event(str(exc))
        yield done_event(session_id)
    except Exception as exc:
        logger.error("Adapter error: %s", exc, exc_info=True)
        yield error_event("Internal adapter error")
        yield done_event(session_id)
