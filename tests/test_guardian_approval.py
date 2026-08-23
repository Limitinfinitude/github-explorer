"""Guardian AI 审批档（approval_mode="guardian"）行为测试。

对齐 Codex guardian/ 设计：需要确认的高风险工具由 AI 审查决定放行/拒绝；
fail-closed（超时/失败/畸形输出一律拒绝）；连续拒绝达上限熔断降级人工审批。

注意：approval_guardian / approval_guardian_breaker 事件通过 _record_event 持久化
（不 yield 给流），因此用 task_store 断言；approval_required 是 yield 的，用 collect 断言。
"""
import asyncio
from pathlib import Path

from agent.memory import Memory
from agent.runtime.registry import ToolDefinition, ToolRegistry, ToolRisk
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager
from agent.runtime.models import ToolResult


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def make_runtime(tmp_path: Path, responses: list[dict], definition: ToolDefinition):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(definition)
        return registry

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm, task_store=store), store


def _delete_definition(calls: list):
    return ToolDefinition(
        name="delete_path",
        description="Delete path",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: calls.append(args) or ToolResult.ok(output="deleted"),
    )


def _persisted_types(store, task_id: str) -> list[str]:
    return [event["type"] for event in store.get_agent_events(task_id)]


def test_guardian_approves_safe_tool_call(tmp_path: Path):
    """guardian 审查批准时：工具执行，且持久化 approval_guardian approved 事件。"""
    calls = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "delete_path", "input": {"path": "old"}}],
            "stop_reason": "tool_use",
        },
        # guardian 审查响应（approved）
        {"text": '{"decision": "approved", "rationale": "safe"}', "tool_uses": [], "stop_reason": "end_turn"},
        # 主循环下一轮（工具执行后模型总结）
        {"text": "删除完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    runtime, store = make_runtime(tmp_path, responses, _delete_definition(calls))

    events = collect(runtime.run("session", "删除 old 目录", approval_mode="guardian"))

    assert calls == [{"path": "old"}]  # 工具确实执行了
    assert events[-1]["status"] == "completed"
    task_id = events[0]["task_id"]
    persisted = _persisted_types(store, task_id)
    assert "approval_guardian" in persisted
    guardian_events = [e for e in store.get_agent_events(task_id) if e["type"] == "approval_guardian"]
    assert guardian_events and guardian_events[0]["payload"]["approved"] is True


def test_guardian_denies_dangerous_tool_call(tmp_path: Path):
    """guardian 审查拒绝时：fail-closed，工具不执行，拒绝结果反馈给模型。"""
    calls = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "delete_path", "input": {"path": "old"}}],
            "stop_reason": "tool_use",
        },
        # guardian 审查响应（denied）
        {"text": '{"decision": "denied", "rationale": "unsafe"}', "tool_uses": [], "stop_reason": "end_turn"},
        # 主循环下一轮（模型看到拒绝结果后总结）
        {"text": "操作被拒绝。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    runtime, store = make_runtime(tmp_path, responses, _delete_definition(calls))

    events = collect(runtime.run("session", "删除 old 目录", approval_mode="guardian"))

    assert calls == []  # 工具未执行（fail-closed）
    task_id = events[0]["task_id"]
    persisted = _persisted_types(store, task_id)
    assert "approval_guardian" in persisted
    guardian_events = [e for e in store.get_agent_events(task_id) if e["type"] == "approval_guardian"]
    assert guardian_events and guardian_events[0]["payload"]["approved"] is False
    # 拒绝结果反馈给模型（tool_result 含错误）
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert tool_results and "拒绝" in str(tool_results[0].get("error") or tool_results[0].get("content") or "")


def test_guardian_timeout_fails_closed(tmp_path: Path):
    """guardian 审查异常时：fail-closed 拒绝，工具不执行。"""
    calls = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "delete_path", "input": {"path": "old"}}],
            "stop_reason": "tool_use",
        },
        # 主循环下一轮（审查异常后模型看到拒绝结果）
        {"text": "操作被拒绝。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = _delete_definition(calls)

    async def fail_llm(**kwargs):
        # guardian 审查消息含"安全审查员"标记；其余调用正常响应
        messages = kwargs.get("messages") or []
        is_guardian = any("安全审查员" in str(m.get("content", "")) for m in messages)
        if is_guardian:
            raise RuntimeError("guardian review failed")
        return responses.pop(0)

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(definition)
        return registry

    runtime = LocalAgentRuntime(workspaces, registry_factory, fail_llm, task_store=store)

    events = collect(runtime.run("session", "删除 old 目录", approval_mode="guardian"))

    assert calls == []  # fail-closed：工具未执行
    task_id = events[0]["task_id"]
    guardian_results = [e for e in store.get_agent_events(task_id) if e["type"] == "approval_guardian_result"]
    assert guardian_results and guardian_results[0]["payload"]["approved"] is False
    assert "fail-closed" in str(guardian_results[0].get("payload", {}).get("decision_raw"))


def test_guardian_circuit_breaker_falls_back_to_manual(tmp_path: Path):
    """guardian 连续拒绝达上限（3 次）后熔断：降级为人工审批（approval_required yield）。"""
    calls = []
    responses = []
    for i in range(3):
        responses.append({
            "text": "",
            "tool_uses": [{"id": f"tool-{i}", "name": "delete_path", "input": {"path": "old"}}],
            "stop_reason": "tool_use",
        })
        responses.append({"text": '{"decision": "denied", "rationale": "no"}', "tool_uses": [], "stop_reason": "end_turn"})
    # 第 4 次工具调用 → 熔断后应触发 approval_required（等用户）
    responses.append({
        "text": "",
        "tool_uses": [{"id": "tool-3", "name": "delete_path", "input": {"path": "old"}}],
        "stop_reason": "tool_use",
    })
    runtime, store = make_runtime(tmp_path, responses, _delete_definition(calls))

    events = collect(runtime.run("session", "删除 old 目录", approval_mode="guardian"))
    types = [event["type"] for event in events]

    assert "approval_required" in types
    assert calls == []  # 所有尝试都未执行（前 3 次 guardian 拒绝 + 第 4 次等人工）
    assert events[-1]["status"] == "waiting_approval"
    task_id = events[0]["task_id"]
    persisted = _persisted_types(store, task_id)
    assert "approval_guardian_breaker" in persisted
