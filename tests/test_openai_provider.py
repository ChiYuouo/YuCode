import json

import httpx
import pytest

from mewcode.config import ProviderConfig
from mewcode.providers.base import Cancellation, Message, ProviderError, StreamCancelled
from mewcode.providers.openai import OpenAIProvider


def config() -> ProviderConfig:
    return ProviderConfig("openai", "gpt-test", "https://example.test/v1", "test-key")


def test_streams_text_and_sends_responses_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["json"] = json.loads(request.content)
        body = (
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"你"}\n\n'
            'event: response.output_text.delta\n'
            'data: {"type":"response.output_text.delta","delta":"好"}\n\n'
            'event: response.completed\n'
            'data: {"type":"response.completed"}\n\n'
        )
        return httpx.Response(200, content=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAIProvider(config(), client)

    events = list(provider.stream([Message("user", "你好")]))

    assert [event.content for event in events] == ["你", "好"]
    assert captured["url"] == "https://example.test/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["json"] == {
        "model": "gpt-test",
        "input": [{"role": "user", "content": "你好"}],
        "stream": True,
        "store": False,
        "max_output_tokens": 4096,
    }


def test_converts_sse_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: response.error\ndata: {"type":"response.error","error":{"message":"额度不足"}}\n\n'
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="额度不足"):
        list(provider.stream([Message("user", "你好")]))


def test_converts_http_error_to_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "认证失败"}})

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(ProviderError, match="认证失败"):
        list(provider.stream([Message("user", "你好")]))


def test_usage_is_emitted_from_completed_event() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            'event: response.completed\n'
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":12,"output_tokens":7}}}\n\n'
        )
        return httpx.Response(200, content=body)

    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    events = list(provider.stream([Message("user", "你好")]))

    assert events[-1].usage is not None
    assert events[-1].usage.input_tokens == 12
    assert events[-1].usage.output_tokens == 7


def test_cancelled_stream_is_not_reported_as_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = 'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"片段"}\n\n'
        return httpx.Response(200, content=body)

    cancellation = Cancellation()
    cancellation.cancel()
    provider = OpenAIProvider(config(), httpx.Client(transport=httpx.MockTransport(handler)))

    with pytest.raises(StreamCancelled):
        list(provider.stream([Message("user", "你好")], cancellation))
