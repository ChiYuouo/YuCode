"""兼容模型常见文本包装的结构化输出解析。"""

from __future__ import annotations

import json
import re
from typing import Any


_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n?```", re.IGNORECASE | re.DOTALL)


class ModelOutputParseError(ValueError):
    """模型输出中不存在唯一、完整的目标 JSON。"""


def parse_json_object(output: str) -> dict[str, Any]:
    """从模型文本中提取唯一 JSON 对象，并保持严格校验。"""
    value = parse_json_value(output)
    if not isinstance(value, dict):
        raise ModelOutputParseError("模型返回的 JSON 顶层必须是对象。")
    return value


def parse_json_value(output: str) -> Any:
    """接受纯 JSON、单个 JSON 代码块或自然语言包装的唯一 JSON 值。"""
    text = output.strip()
    if not text:
        raise ModelOutputParseError("模型未返回内容。")

    fenced = _FENCED_JSON_RE.findall(text)
    if fenced:
        if len(fenced) != 1:
            raise ModelOutputParseError("模型返回了多个 JSON 代码块。")
        return _parse_complete(fenced[0])

    decoder = json.JSONDecoder()
    candidate: tuple[Any, int] | None = None
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        candidate = (value, index + end)
        break
    if candidate is None:
        raise ModelOutputParseError("模型未返回有效 JSON。")

    value, end = candidate
    if _contains_another_json_value(text[end:], decoder):
        raise ModelOutputParseError("模型返回了多个 JSON 值。")
    return value


def _parse_complete(text: str) -> Any:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as error:
        raise ModelOutputParseError("JSON 代码块内容无效或不完整。") from error


def _contains_another_json_value(text: str, decoder: json.JSONDecoder) -> bool:
    for index, character in enumerate(text):
        if character not in "{[":
            continue
        try:
            decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return True
    return False
