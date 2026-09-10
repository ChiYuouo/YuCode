"""命令登记、查找和补全。"""

from __future__ import annotations

from collections.abc import Sequence

from yucode.commands.models import CommandDefinition, CommandRegistrationError


class CommandRegistry:
    def __init__(self, definitions: Sequence[CommandDefinition]) -> None:
        index: dict[str, CommandDefinition] = {}
        saved: list[CommandDefinition] = []
        for definition in definitions:
            keys = (definition.name, *definition.aliases)
            if not definition.name or any(not key or "/" in key or any(char.isspace() for char in key) for key in keys):
                raise CommandRegistrationError(f"命令名称无效：{definition.name!r}")
            if not definition.description or not definition.usage or definition.handler is None:
                raise CommandRegistrationError(f"命令定义不完整：{definition.name}")
            for key in keys:
                normalized = key.lower()
                if normalized in index:
                    raise CommandRegistrationError(
                        f"命令名称冲突：{key} 同时属于 {index[normalized].name} 和 {definition.name}"
                    )
                index[normalized] = definition
            saved.append(definition)
        self._definitions = tuple(saved)
        self._index = index

    def get(self, name: str) -> CommandDefinition | None:
        return self._index.get(name.lstrip("/").lower())

    def visible(self) -> tuple[CommandDefinition, ...]:
        return tuple(item for item in self._definitions if not item.hidden)

    def complete(self, prefix: str) -> tuple[CommandDefinition, ...]:
        normalized = prefix.lower()
        return tuple(
            item for item in self.visible()
            if item.name.lower().startswith(normalized) or any(alias.lower().startswith(normalized) for alias in item.aliases)
        )
