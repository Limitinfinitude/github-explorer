"""终态仲裁：先失败后成功的验证不应判未完成（fastapi 卡点回归）。"""
import asyncio
from pathlib import Path

from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.registry import ToolDefinition, ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def _runtime(tmp_path, responses, handler):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="wait_http",
            description="Wait HTTP",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ,
            handler=handler,
        ))
        return registry

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm)


def test_late_success_overrides_earlier_verification_failure(tmp_path):
    """wait_http 先超时失败、后成功：终态应 completed 而非 incomplete。"""
    responses = [
        # 第一轮：wait_http 失败
        {"text": "", "tool_uses": [{"id": "t1", "name": "wait_http", "input": {"url": "http://127.0.0.1:8011/"}}], "stop_reason": "tool_use"},
        # 第二轮：wait_http 成功
        {"text": "", "tool_uses": [{"id": "t2", "name": "wait_http", "input": {"url": "http://127.0.0.1:8011/"}}], "stop_reason": "tool_use"},
        # 收尾
        {"text": "服务已就绪。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    calls = {"n": 0}

    def handler(args):
        calls["n"] += 1
        if calls["n"] == 1:
            return ToolResult.fail("等待 HTTP 服务超时", error_kind="tool_error", data={
                "verification": [{"kind": "http", "success": False, "cwd": str(tmp_path / "project")}],
            })
        return ToolResult.ok(output="HTTP 服务已就绪: 200", data={
            "verification": [{"kind": "http", "success": True, "cwd": str(tmp_path / "project")}],
        })

    runtime = _runtime(tmp_path, responses, handler)
    events = collect(runtime.run("session", "查看服务"))

    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] == "completed", f"应 completed，实际 {done[-1]['status']}"


def test_result_success_overrides_unrelated_tool_failure(tmp_path):
    """rich 场景：验证成功 + 无关的 search_text 失败 → 应 completed（结果优先）。"""
    responses = [
        {"text": "", "tool_uses": [{"id": "t1", "name": "wait_http", "input": {"url": "http://x"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "t2", "name": "wait_http", "input": {"url": "http://x"}}], "stop_reason": "tool_use"},
        {"text": "测试通过。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    def handler(args):
        # 第一次 wait_http 失败（无关探索），第二次同工具成功（核心验证）
        if not hasattr(handler, "n"):
            handler.n = 0
        handler.n += 1
        if handler.n == 1:
            return ToolResult.fail("搜索目录不存在: poetry.lock", error_kind="tool_error")
        return ToolResult.ok(output="HTTP 服务已就绪: 200", data={"url": "http://x", "status_code": 200})

    runtime = _runtime(tmp_path, responses, handler)
    events = collect(runtime.run("session", "检查测试状态"))

    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] == "completed", f"结果成功时非关键失败不应判死，实际 {done[-1]['status']}"


def test_unrecovered_http_failure_stays_incomplete(tmp_path):
    """验证失败未被后续成功覆盖时，仍应 incomplete（防误报）。"""
    responses = [
        {"text": "", "tool_uses": [{"id": "t1", "name": "wait_http", "input": {"url": "http://127.0.0.1:8011/"}}], "stop_reason": "tool_use"},
        {"text": "服务未就绪。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    def handler(args):
        return ToolResult.fail("等待 HTTP 服务超时", error_kind="tool_error", data={
            "verification": [{"kind": "http", "success": False, "cwd": str(tmp_path / "project")}],
        })

    runtime = _runtime(tmp_path, responses, handler)
    events = collect(runtime.run("session", "查看服务"))

    done = [event for event in events if event["type"] == "done"]
    assert done and done[-1]["status"] == "incomplete", "无后续成功时应保持 incomplete"
