"""模型思考（thinking）配置与展示测试：参数传递、思考块收集、SSE 事件与不落库。"""
import asyncio

import httpx

from agent.memory import Memory
from agent.runtime.registry import ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def test_capture_model_binding_reads_thinking_effort(monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_MODEL", "m1")
    monkeypatch.setenv("LLM_THINKING_EFFORT", "deep")
    monkeypatch.setenv("LLM_THINKING_BUDGET_TOKENS", "4096")

    binding = llm.capture_model_binding()

    assert binding.thinking_effort == "deep"
    assert binding.thinking_budget_tokens == 4096


class _TextBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class _ThinkingBlock:
    type = "thinking"

    def __init__(self, thinking):
        self.thinking = thinking


class _Response:
    stop_reason = "end_turn"

    def __init__(self, content):
        self.content = content
        self.usage = type("U", (), {"input_tokens": 3, "output_tokens": 2})()


class _Messages:
    def __init__(self, captured):
        self.captured = captured
        self.content = []

    async def create(self, **kwargs):
        self.captured["params"] = kwargs
        return _Response(self.content)


class _Client:
    def __init__(self, captured, content):
        self.captured = captured
        self.content = content
        self.messages = _Messages(captured)

    async def close(self):
        pass


def test_anthropic_call_passes_thinking_and_collects_blocks(monkeypatch):
    import agent.llm as llm

    captured = {}
    content = [
        _ThinkingBlock("需要先确认入口文件。"),
        _TextBlock("我来检查。"),
    ]

    class ThinkingMessages(_Messages):
        async def create(self, **kwargs):
            captured["params"] = kwargs
            return _Response(content)

    class ThinkingClient(_Client):
        def __init__(self, **kwargs):
            self.messages = ThinkingMessages(captured)

        async def close(self):
            pass

    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", ThinkingClient)

    binding = llm.ModelBinding(model="m1", protocol="anthropic", thinking_effort="high")
    result = asyncio.run(llm.call_llm_with_tools(
        system="s", messages=[], tools=[], binding=binding,
    ))

    params = captured["params"]
    assert params["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert params["temperature"] == 1
    assert result["thinking"] == "需要先确认入口文件。"
    assert result["text"] == "我来检查。"


def test_anthropic_call_default_keeps_temperature_when_thinking_off(monkeypatch):
    import agent.llm as llm

    captured = {}

    class PlainMessages(_Messages):
        async def create(self, **kwargs):
            captured["params"] = kwargs
            return _Response([_TextBlock("ok")])

    class PlainClient(_Client):
        def __init__(self, **kwargs):
            self.messages = PlainMessages(captured)

        async def close(self):
            pass

    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", PlainClient)

    binding = llm.ModelBinding(model="m1", protocol="anthropic")
    result = asyncio.run(llm.call_llm_with_tools(
        system="s", messages=[], tools=[], binding=binding, temperature=0.2,
    ))

    assert "thinking" not in captured["params"]
    assert captured["params"]["temperature"] == 0.2
    assert "thinking" not in result


class _OpenAIResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class _OpenAIClient:
    captured = {}

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, url, headers=None, json=None):
        _OpenAIClient.captured.update(url=url, headers=headers, json=json)
        return _OpenAIResp({
            "choices": [{
                "message": {
                    "content": "答案",
                    "reasoning_content": "内部推理过程",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        })


def test_openai_call_sends_reasoning_effort_and_collects_reasoning(monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setattr(httpx, "AsyncClient", _OpenAIClient)
    _OpenAIClient.captured = {}

    binding = llm.ModelBinding(
        model="reasoner", protocol="openai", base_url="https://g.example/v1", thinking_effort="max",
    )
    result = asyncio.run(llm.call_llm_with_tools(
        system="s", messages=[], tools=[], binding=binding,
    ))

    payload = _OpenAIClient.captured["json"]
    assert payload["reasoning_effort"] == "max"
    assert result["thinking"] == "内部推理过程"
    assert result["text"] == "答案"


def _make_runtime(tmp_path, captured, task_store=None, responses=None):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    queue = list(responses or [])
    recorded = {"count": 0}

    async def fake_llm(**kwargs):
        recorded["count"] += 1
        if queue:
            response = queue.pop(0)
        else:
            response = {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"}
        captured.update(kwargs)
        return response

    def registry_factory(session_id: str):
        from agent.runtime.models import ToolResult, ToolRisk
        from agent.runtime.registry import ToolDefinition
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="list_directory",
            description="List directory",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ,
            handler=lambda args: ToolResult.ok(output="src\nREADME.md"),
        ))
        return registry

    runtime = LocalAgentRuntime(workspaces, registry_factory, fake_llm, task_store=task_store)
    return runtime


def test_runtime_streams_thinking_event_and_persists_for_replay(tmp_path):
    captured = {}
    store = Memory(tmp_path / "agent.db")
    runtime = _make_runtime(tmp_path, captured, task_store=store, responses=[
        {
            "text": "",
            "thinking": "思考：先看目录。",
            "tool_uses": [{"id": "tool-1", "name": "list_directory", "input": {"path": "."}}],
            "stop_reason": "tool_use",
        },
        {"text": "目录结构已了解。", "tool_uses": [], "stop_reason": "end_turn"},
    ])

    events = collect(runtime.run("session", "查看项目"))

    thinking = [event for event in events if event["type"] == "thinking"]
    assert thinking and thinking[0]["content"] == "思考：先看目录。"
    thinking_index = next(i for i, event in enumerate(events) if event["type"] == "thinking")
    tool_index = next(i for i, event in enumerate(events) if event["type"] == "tool_call")
    assert thinking_index < tool_index

    # thinking 必须落库：supervisor.subscribe 从 SQLite 回放事件给 SSE 订阅者
    task_id = events[-1]["task_id"]
    persisted = store.get_agent_events(task_id)
    assert any(event.get("type") == "thinking" for event in persisted)
    thinking_payload = next(event for event in persisted if event.get("type") == "thinking")
    assert thinking_payload["payload"]["content"] == "思考：先看目录。"


def test_runtime_skips_thinking_when_model_returns_none(tmp_path):
    captured = {}

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"}

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    runtime = LocalAgentRuntime(workspaces, lambda sid: ToolRegistry(), fake_llm)

    events = collect(runtime.run("session", "查看项目"))

    assert all(event["type"] != "thinking" for event in events)
    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] == "completed"


def test_runtime_binding_honors_task_thinking_effort(tmp_path):
    captured = {}
    runtime = _make_runtime(tmp_path, captured, responses=[
        {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ])

    events = collect(runtime.run(
        "session",
        "查看项目",
        model_context={"id": "m1", "protocol": "anthropic", "thinking_effort": "deep"},
    ))

    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] == "completed"
    binding = captured["binding"]
    assert binding.model == "m1"
    assert binding.thinking_effort == "deep"


def test_runtime_binding_keeps_model_effort_when_task_omits_it(tmp_path, monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_THINKING_EFFORT", "on")
    captured = {}
    runtime = _make_runtime(tmp_path, captured, responses=[
        {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ])

    events = collect(runtime.run("session", "查看项目"))
    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] == "completed"
    assert captured["binding"].thinking_effort == "on"


def test_public_model_exposes_thinking_effort_and_apply_sets_env(monkeypatch):
    import model_config

    cfg = {
        "id": "custom-x",
        "name": "X",
        "model": "x-model",
        "protocol": "anthropic",
        "icon": "X",
        "color": "#000",
        "tags": [],
        "api_key": "",
        "base_url": "",
        "thinking_effort": "max",
    }
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {"custom-x": cfg})

    assert model_config.public_model(cfg)["thinking_effort"] == "max"
    assert model_config.apply_model("custom-x") is True
    assert __import__("os").environ["LLM_THINKING_EFFORT"] == "max"
