from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from atuin_ai_adapter.backend import (
    BackendClient,
    BackendConnectionError,
    BackendDone,
    BackendError,
    BackendTextDelta,
    BackendToolCall,
)
from atuin_ai_adapter.config import Settings
from atuin_ai_adapter.prompt import build_system_prompt
from atuin_ai_adapter.protocol import (
    AtuinChatRequest,
    done_event,
    error_event,
    status_event,
    text_event,
    tool_call_event,
)
from atuin_ai_adapter.tools import build_tool_registry, to_openai_tools
from atuin_ai_adapter.translator import translate_messages

logger = logging.getLogger(__name__)


async def handle_chat(
    request: AtuinChatRequest,
    backend: BackendClient,
    settings: Settings,
) -> AsyncIterator[str]:
    session_id = request.session_id or str(uuid.uuid4())

    try:
        if settings.enable_tools and request.config:
            registry = build_tool_registry(request.config.capabilities)
            openai_tools = to_openai_tools(registry) or None
        else:
            registry = []
            openai_tools = None

        system_prompt = build_system_prompt(
            context=request.context,
            config=request.config,
            tools=registry,
            base_prompt=settings.system_prompt_template,
        )

        flatten = not settings.enable_tools
        openai_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *translate_messages(request.messages, flatten_tools=flatten),
        ]

        yield status_event("Thinking")

        async for event in backend.stream_chat(
            messages=openai_messages,
            model=settings.vllm_model,
            tools=openai_tools,
            temperature=settings.generation_temperature,
            max_tokens=settings.generation_max_tokens,
            top_p=settings.generation_top_p,
        ):
            match event:
                case BackendTextDelta(content=content):
                    yield text_event(content)
                case BackendToolCall(id=tc_id, name=name, arguments=args):
                    yield tool_call_event(tc_id, name, args)
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
