from pathlib import Path

import pytest

from yucode.config import ConfigError, load_config
from yucode.permissions import PermissionMode


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "yucode.yaml"
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


def test_loads_context_window_override(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\ncontext:\n  window_tokens: 200000\n",
    )

    assert load_config(path).context.window_tokens == 200_000


@pytest.mark.parametrize("mode", [item.value for item in PermissionMode])
def test_loads_each_permission_mode(tmp_path: Path, mode: str) -> None:
    path = write_config(
        tmp_path,
        f"protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\npermissions:\n  mode: {mode}\n",
    )
    assert load_config(path).permissions.mode.value == mode


def test_defaults_permission_mode_to_default(tmp_path: Path) -> None:
    path = write_config(tmp_path, "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\n")
    assert load_config(path).permissions.mode is PermissionMode.DEFAULT


def test_default_path_only_reads_yucode_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    content = "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\n"
    (tmp_path / ("m" + "ewcode.yaml")).write_text(content, encoding="utf-8")

    with pytest.raises(ConfigError, match="yucode.yaml"):
        load_config()

    write_config(tmp_path, content)
    assert load_config().provider.model == "x"


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
        ("protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: secret\npermissions:\n  mode: unsafe", "permissions.mode"),
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


def test_merges_user_and_project_mcp_servers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-legacy"))
    home = tmp_path / "home"
    user = home / ".yucode"
    user.mkdir(parents=True)
    (user / "yucode.yaml").write_text(
        "mcp_servers:\n  user_only:\n    transport: stdio\n    command: user\n  replaced:\n    transport: stdio\n    command: old\n",
        encoding="utf-8",
    )
    project = write_config(tmp_path, "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\nmcp_servers:\n  replaced:\n    transport: stdio\n    command: new\n  project_only:\n    transport: http\n    url: https://mcp.test\n")
    config = load_config(project, home=home)
    assert {server.name for server in config.mcp_servers} == {"user_only", "replaced", "project_only"}
    assert next(server for server in config.mcp_servers if server.name == "replaced").command == "new"


def test_user_mcp_config_falls_back_to_legacy_appdata_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """升级前放在 %APPDATA%\\YuCode 的用户级配置仍应被读取。"""
    legacy = tmp_path / "AppData" / "YuCode"
    legacy.mkdir(parents=True)
    (legacy / "yucode.yaml").write_text(
        "mcp_servers:\n  legacy_only:\n    transport: stdio\n    command: legacy\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    project = write_config(tmp_path, "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\n")
    config = load_config(project, home=tmp_path / "home")
    assert {server.name for server in config.mcp_servers} == {"legacy_only"}


def test_user_mcp_config_works_without_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非 Windows 平台没有 APPDATA，用户级配置同样必须生效。"""
    monkeypatch.delenv("APPDATA", raising=False)
    home = tmp_path / "home"
    user = home / ".yucode"
    user.mkdir(parents=True)
    (user / "yucode.yaml").write_text(
        "mcp_servers:\n  user_only:\n    transport: stdio\n    command: user\n",
        encoding="utf-8",
    )
    project = write_config(tmp_path, "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\n")
    config = load_config(project, home=home)
    assert {server.name for server in config.mcp_servers} == {"user_only"}


def test_mcp_expands_variables_and_keeps_bad_server_as_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOKEN", "expanded")
    path = write_config(tmp_path, "protocol: openai\nmodel: x\nbase_url: https://x.test\napi_key: key\nmcp_servers:\n  ok:\n    transport: http\n    url: https://mcp.test\n    headers:\n      Authorization: Bearer ${TOKEN}\n  bad:\n    transport: stdio\n    command: ''\n")
    config = load_config(path)
    assert config.mcp_servers[0].headers["Authorization"] == "Bearer expanded"
    assert config.mcp_issues[0].server_name == "bad"
