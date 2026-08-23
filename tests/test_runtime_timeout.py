"""工具执行超时兜底测试：挂起的工具不能卡死整条任务。"""
import asyncio
import time
from pathlib import Path

from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.registry import ToolDefinition, ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def _make_runtime(tmp_path: Path, responses: list[dict], handler, timeout: float):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="slow_tool",
            description="Slow tool",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ,
            handler=handler,
        ))
        return registry

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm, tool_execution_timeout=timeout)


def test_runtime_times_out_hanging_tool(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "t1", "name": "slow_tool", "input": {}}], "stop_reason": "tool_use"},
        {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    def slow_handler(args):
        time.sleep(5)
        return ToolResult.ok(output="done")

    runtime = _make_runtime(tmp_path, responses, slow_handler, timeout=0.2)
    events = collect(runtime.run("session", "查看"))

    results = [event for event in events if event["type"] == "tool_result"]
    assert results, "应产生工具结果事件"
    assert results[0]["success"] is False
    assert results[0]["error_kind"] == "timeout"
    assert "超时" in results[0]["error"]

    # 超时后任务仍能继续并正常收尾
    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] in {"completed", "incomplete"}


def test_runtime_does_not_time_out_fast_tool(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "t1", "name": "slow_tool", "input": {}}], "stop_reason": "tool_use"},
        {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    def fast_handler(args):
        return ToolResult.ok(output="fast")

    runtime = _make_runtime(tmp_path, responses, fast_handler, timeout=5)
    events = collect(runtime.run("session", "查看"))

    results = [event for event in events if event["type"] == "tool_result"]
    assert results and results[0]["success"] is True
    assert results[0]["error_kind"] is None
