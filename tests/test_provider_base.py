import asyncio

from mewcode.providers.base import Cancellation, StreamEvent, Usage


def test_usage_event_carries_token_totals() -> None:
    usage = Usage(input_tokens=10, output_tokens=6, thinking_tokens=4)
    event = StreamEvent(kind="usage", usage=usage)

    assert event.usage == usage


def test_cancellation_waits_and_is_idempotent() -> None:
    async def scenario() -> None:
        cancellation = Cancellation()
        waiter = asyncio.create_task(cancellation.wait())
        await asyncio.sleep(0)
        assert waiter.done() is False

        cancellation.cancel()
        cancellation.cancel()
        await waiter

        assert cancellation.is_cancelled is True
        await cancellation.wait()

    asyncio.run(scenario())
