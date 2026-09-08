import asyncio

from yucode.providers.sse import decode_sse


async def lines(*values: str):
    for value in values:
        yield value


async def collect(*values: str):
    return [message async for message in decode_sse(lines(*values))]


def test_decodes_event_and_data() -> None:
    messages = asyncio.run(collect("event: chunk", "data: hello", ""))

    assert messages[0].event == "chunk"
    assert messages[0].data == "hello"


def test_joins_multiple_data_lines_and_ignores_comments() -> None:
    messages = asyncio.run(collect(": keepalive", "data: first", "data: second", ""))

    assert messages[0].event == "message"
    assert messages[0].data == "first\nsecond"


def test_ignores_unfinished_frame() -> None:
    assert asyncio.run(collect("data: incomplete")) == []
