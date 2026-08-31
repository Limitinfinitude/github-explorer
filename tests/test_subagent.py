import asyncio
import json
from pathlib import Path

from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.registry import ToolDefinition, ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.subagent import (
    DEFAULT_SUBAGENT_TOOLS,
    MAX_FANOUT_TASKS,
    MAX_SUBAGENT_ROUNDS,
    MAX_SUBAGENT_TOOL_CALLS,
    run_subagent,
    run_subagents,
    subagent_system_prompt,
)
from agent.runtime.workspace import WorkspaceManager


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def make_runtime(tmp_path: Path, responses: list[dict], definitions: list[ToolDefinition]):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        for definition in definitions:
            registry.register(definition)
        return registry

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm, task_store=None), root


def read_tool(args):
    return ToolResult.ok(output=f"content-of:{args.get('path', '')}", data={"path": args.get("path")})


def search_tool(args):
    return ToolResult.ok(output=f"hits-for:{args.get('query', '')}", data={})


def test_subagent_system_prompt_contains_delegation_declaration():
    prompt = subagent_system_prompt("查一下配置项", ["read_file"], "C:\\ws")
    assert "被委托的子代理" in prompt
    assert "权限范围固定不可扩大" in prompt
    assert "read_file" in prompt
    assert "不要重试" in prompt
    assert "C:\\ws" in prompt


def test_run_subagent_collects_conclusion_with_tool_usage(tmp_path: Path):
    responses = [
        # 第一轮：子代理要求读文件
        {"text": "", "tool_uses": [{"id": "sub-call-1", "name": "read_file", "input": {"path": "a.py"}}], "stop_reason": "tool_use"},
        # 第二轮：给出结论
        {"text": "配置项 X 在 a.py 中，值为 true。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    runtime, _ = make_runtime(tmp_path, responses, [
        ToolDefinition(name="read_file", description="Read", input_schema={"type": "object", "properties": {}}, risk=ToolRisk.READ, handler=read_tool),
    ])

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "subagent-task", "session_id": "session", "round": 1, "summary": {}, "user_message": "委托"}
        runtime.register_task("session", "subagent-task")
        result = await run_subagent(runtime, state, registry, "查一下配置项 X", ["read_file"])
        return result

    result = asyncio.run(scenario())
    assert result.success is True
    assert "配置项 X 在 a.py 中" in result.output
    assert result.data["rounds"] == 2
    assert result.data["tool_calls"] == 1


def test_run_subagent_rejects_out_of_scope_tools(tmp_path: Path):
    """白名单外的工具不执行：权限范围固定不可扩大。"""
    responses = [
        {"text": "", "tool_uses": [
            {"id": "sub-call-1", "name": "run_command", "input": {"command": "rm -rf /"}},
        ], "stop_reason": "tool_use"},
        {"text": "受限：无法执行命令。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    runtime, _ = make_runtime(tmp_path, responses, [
        ToolDefinition(name="run_command", description="Run", input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS, handler=lambda a: ToolResult.ok(output="should-not-run")),
    ])

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "subagent-task", "session_id": "session", "round": 1, "summary": {}, "user_message": "委托"}
        runtime.register_task("session", "subagent-task")
        # 白名单只给 read_file（注册表里甚至没有 read_file，只有 run_command）
        result = await run_subagent(runtime, state, registry, "执行命令", ["read_file"])
        return result

    result = asyncio.run(scenario())
    assert result.success is True
    assert "受限" in result.output
    # 工具的调用最终是失败结果（被拒），模型看到后收敛
    assert result.data["tool_calls"] == 1


def test_run_subagent_stops_at_tool_budget(tmp_path: Path):
    """工具预算用尽后强制收敛，不再执行工具。"""
    responses = []
    for index in range(MAX_SUBAGENT_TOOL_CALLS + 1):
        responses.append({"text": "", "tool_uses": [{"id": f"sub-call-{index}", "name": "search_text", "input": {"query": f"q{index}"}}], "stop_reason": "tool_use"})
    responses.append({"text": "结论：检索完成。", "tool_uses": [], "stop_reason": "end_turn"})
    runtime, _ = make_runtime(tmp_path, responses, [
        ToolDefinition(name="search_text", description="Search", input_schema={"type": "object", "properties": {}}, risk=ToolRisk.READ, handler=search_tool),
    ])

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "subagent-task", "session_id": "session", "round": 1, "summary": {}, "user_message": "委托"}
        runtime.register_task("session", "subagent-task")
        return await run_subagent(runtime, state, registry, "检索 20 次", ["search_text"])

    result = asyncio.run(scenario())
    assert result.data["tool_calls"] <= MAX_SUBAGENT_TOOL_CALLS
    assert "结论" in result.output


def test_run_subagent_permission_gate_rejects_without_confirmation(tmp_path: Path):
    """EXTERNAL 风险工具在子代理内不弹审批，直接转为被拒结果（委托声明语义）。"""
    responses = [
        {"text": "", "tool_uses": [{"id": "sub-call-1", "name": "mcp_write", "input": {}}], "stop_reason": "tool_use"},
        {"text": "无法执行写入操作，需要主代理处理。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    runtime, _ = make_runtime(tmp_path, responses, [
        ToolDefinition(name="mcp_write", description="Write", input_schema={"type": "object", "properties": {}}, risk=ToolRisk.EXTERNAL, handler=lambda a: ToolResult.ok(output="wrote")),
    ])

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "subagent-task", "session_id": "session", "round": 1, "summary": {}, "user_message": "委托"}
        runtime.register_task("session", "subagent-task")
        return await run_subagent(runtime, state, registry, "写入", ["mcp_write"])

    result = asyncio.run(scenario())
    assert result.success is True
    assert "需要用户确认" in result.output or "主代理" in result.output


# ==================== run_subagents：并行 fan-out + 汇总 ====================

def make_parallel_runtime(tmp_path: Path, per_task_conclusions: dict[str, str]):
    """每个子代理第一轮直接给结论（无需工具），结论按 task 内容路由。"""
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        messages = kwargs.get("messages") or []
        # 子代理首条消息即 task 内容；据此路由结论
        task = messages[0]["content"] if messages else ""
        for key, conclusion in per_task_conclusions.items():
            if key in task:
                return {"text": conclusion, "tool_uses": [], "stop_reason": "end_turn"}
        return {"text": f"处理了：{task}", "tool_uses": [], "stop_reason": "end_turn"}

    def registry_factory(session_id: str):
        return ToolRegistry()

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm, task_store=None), root


def test_run_subagents_fans_out_and_collects_conclusions(tmp_path: Path):
    conclusions = {
        "查询A": "A 的结论：找到 3 处。",
        "查询B": "B 的结论：无匹配。",
        "查询C": "C 的结论：已定位。",
    }
    runtime, _ = make_parallel_runtime(tmp_path, conclusions)

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "fanout-task", "session_id": "session", "summary": {}, "user_message": "并行"}
        runtime.register_task("session", "fanout-task")
        return await run_subagents(runtime, state, registry, ["查询A", "查询B", "查询C"], None)

    result = asyncio.run(scenario())
    assert result.success is True
    assert result.data["task_count"] == 3
    assert result.data["succeeded"] == 3
    assert result.data["failed"] == 0
    # 交回的 output 以编号清单组织，注明让主模型 synthesize
    assert "请综合成对主任务的答复" in result.output
    assert "[1]" in result.output and "[2]" in result.output and "[3]" in result.output
    assert "A 的结论" in result.output and "B 的结论" in result.output


def test_run_subagents_isolates_failed_task(tmp_path: Path):
    """单个子代理失败不阻断整批：失败项记录错误，成功项照常收编。"""
    conclusions = {"OK": "成功结论。", "FAIL": "成功结论。"}

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        messages = kwargs.get("messages") or []
        task = messages[0]["content"] if messages else ""
        if "FAIL" in task:
            raise RuntimeError("子代理炸了")
        return {"text": "成功结论。", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm, task_store=None)

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "fanout-task", "session_id": "session", "summary": {}, "user_message": "并行"}
        runtime.register_task("session", "fanout-task")
        return await run_subagents(runtime, state, registry, ["OK 任务", "FAIL 任务"], None)

    result = asyncio.run(scenario())
    assert result.success is True  # 整批调用本身成功
    assert result.data["succeeded"] == 1
    assert result.data["failed"] == 1
    # 汇总里能看到失败描摹
    assert "失败 1" in result.output
    failed_item = [r for r in result.data["results"] if not r["success"]][0]
    assert failed_item["error"]


def test_run_subagents_caps_task_count(tmp_path: Path):
    conclusions = {}
    runtime, _ = make_parallel_runtime(tmp_path, conclusions)
    too_many = [f"任务{i}" for i in range(MAX_FANOUT_TASKS + 5)]

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "fanout-task", "session_id": "session", "summary": {}, "user_message": "并行"}
        runtime.register_task("session", "fanout-task")
        return await run_subagents(runtime, state, registry, too_many, None)

    result = asyncio.run(scenario())
    assert result.data["task_count"] == MAX_FANOUT_TASKS  # 超出被截断


def test_run_subagents_rejects_empty_tasks(tmp_path: Path):
    runtime, _ = make_parallel_runtime(tmp_path, {})

    async def scenario():
        registry = runtime.registry_factory("session")
        state = {"task_id": "fanout-task", "session_id": "session", "summary": {}, "user_message": "并行"}
        runtime.register_task("session", "fanout-task")
        return await run_subagents(runtime, state, registry, [], None)

    result = asyncio.run(scenario())
    assert result.success is False
    assert result.error_kind == "invalid_input"
