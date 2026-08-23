"""流式模型调用测试：逐块 token/thinking、工具调用增量组装。"""
import asyncio

import httpx

from agent.llm import ModelBinding


def collect(async_generator):
    async def run():
        return [chunk async for chunk in async_generator]

    return asyncio.run(run())


class _StreamResp:
    def __init__(self, lines):
        self.lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        pass


class _StreamClient:
    captured = {}
    resp = None

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def stream(self, method, url, headers=None, json=None):
        _StreamClient.captured = {"method": method, "url": url, "headers": headers, "json": json}
        return _StreamCtx(_StreamClient.resp)


def _openai_chunks():
    return [
        'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"先想一下"}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t1","function":{"name":"read_file","arguments":"{\\"path\\":\\""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"README\\"}"}}]}}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"tool_use"}]}',
        'data: [DONE]',
    ]


def test_openai_stream_yields_tokens_thinking_and_assembles_tools(monkeypatch):
    from agent.llm import stream_llm_with_tools

    monkeypatch.setattr(httpx, "AsyncClient", _StreamClient)
    _StreamClient.resp = _StreamResp(_openai_chunks())
    _StreamClient.captured = {}

    binding = ModelBinding(model="m1", protocol="openai", base_url="https://g.example/v1", api_key="k")
    chunks = collect(stream_llm_with_tools(
        system="s", messages=[], tools=[], binding=binding,
    ))

    tokens = [c["content"] for c in chunks if c["type"] == "token"]
    thinking = [c["content"] for c in chunks if c["type"] == "thinking"]
    done = next(c for c in chunks if c["type"] == "done")

    assert tokens == ["你", "好"]
    assert thinking == ["先想一下"]
    response = done["response"]
    assert response["text"] == "你好"
    assert len(response["tool_uses"]) == 1
    tool = response["tool_uses"][0]
    assert tool["id"] == "t1"
    assert tool["name"] == "read_file"
    assert tool["input"] == {"path": "README"}
    assert response["stop_reason"] == "tool_use"


def test_openai_stream_sends_reasoning_effort_and_stream_flag(monkeypatch):
    from agent.llm import stream_llm_with_tools

    monkeypatch.setattr(httpx, "AsyncClient", _StreamClient)
    _StreamClient.resp = _StreamResp([
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
        'data: [DONE]',
    ])
    _StreamClient.captured = {}

    binding = ModelBinding(model="m1", protocol="openai", base_url="https://g.example/v1", thinking_effort="max")
    collect(stream_llm_with_tools(system="s", messages=[], tools=[], binding=binding))

    payload = _StreamClient.captured["json"]
    assert payload["stream"] is True
    assert payload["reasoning_effort"] == "max"
