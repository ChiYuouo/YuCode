"""最小且可测试的 Server-Sent Events 解码器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True)
class SSEMessage:
    """一帧已经解码的 SSE 消息。"""

    event: str
    data: str


def decode_sse(lines: Iterable[str]) -> Iterator[SSEMessage]:
    """从 ``httpx.Response.iter_lines`` 的输出中逐帧解析 SSE。

    SSE 以空行结束一帧，多个 ``data:`` 行需要用换行拼接。连接在
    一帧中途结束时不产出不完整事件，避免把损坏 JSON 交给 Provider。
    """
    event = "message"
    data_lines: list[str] = []

    for line in lines:
        if line == "":
            if data_lines:
                yield SSEMessage(event=event, data="\n".join(data_lines))
            event = "message"
            data_lines = []
            continue

        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]

        if field == "event":
            event = value
        elif field == "data":
            data_lines.append(value)
