"""用户级目录统一与旧位置兼容的行为测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from yucode import userdirs


def test_user_root_is_yucode_under_home(tmp_path: Path) -> None:
    assert userdirs.user_root(tmp_path) == tmp_path / ".yucode"


def test_legacy_root_requires_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert userdirs.legacy_root() == tmp_path / "AppData" / "YuCode"
    monkeypatch.delenv("APPDATA", raising=False)
    assert userdirs.legacy_root() is None


def test_resolve_prefers_new_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "AppData" / "YuCode"
    legacy.mkdir(parents=True)
    (legacy / "skills").mkdir()
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    home = tmp_path / "home"
    (home / ".yucode" / "skills").mkdir(parents=True)
    assert userdirs.resolve_path("skills", home) == home / ".yucode" / "skills"


def test_resolve_falls_back_to_legacy_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "AppData" / "YuCode" / "skills"
    legacy.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert userdirs.resolve_path("skills", tmp_path / "home") == legacy


def test_resolve_without_appdata_returns_new_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """非 Windows 平台没有 APPDATA，用户级路径仍必须解析到新位置。"""
    monkeypatch.delenv("APPDATA", raising=False)
    expected = tmp_path / "home" / ".yucode" / "yucode.yaml"
    assert userdirs.resolve_path("yucode.yaml", tmp_path / "home") == expected


def test_resolve_returns_new_location_when_neither_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert userdirs.resolve_path("yucode.yaml", tmp_path / "home") == tmp_path / "home" / ".yucode" / "yucode.yaml"


def test_migration_warning_only_for_paths_that_are_actually_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    appdata = tmp_path / "AppData"
    monkeypatch.setenv("APPDATA", str(appdata))
    assert userdirs.migration_warnings() == ()
    (appdata / "YuCode").mkdir(parents=True)
    assert userdirs.migration_warnings() == ()
    (appdata / "YuCode" / "skills").mkdir()
    warnings = userdirs.migration_warnings()
    assert len(warnings) == 1
    assert "旧版用户级目录" in warnings[0]
    assert "skills" in warnings[0]
    assert str(userdirs.user_root()) in warnings[0]


def test_migration_warning_ignores_unrelated_leftovers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """旧目录里不被读取的残留不应触发提示，否则提示会因为无关文件永远消不掉。"""
    legacy = tmp_path / "AppData" / "YuCode"
    (legacy / "teams" / "demo").mkdir(parents=True)
    (legacy / "teams" / "demo" / "team.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert userdirs.migration_warnings() == ()


def test_migration_warning_reports_legacy_user_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "AppData" / "YuCode"
    legacy.mkdir(parents=True)
    (legacy / "yucode.yaml").write_text("mcp_servers: {}\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    warnings = userdirs.migration_warnings()
    assert len(warnings) == 1
    assert "yucode.yaml" in warnings[0]


def test_migration_warning_absent_without_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    assert userdirs.migration_warnings() == ()
