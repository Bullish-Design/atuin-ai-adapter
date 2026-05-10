from __future__ import annotations

import json
import logging
from typing import Any

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


def translate_messages(
    messages: list[dict[str, Any]],
    *,
    flatten_tools: bool = False,
) -> list[dict[str, Any]]:
    if flatten_tools:
        return _translate_flattened(messages)
    return _translate_structured(messages)


def _translate_flattened(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = flatten_content_blocks(msg.get("content", ""))
        result.append({"role": role, "content": content})
    return result


def _translate_structured(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")

        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue

        if not isinstance(content, list):
            logger.warning("Unexpected content type: %s", type(content).__name__)
            result.append({"role": role, "content": str(content)})
            continue

        if role == "assistant":
            result.extend(_translate_assistant_blocks(content))
        elif role == "user":
            result.extend(_translate_user_blocks(content))
        else:
            result.append({"role": role, "content": flatten_content_blocks(content)})

    return result


def _translate_assistant_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []

    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(str(block.get("text", "")))
        elif block_type == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    },
                }
            )
        else:
            dumped = json.dumps(block, ensure_ascii=False)
            logger.warning("Unknown assistant content block type: %s", block_type)
            text_parts.append(f"[Unknown block (type={block_type}): {dumped}]")

    msg: dict[str, Any] = {"role": "assistant"}
    msg["content"] = "\n\n".join(text_parts) if text_parts else None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return [msg]


def _translate_user_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for block in blocks:
        block_type = block.get("type")
        if block_type == "tool_result":
            result.append(
                {
                    "role": "tool",
                    "tool_call_id": block.get("tool_use_id", ""),
                    "content": str(block.get("content", "")),
                }
            )
        elif block_type == "text":
            text_parts.append(str(block.get("text", "")))
        else:
            dumped = json.dumps(block, ensure_ascii=False)
            logger.warning("Unknown user content block type: %s", block_type)
            text_parts.append(f"[Unknown block (type={block_type}): {dumped}]")

    if text_parts:
        result.append({"role": "user", "content": "\n\n".join(text_parts)})

    return result
