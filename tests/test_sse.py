from mewcode.providers.sse import decode_sse


def test_decodes_event_and_data() -> None:
    messages = list(decode_sse(["event: chunk", "data: hello", ""]))

    assert messages[0].event == "chunk"
    assert messages[0].data == "hello"


def test_joins_multiple_data_lines_and_ignores_comments() -> None:
    messages = list(decode_sse([": keepalive", "data: first", "data: second", ""]))

    assert messages[0].event == "message"
    assert messages[0].data == "first\nsecond"


def test_ignores_unfinished_frame() -> None:
    assert list(decode_sse(["data: incomplete"])) == []
