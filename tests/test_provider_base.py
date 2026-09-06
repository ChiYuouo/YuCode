from mewcode.providers.base import Cancellation, StreamEvent, Usage


def test_usage_event_carries_token_totals() -> None:
    usage = Usage(input_tokens=10, output_tokens=6, thinking_tokens=4)
    event = StreamEvent(kind="usage", usage=usage)

    assert event.usage == usage


def test_cancellation_calls_active_close_once_and_is_idempotent() -> None:
    calls: list[str] = []
    cancellation = Cancellation()
    cancellation.attach_close(lambda: calls.append("closed"))

    cancellation.cancel()
    cancellation.cancel()

    assert cancellation.is_cancelled is True
    assert calls == ["closed"]
