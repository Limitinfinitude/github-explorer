"""边说话边干活：工具轮次实时推送模型文字（token），静默时生成旁白（narration）。"""
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


def make_runtime(tmp_path: Path, responses: list[dict], definition: ToolDefinition):
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

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm), root


def test_runtime_streams_model_text_before_tool_events(tmp_path: Path):
    responses = [
        {
            "text": "我先看一下目录结构。",
            "tool_uses": [{"id": "tool-1", "name": "list_directory", "input": {"path": "."}}],
            "stop_reason": "tool_use",
        },
        {"text": "目录结构已了解。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="list_directory",
        description="List directory",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="src\nREADME.md"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "查看项目"))

    tokens = [event["content"] for event in events if event["type"] == "token"]
    assert tokens[0] == "我先看一下目录结构。"
    token_index = next(i for i, event in enumerate(events) if event["type"] == "token")
    tool_index = next(i for i, event in enumerate(events) if event["type"] == "tool_call")
    assert token_index < tool_index


def test_runtime_emits_narration_when_model_is_silent(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "read_file", "input": {"path": "README.md"}}],
            "stop_reason": "tool_use",
        },
        {"text": "已查看。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="# Title"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "查看 README"))

    narrations = [event for event in events if event["type"] == "narration"]
    assert narrations, "静默调用工具时必须生成旁白"
    assert narrations[0]["tool_name"] == "read_file"
    assert narrations[0]["content"] == "正在读取 README.md"
    narration_index = next(i for i, event in enumerate(events) if event["type"] == "narration")
    tool_index = next(i for i, event in enumerate(events) if event["type"] == "tool_call")
    assert narration_index < tool_index


def test_tool_narration_humanizes_common_arguments(tmp_path: Path):
    cases = [
        ({"name": "run_command", "input": {"command": "pytest -q"}}, "正在执行 pytest -q"),
        ({"name": "edit_files", "input": {"edits": [{"path": "a.py"}, {"path": "b.py"}]}}, "正在修改 2 个文件"),
        ({"name": "search_text", "input": {"query": "def main"}}, "正在搜索 def main"),
        ({"name": "wait_http", "input": {"url": "http://127.0.0.1:7788"}}, "正在等待服务就绪 http://127.0.0.1:7788"),
        ({"name": "unknown_tool", "input": {}}, "正在调用 unknown_tool"),
    ]
    for tool_use, expected in cases:
        assert LocalAgentRuntime._tool_narration(tool_use) == expected
