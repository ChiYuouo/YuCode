"""纯文本输入解析。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class InputKind(str, Enum):
    EMPTY = "empty"
    CHAT = "chat"
    COMMAND = "command"


@dataclass(frozen=True)
class ParsedInput:
    kind: InputKind
    text: str
    name: str = ""
    arguments: str = ""


def parse_input(raw: str) -> ParsedInput:
    text = raw.strip()
    if not text:
        return ParsedInput(InputKind.EMPTY, "")
    if not text.startswith("/"):
        return ParsedInput(InputKind.CHAT, text)
    remainder = text[1:]
    split_at = next((index for index, char in enumerate(remainder) if char.isspace()), len(remainder))
    return ParsedInput(InputKind.COMMAND, text, remainder[:split_at].lower(), remainder[split_at:].strip())
