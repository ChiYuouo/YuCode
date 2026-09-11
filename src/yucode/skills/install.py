"""从用户明确提供的直接 URL 安全安装 Skill。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse
import zipfile

import httpx
import yaml

from yucode.skills.loader import SkillLoader


MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 128


@dataclass(frozen=True)
class InstallResult:
    name: str
    target: Path
    already_installed: bool = False


class SkillInstaller:
    def __init__(self, user_root: Path) -> None:
        self._user_root = user_root.resolve()

    async def install(self, url: str) -> InstallResult:
        resolved_url = _resolve_skills_sh_url(url)
        parsed = urlparse(resolved_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Skill URL 必须是完整的 http 或 https 地址。")
        async with httpx.AsyncClient(follow_redirects=True, max_redirects=3, timeout=20.0) as client:
            response = await client.get(resolved_url)
            response.raise_for_status()
            data = response.content
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValueError("下载的 Skill 超过大小限制。")
        self._user_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="yucode-skill-", dir=self._user_root) as temporary:
            stage = Path(temporary) / "candidate"
            stage.mkdir()
            if data.startswith(b"PK\x03\x04"):
                self._extract_zip(data, stage)
            else:
                (stage / "download.md").write_text(_normalize_external_markdown(data), encoding="utf-8")
            catalog = SkillLoader(Path(temporary) / "project", stage, Path(temporary) / "builtin").discover()
            if len(catalog.definitions) != 1:
                detail = catalog.diagnostics[0].message if catalog.diagnostics else "下载内容必须恰好包含一个有效 Skill。"
                raise ValueError(f"无法安装 Skill：{detail}")
            skill = next(iter(catalog.definitions.values()))
            entry = skill.source.entry_path
            package = skill.source.package_root
            is_package = entry.name == "SKILL.md"
            target = self._user_root / (skill.name if is_package else f"{skill.name}.md")
            if target.exists():
                # 重复点击安装不是错误：不覆盖用户内容，交由运行时复用并激活现有版本。
                return InstallResult(skill.name, target, already_installed=True)
            source = package if is_package else entry
            await asyncio.to_thread(os.replace, source, target)
            return InstallResult(skill.name, target)

    @staticmethod
    def _extract_zip(data: bytes, target: Path) -> None:
        import io

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ValueError("Skill 压缩包文件数量超过限制。")
            for member in members:
                relative = Path(member.filename)
                if member.is_dir():
                    continue
                if relative.is_absolute() or ".." in relative.parts or member.is_dir() or member.external_attr >> 16 & 0o170000 == 0o120000:
                    raise ValueError("Skill 压缩包包含不安全路径。")
                destination = (target / relative).resolve()
                try:
                    destination.relative_to(target.resolve())
                except ValueError as error:
                    raise ValueError("Skill 压缩包包含越界路径。") from error
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))
        # zip 根直接放 SKILL.md 时包装为一个目录，满足目录包发现规则。
        entry = target / "SKILL.md"
        if entry.is_file():
            package = target / "package"
            package.mkdir()
            for child in tuple(target.iterdir()):
                if child != package:
                    shutil.move(str(child), package / child.name)


def _resolve_skills_sh_url(url: str) -> str:
    """skills.sh 页面只做展示，转换为官方仓库中的原始 SKILL.md。"""
    parsed = urlparse(url)
    if parsed.netloc.lower() != "www.skills.sh":
        return url
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 3:
        return url
    owner, repository, skill = parts
    return f"https://raw.githubusercontent.com/{owner}/{repository}/main/skills/{skill}/SKILL.md"


def _normalize_external_markdown(data: bytes) -> str:
    """把标准 SKILL.md 导入为 YuCode 可执行格式，原正文不改写。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("下载的 Skill 不是 UTF-8 Markdown。") from error
    if not text.startswith("---"):
        return text
    lines = text.splitlines(keepends=True)
    end = next((index for index in range(1, len(lines)) if lines[index].strip() == "---"), None)
    if end is None:
        return text
    metadata = yaml.safe_load("".join(lines[1:end]))
    if not isinstance(metadata, dict):
        return text
    metadata.setdefault("allowedTools", ["read_file", "write_file", "edit_file", "run_command", "find_files", "search_code"])
    metadata.setdefault("mode", "inline")
    metadata.setdefault("history", "all")
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n{''.join(lines[end + 1:])}"
