import asyncio
import json
from pathlib import Path

from agent.runtime.hooks import (
    HOOK_EVENTS,
    HookConfig,
    HookRunner,
    parse_hook_configs,
    serialize_hook_configs,
)
from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.registry import ToolDefinition, ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager

HOOK_SCRIPT = """\
import json, sys
payload = json.load(sys.stdin)
mode = sys.argv[1]
if mode == "block":
    sys.stderr.write("blocked by policy")
    sys.exit(2)
if mode == "record":
    # 每事件独立日志：多个 hook 子进程并发 append 同一文件在 Windows 上会交错
    with open(sys.argv[2] + "." + payload.get("event", "x"), "a", encoding="utf-8") as f:
        f.write(payload.get("event", "") + "|" + str(payload.get("tool_name") or payload.get("status") or "") + "\\n")
sys.exit(0)
"""


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def make_runtime(tmp_path: Path, responses: list[dict], definition: ToolDefinition, configs: list[HookConfig]):
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

    runner = HookRunner(lambda: configs)
    return LocalAgentRuntime(
        workspaces, registry_factory, fake_llm, task_store=None,
        hook_runner=runner,
    ), root


def tool_response(call_id: str, command: str = "echo hi"):
    return {
        "text": "",
        "tool_uses": [{"id": call_id, "name": "run_command", "input": {"command": command}}],
        "stop_reason": "tool_use",
    }


def ok_tool(args):
    return ToolResult.ok(output="ok", data={})


def test_parse_hook_configs_drops_invalid_entries():
    raw = [
        {"event": "pre_tool", "command": "python x.py"},
        {"event": "nonsense", "command": "python y.py"},  # 非法事件
        {"event": "post_tool", "command": "  "},           # 空命令
        {"event": "session_end", "command": "python z.py", "matcher": "[", "enabled": False},  # 坏正则留待匹配期处理
        "not-a-dict",
    ]
    configs = parse_hook_configs(raw)
    assert [config.event for config in configs] == ["pre_tool", "session_end"]
    assert configs[1].enabled is False
    assert serialize_hook_configs(configs) == json.dumps(
        [
            {"event": "pre_tool", "command": "python x.py", "matcher": "", "enabled": True, "timeout": 20.0},
            {"event": "session_end", "command": "python z.py", "matcher": "[", "enabled": False, "timeout": 20.0},
        ],
        ensure_ascii=False,
    )
    assert parse_hook_configs("not json") == []
    assert parse_hook_configs({"event": "pre_tool"}) == []


def test_pre_tool_hook_blocks_tool_call(tmp_path: Path):
    from agent.memory import Memory

    script = tmp_path / "hook.py"
    script.write_text(HOOK_SCRIPT, encoding="utf-8")
    configs = [HookConfig(event="pre_tool", command=f'python "{script}" block')]
    responses = [
        tool_response("call-1"),
        {"text": "被拦截了。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="run_command", description="Run",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=ok_tool,
    )
    store = Memory(tmp_path / "agent.db")
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

    runtime = LocalAgentRuntime(
        workspaces, registry_factory, fake_llm, task_store=store,
        hook_runner=HookRunner(lambda: configs),
    )

    events = collect(runtime.run("session", "运行命令", task_id="hook-block"))

    result_events = [event for event in events if event["type"] == "tool_result"]
    assert result_events[0]["error_kind"] == "hook_blocked"
    assert "blocked by policy" in (result_events[0]["error"] or "")
    assert events[-1]["status"] == "incomplete"
    persisted = store.get_agent_events("hook-block")
    assert any(
        event["type"] == "tool_hook_blocked"
        and event["payload"].get("reason") == "blocked by policy"
        for event in persisted
    )


def test_pre_tool_hook_allows_when_exit_zero(tmp_path: Path):
    script = tmp_path / "hook.py"
    script.write_text(HOOK_SCRIPT, encoding="utf-8")
    configs = [HookConfig(event="pre_tool", command=f'python "{script}" record {tmp_path / "log.txt"}')]
    responses = [
        tool_response("call-1"),
        {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="run_command", description="Run",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=ok_tool,
    )
    runtime, _ = make_runtime(tmp_path, responses, definition, configs)

    events = collect(runtime.run("session", "运行命令", task_id="hook-allow"))

    assert events[-1]["status"] == "completed"
    assert events[-1]["type"] == "done"
    log = (tmp_path / "log.txt.pre_tool").read_text(encoding="utf-8")
    assert "pre_tool|run_command" in log


def test_pre_tool_matcher_filters_unrelated_tools(tmp_path: Path):
    script = tmp_path / "hook.py"
    script.write_text(HOOK_SCRIPT, encoding="utf-8")
    configs = [HookConfig(event="pre_tool", command=f'python "{script}" block', matcher="^other_tool$")]
    responses = [
        tool_response("call-1"),
        {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="run_command", description="Run",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=ok_tool,
    )
    runtime, _ = make_runtime(tmp_path, responses, definition, configs)

    events = collect(runtime.run("session", "运行命令", task_id="hook-matcher"))

    assert not any(event["type"] == "tool_hook_blocked" for event in events)
    assert events[-1]["status"] == "completed"


def test_session_start_and_end_hooks_receive_payload(tmp_path: Path):
    script = tmp_path / "hook.py"
    script.write_text(HOOK_SCRIPT, encoding="utf-8")
    log = tmp_path / "log.txt"
    configs = [
        HookConfig(event="session_start", command=f'python "{script}" record "{log}"'),
        HookConfig(event="session_end", command=f'python "{script}" record "{log}"'),
    ]
    responses = [
        {"text": "直接回答。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="run_command", description="Run",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=ok_tool,
    )
    runtime, _ = make_runtime(tmp_path, responses, definition, configs)

    events = collect(runtime.run("session", "你好", task_id="hook-session"))
    runner = runtime.hook_runner
    asyncio.run(runner.drain())

    assert events[-1]["status"] == "completed"
    # HOOK_SCRIPT 按事件分文件写（并发 append 同一文件在 Windows 上会交错）
    start_lines = (tmp_path / "log.txt.session_start").read_text(encoding="utf-8").splitlines()
    end_lines = (tmp_path / "log.txt.session_end").read_text(encoding="utf-8").splitlines()
    assert any(event == "session_start" for event, *_ in (line.split("|") for line in start_lines))
    assert any(
        event == "session_end" and status == "completed"
        for event, status in (line.split("|") for line in end_lines)
    )
