"""模型绑定测试：显式 ModelBinding 优先于环境变量，runtime 在任务启动时冻结模型。"""
import asyncio

import httpx

from agent.runtime.registry import ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def test_capture_model_binding_snapshots_environment(monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_MODEL", "model-x")
    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "secret-key")

    binding = llm.capture_model_binding()

    assert binding.model == "model-x"
    assert binding.protocol == "openai"
    assert binding.base_url == "https://gateway.example/v1"
    assert binding.api_key == "secret-key"


class _Resp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _BoundOpenAIClient:
    captured = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, headers=None, json=None):
        _BoundOpenAIClient.captured.update(url=url, headers=headers, json=json)
        return _Resp({
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        })


def test_openai_call_uses_binding_over_environment(monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    monkeypatch.setattr(httpx, "AsyncClient", _BoundOpenAIClient)
    _BoundOpenAIClient.captured = {}

    binding = llm.ModelBinding(
        model="bound-model",
        protocol="openai",
        base_url="https://bound.example/v1",
        api_key="bound-key",
    )
    result = asyncio.run(llm.call_llm_with_tools(
        system="s", messages=[], tools=[], binding=binding,
    ))

    captured = _BoundOpenAIClient.captured
    assert captured["url"] == "https://bound.example/v1/chat/completions"
    assert captured["json"]["model"] == "bound-model"
    assert captured["headers"]["Authorization"] == "Bearer bound-key"
    assert "env-model" not in str(captured["json"])
    assert result["text"] == "OK"


def test_anthropic_call_uses_binding_over_environment(monkeypatch):
    import agent.llm as llm

    captured = {}

    class _TextBlock:
        type = "text"

        def __init__(self, text):
            self.text = text

    class _Messages:
        async def create(self, **kwargs):
            captured["params"] = kwargs
            return type("R", (), {
                "content": [_TextBlock("hi")],
                "stop_reason": "end_turn",
                "usage": type("U", (), {"input_tokens": 1, "output_tokens": 1})(),
            })()

    class _Client:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = _Messages()

        async def close(self):
            pass

    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://env.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", _Client)

    binding = llm.ModelBinding(
        model="bound-model",
        protocol="anthropic",
        base_url="https://bound.example",
        api_key="bound-key",
    )
    asyncio.run(llm.call_llm_with_tools(
        system="s", messages=[], tools=[], binding=binding,
    ))

    assert captured["client_kwargs"]["api_key"] == "bound-key"
    assert captured["client_kwargs"]["base_url"] == "https://bound.example"
    assert captured["params"]["model"] == "bound-model"


def _runtime_with_capturing_llm(tmp_path, monkeypatch, captured):
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.example")
    monkeypatch.setenv("LLM_API_KEY", "env-key")

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda sid: ToolRegistry(), fake_llm)
    return runtime


def test_runtime_freezes_model_binding_at_task_start(tmp_path, monkeypatch):
    captured = {}
    runtime = _runtime_with_capturing_llm(tmp_path, monkeypatch, captured)

    events = collect(runtime.run("session", "查看项目结构"))
    done = [event for event in events if event["type"] == "done"]

    assert done and done[-1]["status"] == "completed"
    binding = captured.get("binding")
    assert binding is not None
    assert binding.model == "env-model"
    assert binding.protocol == "anthropic"
    assert binding.base_url == "https://env.example"
    assert binding.api_key == "env-key"


def test_runtime_binding_prefers_task_model_context_identity(tmp_path, monkeypatch):
    captured = {}
    runtime = _runtime_with_capturing_llm(tmp_path, monkeypatch, captured)

    events = collect(runtime.run(
        "session",
        "查看项目结构",
        model_context={
            "id": "ctx-model",
            "protocol": "openai",
            "base_url": "https://ctx.example/v1",
        },
    ))
    done = [event for event in events if event["type"] == "done"]

    assert done and done[-1]["status"] == "completed"
    binding = captured.get("binding")
    assert binding is not None
    assert binding.model == "ctx-model"
    assert binding.protocol == "openai"
    assert binding.base_url == "https://ctx.example/v1"
    # api_key 只来自环境，不写入任务记录
    assert binding.api_key == "env-key"


def test_runtime_releases_binding_after_terminal_state(tmp_path, monkeypatch):
    captured = {}
    runtime = _runtime_with_capturing_llm(tmp_path, monkeypatch, captured)

    events = collect(runtime.run("session", "查看项目结构"))
    done = [event for event in events if event["type"] == "done"]

    assert done[-1]["status"] == "completed"
    task_id = done[-1]["task_id"]
    assert task_id not in runtime._model_bindings
