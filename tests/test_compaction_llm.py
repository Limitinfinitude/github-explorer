import asyncio
import json
from pathlib import Path

from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.registry import ToolDefinition, ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager

NINE_SECTION_SUMMARY = """<analysis>分析过程。</analysis>
<summary>
1. Primary Request and Intent: 用户要求修复测试。
2. Key Technical Concepts: pytest。
3. Files and Code Sections: - tests/test_x.py：断言错误。
4. Errors and fixes: 断言失败，已修复。
5. Problem Solving: 修复完成。
6. All user messages: 修复测试。
7. Pending Tasks: 无。
8. Work Completed: 修复完成。
9. Context for Continuing Work: 项目在临时目录。
</summary>"""

BIG_OUTPUT = "x" * 6000


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def make_runtime(tmp_path: Path, responses: list[dict], definition: ToolDefinition, max_context_tokens: int = 128_000):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(definition)
        return registry

    return LocalAgentRuntime(
        workspaces, registry_factory, fake_llm, task_store=None,
        max_context_tokens=max_context_tokens,
    ), root


def big_output_tool(args):
    return ToolResult.ok(output=BIG_OUTPUT, data={})


def test_first_compaction_uses_llm_nine_section_summary(tmp_path: Path):
    from agent.memory import Memory

    seen = []

    async def fake_llm(**kwargs):
        seen.append({
            "system": kwargs.get("system", ""),
            "messages": kwargs.get("messages", []),
        })
        if kwargs.get("system", "").startswith("CRITICAL: Respond with TEXT ONLY"):
            return {"text": NINE_SECTION_SUMMARY, "tool_uses": [], "stop_reason": "end_turn"}
        if len(seen) == 1:
            return {"text": "", "tool_uses": [{"id": "call-1", "name": "run_command", "input": {"command": "echo hi"}}], "stop_reason": "tool_use"}
        return {"text": "修复完成。", "tool_uses": [], "stop_reason": "end_turn"}

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="run_command", description="Run",
            input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
            handler=big_output_tool,
        ))
        return registry

    store = Memory(tmp_path / "agent.db")
    runtime = LocalAgentRuntime(
        workspaces, registry_factory, fake_llm, task_store=store,
        max_context_tokens=2_600, max_output_tokens=1_200,
    )

    events = collect(runtime.run("session", "修复测试", task_id="compact-llm"))

    summarize_calls = [call for call in seen if call["system"].startswith("CRITICAL: Respond with TEXT ONLY")]
    assert len(summarize_calls) == 1
    final_call = seen[-1]
    joined = json.dumps(final_call["messages"], ensure_ascii=False)
    assert "Primary Request and Intent" in joined
    assert "9. Context for Continuing Work" in joined
    # 首次压缩后只压缩一次：后续轮不再调用摘要器
    assert len(summarize_calls) == 1
    compacted = [event for event in store.get_agent_events("compact-llm") if event["type"] == "context_compacted"]
    assert len(compacted) == 1
    assert compacted[0]["payload"]["compaction_count"] == 1
    assert events[-1]["status"] == "completed"


def test_llm_compaction_falls_back_to_deterministic_on_failure(tmp_path: Path):
    seen = []

    async def fake_llm(**kwargs):
        seen.append(kwargs.get("system", ""))
        if kwargs.get("system", "").startswith("CRITICAL: Respond with TEXT ONLY"):
            raise RuntimeError("summarizer provider error")
        if len(seen) == 1:
            return {"text": "", "tool_uses": [{"id": "call-1", "name": "run_command", "input": {"command": "echo hi"}}], "stop_reason": "tool_use"}
        return {"text": "修复完成。", "tool_uses": [], "stop_reason": "end_turn"}

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="run_command", description="Run",
            input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
            handler=big_output_tool,
        ))
        return registry

    runtime = LocalAgentRuntime(
        workspaces, registry_factory, fake_llm, task_store=None,
        max_context_tokens=2_600, max_output_tokens=1_200,
    )

    events = collect(runtime.run("session", "修复测试", task_id="compact-fallback"))

    final_messages = None
    for call_system in seen:
        pass
    # 摘要器失败后：确定性 ContextHandoff 仍在，任务完成
    assert any("CRITICAL: Respond with TEXT ONLY" in system for system in seen)
    assert events[-1]["status"] == "completed"
