from __future__ import annotations

from atuin_ai_adapter.prompt import build_system_prompt
from atuin_ai_adapter.protocol import AtuinConfig, AtuinContext, AtuinSkillSummary
from atuin_ai_adapter.tools import build_tool_registry

BASE_PROMPT = "You are a test assistant."


def test_base_prompt_only() -> None:
    result = build_system_prompt(context=None, config=None, tools=[], base_prompt=BASE_PROMPT)
    assert result == BASE_PROMPT


def test_with_context() -> None:
    ctx = AtuinContext(os="linux", shell="zsh", pwd="/home/user")
    result = build_system_prompt(context=ctx, config=None, tools=[], base_prompt=BASE_PROMPT)
    assert "## Environment" in result
    assert "- OS: linux" in result


def test_with_tools() -> None:
    tools = build_tool_registry(["client_invocations"])
    result = build_system_prompt(context=None, config=None, tools=tools, base_prompt=BASE_PROMPT)
    assert "## Available tools" in result
    assert "suggest_command" in result


def test_with_skills() -> None:
    config = AtuinConfig(
        capabilities=["client_v1_load_skill"],
        skills=[AtuinSkillSummary(name="deploy", description="Deploy to prod")],
    )
    tools = build_tool_registry(config.capabilities)
    result = build_system_prompt(context=None, config=config, tools=tools, base_prompt=BASE_PROMPT)
    assert "## Available skills" in result
    assert "deploy: Deploy to prod" in result


def test_skills_without_load_skill_capability() -> None:
    config = AtuinConfig(
        capabilities=["client_invocations"],
        skills=[AtuinSkillSummary(name="deploy", description="Deploy")],
    )
    tools = build_tool_registry(config.capabilities)
    result = build_system_prompt(context=None, config=config, tools=tools, base_prompt=BASE_PROMPT)
    assert "## Available skills" not in result


def test_with_user_contexts() -> None:
    config = AtuinConfig(user_contexts=["Always use sudo", "Prefer fish shell"])
    result = build_system_prompt(context=None, config=config, tools=[], base_prompt=BASE_PROMPT)
    assert "## User preferences" in result


def test_skills_overflow() -> None:
    config = AtuinConfig(
        capabilities=["client_v1_load_skill"],
        skills=[AtuinSkillSummary(name="deploy", description="Deploy")],
        skills_overflow="build, test, lint",
    )
    tools = build_tool_registry(config.capabilities)
    result = build_system_prompt(context=None, config=config, tools=tools, base_prompt=BASE_PROMPT)
    assert "Additional skills not shown: build, test, lint" in result
