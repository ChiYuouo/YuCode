from pathlib import Path

import pytest

from mewcode.cli import create_provider, main
from mewcode.config import ProviderConfig
from mewcode.providers.anthropic import AnthropicProvider
from mewcode.providers.base import Provider
from mewcode.providers.openai import OpenAIProvider
from mewcode.tools.registry import ToolRegistry


def test_selects_provider_from_protocol() -> None:
    openai = create_provider(ProviderConfig("openai", "gpt-test", "https://x.test", "key"))
    anthropic = create_provider(
        ProviderConfig("anthropic", "claude-test", "https://x.test", "key")
    )

    assert isinstance(openai, OpenAIProvider)
    assert isinstance(anthropic, AnthropicProvider)


@pytest.mark.parametrize(
    ("protocol", "expected_type"),
    [("openai", OpenAIProvider), ("anthropic", AnthropicProvider)],
)
def test_main_loads_current_directory_config_and_starts_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, protocol: str, expected_type: type[Provider]
) -> None:
    (tmp_path / "mewcode.yaml").write_text(
        "\n".join(
            [
                f"protocol: {protocol}",
                "model: test-model",
                "base_url: https://example.test",
                "api_key: test-key",
            ]
        ),
        encoding="utf-8",
    )
    captured: list[tuple[Provider, ProviderConfig, ToolRegistry]] = []

    class FakeApp:
        def __init__(self, provider: Provider, config: ProviderConfig, registry: ToolRegistry) -> None:
            captured.append((provider, config, registry))

        def run(self) -> None:
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mewcode.cli.ChatApp", FakeApp)

    main()

    assert isinstance(captured[0][0], expected_type)
    assert captured[0][1].model == "test-model"
    assert len(captured[0][2].definitions) == 6
