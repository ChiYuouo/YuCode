from pathlib import Path

import pytest

from yucode.agent import Agent
from yucode.cli import create_provider, main
from yucode.config import ProviderConfig
from yucode.providers.anthropic import AnthropicProvider
from yucode.providers.base import Provider
from yucode.providers.openai import OpenAIProvider


def test_selects_provider_from_protocol() -> None:
    assert isinstance(create_provider(ProviderConfig("openai", "gpt", "https://x.test", "key")), OpenAIProvider)
    assert isinstance(create_provider(ProviderConfig("anthropic", "claude", "https://x.test", "key")), AnthropicProvider)


@pytest.mark.parametrize(
    ("protocol", "expected_type"),
    [("openai", OpenAIProvider), ("anthropic", AnthropicProvider)],
)
def test_main_builds_agent_with_configured_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protocol: str, expected_type: type[Provider]
) -> None:
    (tmp_path / "yucode.yaml").write_text(
        f"protocol: {protocol}\nmodel: test-model\nbase_url: https://example.test\napi_key: test-key\nagent:\n  max_iterations: 3\n",
        encoding="utf-8",
    )
    captured: list[tuple[Agent, ProviderConfig]] = []

    class FakeApp:
        def __init__(self, agent: Agent, config: ProviderConfig) -> None:
            captured.append((agent, config))

        def run(self) -> None:
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("yucode.cli.ChatApp", FakeApp)
    main()

    agent, config = captured[0]
    assert isinstance(agent._provider, expected_type)
    assert agent._max_iterations == 3
    assert config.model == "test-model"
    assert len(agent._registry.definitions) == 6
    assert agent.session_manager.active_session_id is not None
    assert len(list((tmp_path / ".yucode" / "sessions").glob("*.jsonl"))) == 1


def test_main_loads_project_instructions_into_agent_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "yucode.yaml").write_text(
        "protocol: openai\nmodel: test\nbase_url: https://example.test\napi_key: key\n",
        encoding="utf-8",
    )
    (tmp_path / "YUCODE.md").write_text("项目规则：优先测试", encoding="utf-8")
    captured: list[Agent] = []

    class FakeApp:
        def __init__(self, agent: Agent, _config: ProviderConfig) -> None:
            captured.append(agent)

        def run(self) -> None:
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("yucode.cli.ChatApp", FakeApp)
    main()

    request = captured[0]._prompt_builder.build(
        __import__("yucode.prompting", fromlist=["RuntimeContext"]).RuntimeContext(tmp_path, "full", 1), (), ()
    )
    assert "项目规则：优先测试" in request.stable_instructions
