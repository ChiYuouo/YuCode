from pathlib import Path

import pytest

from mewcode.agent import Agent
from mewcode.cli import create_provider, main
from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import Provider
from mewcode.providers.openai import OpenAIProvider


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
    (tmp_path / "mewcode.yaml").write_text(
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
    monkeypatch.setattr("mewcode.cli.ChatApp", FakeApp)
    main()

    agent, config = captured[0]
    assert isinstance(agent._provider, expected_type)
    assert agent._max_iterations == 3
    assert config.model == "test-model"
    assert len(agent._registry.definitions) == 6
