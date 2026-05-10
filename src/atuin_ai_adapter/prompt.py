from __future__ import annotations

from atuin_ai_adapter.protocol import AtuinConfig, AtuinContext
from atuin_ai_adapter.tools import ToolDefinition


def build_system_prompt(
    context: AtuinContext | None,
    config: AtuinConfig | None,
    tools: list[ToolDefinition],
    base_prompt: str,
) -> str:
    sections: list[str] = [base_prompt]

    env_section = _build_environment_section(context)
    if env_section:
        sections.append(env_section)

    tool_section = _build_tool_section(tools)
    if tool_section:
        sections.append(tool_section)

    skill_section = _build_skill_section(config, tools)
    if skill_section:
        sections.append(skill_section)

    user_section = _build_user_context_section(config)
    if user_section:
        sections.append(user_section)

    return "\n\n".join(sections)


def _build_environment_section(context: AtuinContext | None) -> str | None:
    if context is None:
        return None

    lines: list[str] = []
    field_map = [
        ("OS", context.os),
        ("Shell", context.shell),
        ("Distribution", context.distro),
        ("Working directory", context.pwd),
        ("Last command", context.last_command),
    ]
    for label, value in field_map:
        if value:
            lines.append(f"- {label}: {value}")

    if not lines:
        return None

    return "## Environment\n" + "\n".join(lines)


def _build_tool_section(tools: list[ToolDefinition]) -> str | None:
    if not tools:
        return None

    tool_lines = [f"- {tool.name}: {tool.description}" for tool in tools]

    guidelines = [
        "- When the user asks for a command, use suggest_command rather than just writing it in text.",
        "- Use read_file before edit_file to understand current file contents.",
        "- Prefer suggest_command over execute_shell_command when the user should review first.",
        '- For dangerous operations, set danger to "high" and include a warning.',
    ]

    tool_names = {t.name for t in tools}
    filtered_guidelines = []
    guideline_tool_deps = {
        0: {"suggest_command"},
        1: {"read_file", "edit_file"},
        2: {"suggest_command", "execute_shell_command"},
        3: {"suggest_command"},
    }
    for i, guideline in enumerate(guidelines):
        if guideline_tool_deps.get(i, set()) <= tool_names:
            filtered_guidelines.append(guideline)

    section = "## Available tools\nYou have the following tools available. Use them when appropriate:\n"
    section += "\n".join(tool_lines)

    if filtered_guidelines:
        section += "\n\n## Guidelines\n" + "\n".join(filtered_guidelines)

    return section


def _build_skill_section(config: AtuinConfig | None, tools: list[ToolDefinition]) -> str | None:
    if config is None or not config.skills:
        return None

    tool_names = {t.name for t in tools}
    if "load_skill" not in tool_names:
        return None

    lines = [f"- {skill.name}: {skill.description}" for skill in config.skills]

    section = "## Available skills\n"
    section += "The user has the following skills installed. Use load_skill to load the full content when relevant:\n"
    section += "\n".join(lines)

    if config.skills_overflow:
        section += f"\n\nAdditional skills not shown: {config.skills_overflow}"

    return section


def _build_user_context_section(config: AtuinConfig | None) -> str | None:
    if config is None or not config.user_contexts:
        return None

    return "## User preferences\n" + "\n".join(f"- {ctx}" for ctx in config.user_contexts)
