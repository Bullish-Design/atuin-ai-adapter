from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class ToolExecution(str, Enum):
    CLIENT = "client"
    PSEUDO = "pseudo"
    ADAPTER = "adapter"


class ToolDefinition(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    execution: ToolExecution


_SUGGEST_COMMAND = ToolDefinition(
    name="suggest_command",
    description="Suggest a shell command for the user to run or edit. Use this when the best answer is a command.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": ["string", "null"],
                "description": "The shell command to suggest",
            },
            "description": {
                "type": ["string", "null"],
                "description": "Brief description of what the command does",
            },
            "confidence": {
                "type": ["string", "null"],
                "enum": ["low", "medium", "high", None],
            },
            "danger": {
                "type": ["string", "null"],
                "enum": ["low", "medium", "high", None],
            },
            "warning": {
                "type": ["string", "null"],
                "description": "Warning message for dangerous commands",
            },
        },
        "required": ["command"],
    },
    execution=ToolExecution.PSEUDO,
)

_READ_FILE = ToolDefinition(
    name="read_file",
    description="Read the contents of a file.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {
                "type": "integer",
                "default": 100,
                "minimum": 1,
                "maximum": 1000,
            },
        },
        "required": ["file_path"],
    },
    execution=ToolExecution.CLIENT,
)

_EDIT_FILE = ToolDefinition(
    name="edit_file",
    description="Edit a file by replacing a specific string with a new string.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
    execution=ToolExecution.CLIENT,
)

_WRITE_FILE = ToolDefinition(
    name="write_file",
    description="Write content to a file. Creates the file if it doesn't exist.",
    parameters={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "content": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
        },
        "required": ["file_path", "content"],
    },
    execution=ToolExecution.CLIENT,
)

_EXECUTE_SHELL_COMMAND = ToolDefinition(
    name="execute_shell_command",
    description="Execute a shell command and return the output.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "shell": {"type": "string", "default": "bash"},
            "dir": {"type": ["string", "null"]},
            "timeout": {
                "type": "integer",
                "default": 30,
                "minimum": 1,
                "maximum": 600,
            },
            "description": {"type": ["string", "null"]},
        },
        "required": ["command"],
    },
    execution=ToolExecution.CLIENT,
)

_ATUIN_HISTORY = ToolDefinition(
    name="atuin_history",
    description="Search the user's shell command history.",
    parameters={
        "type": "object",
        "properties": {
            "filter_modes": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": ["global", "host", "session", "directory", "workspace"],
                },
            },
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 50},
        },
        "required": ["filter_modes", "query"],
    },
    execution=ToolExecution.CLIENT,
)

_LOAD_SKILL = ToolDefinition(
    name="load_skill",
    description="Load the full content of a skill by name.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string"},
        },
        "required": ["name"],
    },
    execution=ToolExecution.CLIENT,
)

_TOOL_BY_NAME: dict[str, ToolDefinition] = {
    "suggest_command": _SUGGEST_COMMAND,
    "read_file": _READ_FILE,
    "edit_file": _EDIT_FILE,
    "write_file": _WRITE_FILE,
    "execute_shell_command": _EXECUTE_SHELL_COMMAND,
    "atuin_history": _ATUIN_HISTORY,
    "load_skill": _LOAD_SKILL,
}

CAPABILITY_TOOL_MAP: dict[str, list[str]] = {
    "client_invocations": ["suggest_command"],
    "client_v1_load_skill": ["load_skill"],
    "client_v1_atuin_history": ["atuin_history"],
    "client_v1_read_file": ["read_file"],
    "client_v1_edit_file": ["edit_file"],
    "client_v1_write_file": ["write_file"],
    "client_v1_execute_shell_command": ["execute_shell_command"],
}


def build_tool_registry(capabilities: list[str]) -> list[ToolDefinition]:
    seen: set[str] = set()
    tools: list[ToolDefinition] = []
    for cap in capabilities:
        for tool_name in CAPABILITY_TOOL_MAP.get(cap, []):
            if tool_name not in seen:
                seen.add(tool_name)
                tools.append(_TOOL_BY_NAME[tool_name])
    return tools


def to_openai_tools(registry: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
        for tool in registry
    ]
