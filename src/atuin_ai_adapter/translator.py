from __future__ import annotations

import json
import logging
from typing import Any

from atuin_ai_adapter.protocol.atuin import AtuinChatRequest
from atuin_ai_adapter.protocol.openai import OpenAIChatMessage

logger = logging.getLogger(__name__)


def flatten_content_blocks(content: str | list[dict[str, Any]] | Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        rendered: list[str] = []
        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                rendered.append(str(block.get("text", "")))
            elif block_type == "tool_use":
                name = block.get("name", "unknown_tool")
                tool_input = json.dumps(block.get("input", {}), ensure_ascii=False)
                rendered.append(f"[Tool call: {name}({tool_input})]")
            elif block_type == "tool_result":
                tool_use_id = block.get("tool_use_id", "unknown")
                tool_content = str(block.get("content", ""))
                if block.get("is_error"):
                    rendered.append(f"[Tool error ({tool_use_id}): {tool_content}]")
                else:
                    rendered.append(f"[Tool result ({tool_use_id}): {tool_content}]")
            else:
                dumped = json.dumps(block, ensure_ascii=False)
                logger.warning("Unknown content block type: %s", block_type)
                rendered.append(f"[Unknown block: {dumped}]")
        return "\n\n".join(rendered)

    logger.warning("Unexpected content type: %s", type(content).__name__)
    return str(content)


def build_openai_messages(request: AtuinChatRequest, system_prompt_template: str) -> list[OpenAIChatMessage]:
    body_lines: list[str] = []

    if request.context is not None:
        env_lines: list[str] = []
        field_map = [
            ("OS", request.context.os),
            ("Shell", request.context.shell),
            ("Distribution", request.context.distro),
            ("Working directory", request.context.pwd),
            ("Last command", request.context.last_command),
        ]
        for label, value in field_map:
            if value:
                env_lines.append(f"- {label}: {value}")
        if env_lines:
            body_lines.append("Environment:")
            body_lines.extend(env_lines)

    if request.config is not None and request.config.user_contexts:
        if body_lines:
            body_lines.append("")
        body_lines.append("User context:")
        body_lines.extend(request.config.user_contexts)

    system_content = system_prompt_template
    if body_lines:
        system_content = f"{system_prompt_template}\n\n" + "\n".join(body_lines)

    translated: list[OpenAIChatMessage] = [OpenAIChatMessage(role="system", content=system_content)]
    for message in request.messages:
        role = str(message.get("role", "user"))
        content = flatten_content_blocks(message.get("content", ""))
        translated.append(OpenAIChatMessage(role=role, content=content))

    return translated
