from pathlib import Path

import pytest

from mewcode.config import ConfigError, load_config


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "mewcode.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_loads_anthropic_config_with_thinking(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """protocol: anthropic
model: claude-test
base_url: https://api.anthropic.com/
api_key: secret-value
thinking:
  enabled: true
""",
    )

    config = load_config(path)

    assert config.provider.protocol == "anthropic"
    assert config.provider.base_url == "https://api.anthropic.com"
    assert config.provider.thinking_enabled is True
    assert config.agent.max_iterations == 10


def test_loads_agent_iteration_override(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\nagent:\n  max_iterations: 3\n",
    )

    assert load_config(path).agent.max_iterations == 3


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("protocol: unknown\nmodel: x\nbase_url: https://x.test\napi_key: secret", "protocol"),
        ("protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: secret\nthinking:\n  enabled: true", "thinking"),
        ("protocol: openai\nmodel: x\nbase_url: no-url\napi_key: secret", "base_url"),
        ("protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: ''", "api_key"),
        ("protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: secret\nagent: []", "agent"),
        ("protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: secret\nagent:\n  max_iterations: 0", "max_iterations"),
        ("protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: secret\nagent:\n  max_iterations: true", "max_iterations"),
    ],
)
def test_rejects_invalid_config_without_leaking_key(
    tmp_path: Path, content: str, message: str
) -> None:
    path = write_config(tmp_path, content)

    with pytest.raises(ConfigError, match=message) as error:
        load_config(path)

    assert "secret" not in str(error.value)


def test_requires_config_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="找不到配置文件"):
        load_config(tmp_path / "missing.yaml")
