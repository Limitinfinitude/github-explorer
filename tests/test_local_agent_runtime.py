import asyncio
import json
from pathlib import Path

import httpx

from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.compaction import CompactionEngine
from agent.runtime.registry import ToolDefinition, ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.workspace import WorkspaceManager


def collect(async_iterator):
    async def run():
        return [event async for event in async_iterator]

    return asyncio.run(run())


def make_runtime(tmp_path: Path, responses: list[dict], definition: ToolDefinition, task_store=None):
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

    return LocalAgentRuntime(workspaces, registry_factory, fake_llm, task_store=task_store), root


def test_runtime_emits_plan_tool_change_and_done_without_intent_classifier(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "create_directory", "input": {"path": "src"}}],
            "stop_reason": "tool_use",
        },
        {"text": "目录已创建。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="create_directory",
        description="Create directory",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(output="created", changed_files=[args["path"]]),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "创建 src 目录"))
    event_types = [event["type"] for event in events]

    assert event_types == ["context_usage", "plan", "narration", "tool_call", "tool_result", "file_changed", "context_usage", "finalization", "token", "done"]
    assert "目录已创建。" in events[-1]["content"]
    assert "## 文件变更" in events[-1]["content"]


def test_runtime_normalizes_persisted_file_verification_and_process_paths(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "build_project", "input": {}}],
            "stop_reason": "tool_use",
        },
        {"text": "已完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    root_holder = {}

    def handler(_args):
        root = root_holder["root"]
        return ToolResult.ok(
            output="built",
            changed_files=[str(root / "src" / "应用.py")],
            process_id="process-1",
            data={
                "cwd": str(root / "服务"),
                "verification": [{
                    "kind": "unit",
                    "success": True,
                    "cwd": str(root),
                    "path": str(root / "src" / "应用.py"),
                }],
            },
        )

    definition = ToolDefinition(
        name="build_project",
        description="Build project",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=handler,
    )
    runtime, root = make_runtime(tmp_path, responses, definition)
    root_holder["root"] = root

    events = collect(runtime.run("session", "创建并验证服务"))

    changed = next(event for event in events if event["type"] == "file_changed")
    process = next(event for event in events if event["type"] == "process_started")
    verification = next(event for event in events if event["type"] == "verification")
    assert changed["files"] == ["src/应用.py"]
    assert process["data"]["cwd"] == "服务"
    assert verification["checks"][0]["cwd"] == "."
    assert verification["checks"][0]["path"] == "src/应用.py"


def test_runtime_emits_structured_finalization_independent_of_model_markdown(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "edit_files", "input": {}}],
            "stop_reason": "tool_use",
        },
        {
            "text": '<｜｜DSML｜｜tool_calls>broken</｜｜DSML｜｜tool_calls>',
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]
    definition = ToolDefinition(
        name="edit_files",
        description="Edit file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(
            output="changed",
            changed_files=["app.py"],
            data={"verification": [{"kind": "unit", "success": True}]},
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "修改并验证 app.py"))

    finalization = next(event for event in events if event["type"] == "finalization")
    assert finalization["status"] == "completed"
    assert finalization["facts"]["changed_files"] == ["app.py"]
    assert finalization["facts"]["verification"] == {"passed": 1, "failed": 0, "total": 1}
    assert "DSML" not in finalization["explanation"]


def test_runtime_marks_max_tool_rounds_incomplete(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "tool-1", "name": "read_file", "input": {"path": "README.md"}}], "stop_reason": "tool_use"},
        {"text": "tool budget exhausted", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file", description="Read file",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="content"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)
    runtime.max_rounds = 1
    events = collect(runtime.run("session", "read README.md"))
    assert events[-1]["status"] == "incomplete"


def test_runtime_allows_one_schema_repair_then_stops_incomplete(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "bad-1", "name": "read_file", "input": {}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "",
            "tool_uses": [{"id": "bad-2", "name": "read_file", "input": {}}],
            "stop_reason": "tool_use",
        },
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="should not run"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "读取指定文件"))

    failures = [event for event in events if event["type"] == "tool_result"]
    assert len(failures) == 2
    assert failures[0]["data"]["validation"]["suggestion"]
    assert any(event["type"] == "tool_repair_exhausted" for event in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["status"] == "incomplete"


def test_runtime_records_independent_stage_budgets(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "a.py"}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "",
            "tool_uses": [{"id": "edit-1", "name": "edit_files", "input": {"edits": []}}],
            "stop_reason": "tool_use",
        },
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definitions = {
        "read_file": ToolDefinition(
            name="read_file", description="Read", input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ, handler=lambda args: ToolResult.ok(output="read"),
        ),
        "edit_files": ToolDefinition(
            name="edit_files", description="Edit", input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.WRITE_SAFE,
            handler=lambda args: ToolResult.ok(output="edited", changed_files=["a.py"]),
        ),
    }
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(_session_id):
        registry = ToolRegistry()
        for definition in definitions.values():
            registry.register(definition)
        return registry

    runtime = LocalAgentRuntime(workspaces, registry_factory, fake_llm)
    events = collect(runtime.run("session", "读取后修改文件"))

    finalization = next(event for event in events if event["type"] == "finalization")
    budgets = finalization["facts"]["stage_budgets"]
    assert budgets["inspect"]["used"] == 1
    assert budgets["implement"]["used"] == 1
    assert budgets["test"]["used"] == 0
    assert budgets["run"]["used"] == 0


def test_runtime_promotes_batch_http_checks_to_verifiable_evidence(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{
                "id": "batch-1",
                "name": "http_request_batch",
                "input": {"requests": [{"method": "GET", "url": "http://127.0.0.1:8000/missing", "expected_status": 404}]},
            }],
            "stop_reason": "tool_use",
        },
        {
            "text": "[1] [完成] 接口验收通过。[证据:check:http_batch]",
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]
    definition = ToolDefinition(
        name="http_request_batch",
        description="Batch HTTP",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda _args: ToolResult.ok(
            output="batch passed",
            data={
                "checks": [{"status": 404, "expected_status": 404, "success": True}],
                "total": 1,
                "passed": 1,
                "failed": 0,
            },
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "验收接口\n1. 检查接口返回预期状态"))

    finalization = next(event for event in events if event["type"] == "finalization")
    assert finalization["facts"]["verification"]["passed"] == 1
    assert events[-1]["status"] == "completed"


def test_runtime_counts_corrected_invalid_batch_as_recovered(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "batch-invalid", "name": "http_request_batch", "input": {}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "",
            "tool_uses": [{"id": "batch-fixed", "name": "http_request_batch", "input": {"requests": [{"method": "GET", "url": "http://127.0.0.1:8000"}]}}],
            "stop_reason": "tool_use",
        },
        {"text": "已完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="http_request_batch",
        description="Batch HTTP",
        input_schema={
            "type": "object",
            "properties": {"requests": {"type": "array", "minItems": 1}},
            "required": ["requests"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ,
        handler=lambda _args: ToolResult.ok(
            output="batch passed",
            data={"checks": [{"kind": "http", "success": True}], "total": 1, "passed": 1, "failed": 0},
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "执行批量验收"))

    assert any(event["type"] == "tool_recovered" for event in events)
    assert events[-1]["status"] == "completed"


def test_runtime_stops_before_tool_side_effect_when_stage_budget_is_exhausted(tmp_path: Path, monkeypatch):
    responses = [{
        "text": "",
        "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "a.py"}}],
        "stop_reason": "tool_use",
    }]
    calls = []
    definition = ToolDefinition(
        name="read_file", description="Read", input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ, handler=lambda args: calls.append(args) or ToolResult.ok(output="read"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)
    monkeypatch.setitem(__import__("agent.runtime.runtime", fromlist=["_STAGE_LIMITS"])._STAGE_LIMITS, "inspect", 0)

    events = collect(runtime.run("session", "读取文件"))

    assert calls == []
    assert any(event["type"] == "stage_budget_exhausted" for event in events)
    assert events[-1]["status"] == "incomplete"


def test_runtime_settles_requirements_from_final_response_after_max_tool_rounds(tmp_path: Path):
    from agent.memory import Memory

    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "edit-1", "name": "edit_files", "input": {"target": "search.py"}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "[1] [完成] 搜索已实现。[证据:check:unit]",
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]
    definition = ToolDefinition(
        name="edit_files",
        description="Edit files",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(
            output="changed",
            changed_files=[args["target"]],
            data={"verification": [{"kind": "unit", "success": True}]},
        ),
    )
    store = Memory(tmp_path / "requirements.db")
    runtime, _ = make_runtime(tmp_path, responses, definition, task_store=store)
    runtime.max_rounds = 1

    events = collect(runtime.run("session", "请实现：1) 支持搜索。", task_id="task-1"))

    assert events[-1]["status"] == "completed"
    assert [event["type"] for event in events].count("acceptance") == 1
    assert store.list_session_requirements("session", status="pending") == []


def test_runtime_marks_running_tool_interrupted_when_drive_fails(tmp_path: Path):
    from agent.memory import Memory

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")

    async def fake_llm(**kwargs):
        return {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "explode", "input": {}}],
            "stop_reason": "tool_use",
        }

    class ExplodingRegistry:
        def schemas(self):
            return []

        def execute(self, name, args, *, confirmed=False):
            raise RuntimeError("registry boundary failed")

    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: ExplodingRegistry(),
        fake_llm,
        task_store=store,
    )

    events = collect(runtime.run("session", "run explode", task_id="interrupted-task"))

    assert events[-1]["status"] == "failed"
    calls = store.get_agent_tool_calls("interrupted-task")
    assert calls[0]["status"] == "interrupted"
    assert calls[0]["error_kind"] == "interrupted"


def test_runtime_marks_unrecovered_tool_failure_incomplete(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "tool-1", "name": "run_command", "input": {"command": "bad"}}], "stop_reason": "tool_use"},
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="run_command", description="Run command",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=lambda args: ToolResult.fail("command failed"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)
    events = collect(runtime.run("session", "run it"))
    assert events[-1]["status"] == "incomplete"


def test_runtime_preserves_registry_error_kind_in_sse_and_ledger(tmp_path: Path):
    from agent.memory import Memory

    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "invalid-1", "name": "read_file", "input": {}}],
            "stop_reason": "tool_use",
        },
        {"text": "无法继续。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="unused"),
    )
    store = Memory(tmp_path / "agent.db")
    runtime, _ = make_runtime(tmp_path, responses, definition, task_store=store)

    events = collect(runtime.run("session", "读取文件", task_id="invalid-task"))

    result_event = next(event for event in events if event["type"] == "tool_result")
    ledger = store.get_agent_tool_calls("invalid-task")
    assert result_event["error_kind"] == "invalid_input"
    assert ledger[0]["error_kind"] == "invalid_input"


def test_runtime_recovers_failed_tool_only_with_success_in_same_domain(tmp_path: Path):
    from agent.memory import Memory

    attempts = 0
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "call-failed", "name": "run_command", "input": {
                "command": "pytest -q", "cwd": ".",
            }}],
            "stop_reason": "tool_use",
        },
        {
            "text": "",
            "tool_uses": [{"id": "call-retry", "name": "run_command", "input": {
                "command": ".venv\\Scripts\\python.exe -m pytest -q", "cwd": ".",
            }}],
            "stop_reason": "tool_use",
        },
        {"text": "测试已通过。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    def run_command(args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return ToolResult.fail("tests failed", data={"cwd": str(tmp_path / "project")})
        return ToolResult.ok(output="1 passed", data={"cwd": str(tmp_path / "project")})

    definition = ToolDefinition(
        name="run_command", description="Run command",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=run_command,
    )
    store = Memory(tmp_path / "agent.db")
    runtime, _ = make_runtime(tmp_path, responses, definition, task_store=store)

    events = collect(runtime.run("session", "修复测试", task_id="recovery-task"))

    recovered = next(event for event in events if event["type"] == "tool_recovered")
    calls = {call["call_id"]: call for call in store.get_agent_tool_calls("recovery-task")}
    assert recovered["failed_call_id"] == "call-failed"
    assert recovered["recovered_by_call_id"] == "call-retry"
    assert calls["call-failed"]["recovery_key"] == calls["call-retry"]["recovery_key"]
    assert calls["call-failed"]["recovery_key"].endswith(":test")
    assert events[-1]["status"] == "completed"
    requirement = store.list_session_requirements("session", status="completed")[0]
    assert requirement["evidence"] == [{"type": "tool", "ref": "run_command"}]


def test_runtime_does_not_recover_failed_test_with_unrelated_success(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "call-failed", "name": "run_command", "input": {
                "command": "pytest -q", "cwd": ".",
            }}],
            "stop_reason": "tool_use",
        },
        {
            "text": "",
            "tool_uses": [{"id": "call-unrelated", "name": "run_command", "input": {
                "command": "Get-Location", "cwd": ".",
            }}],
            "stop_reason": "tool_use",
        },
        {"text": "检查结束。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    def run_command(args):
        if "pytest" in args["command"]:
            return ToolResult.fail("tests failed", data={"cwd": str(tmp_path / "project")})
        return ToolResult.ok(output=str(tmp_path / "project"), data={"cwd": str(tmp_path / "project")})

    definition = ToolDefinition(
        name="run_command", description="Run command",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.PROCESS,
        handler=run_command,
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "修复测试"))

    assert not any(event["type"] == "tool_recovered" for event in events)
    assert events[-1]["status"] == "incomplete"


def test_runtime_plain_chat_skips_execution_plan_when_no_tool_is_used(tmp_path: Path):
    responses = [{"text": "你好，有什么可以帮你？", "tool_uses": [], "stop_reason": "end_turn"}]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="unused"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "你好"))

    assert [event["type"] for event in events] == ["context_usage", "token", "done"]
    assert "你好，有什么可以帮你？" in events[-1]["content"]
    assert "## 完成结果" not in events[-1]["content"]
    assert "## 文件变更" not in events[-1]["content"]


def test_runtime_continues_an_incomplete_plain_chat_response(tmp_path: Path):
    responses = [
        {"text": "你好！你可以告诉", "tool_uses": [], "stop_reason": "end_turn"},
        {"text": "我想了解的本地任务。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    runtime, _ = make_runtime(tmp_path, responses, ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="unused"),
    ))

    events = collect(runtime.run("session", "你好"))

    assert events[-1]["status"] == "completed"
    assert "你好！你可以告诉我想了解的本地任务。" in events[-1]["content"]


def test_capability_question_skips_repo_map_and_tool_schemas(tmp_path: Path):
    captured = {}
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    class TrackingContext:
        calls = 0

        def repo_map(self, session_id):
            self.calls += 1
            return ToolResult.ok(output="app.py")

    context = TrackingContext()

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "我可以读取、修改和运行本地项目。", "tool_uses": [], "stop_reason": "end_turn"}

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="unused"),
    ))
    runtime = LocalAgentRuntime(workspaces, lambda _: registry, fake_llm, context_engine=context)

    events = collect(runtime.run("session", "你能做什么"))

    assert context.calls == 0
    assert captured["tools"] == []
    assert [event["type"] for event in events] == ["context_usage", "token", "done"]


def test_runtime_prompt_allows_plain_chat_without_tools():
    prompt = LocalAgentRuntime._system_prompt("C:/workspace")

    # 意图判断交给模型（大厂做法：不枚举触发词/意图分类表），提示词只给原则
    assert "动手前先判断" in prompt
    assert "知识截止" in prompt
    assert "不要使用 Emoji 作为标题或列表装饰" in prompt
    assert "普通问答不要生成执行报告" in prompt
    assert "代码围栏必须独占一行且不缩进" in prompt
    assert "列举能力或步骤时使用 Markdown 列表" in prompt
    # harness 机制已保证的规则不再重复进提示词（大厂「harness 限制而非提示词」；
    # venv 约束已移入 ensure_venv 工具描述 + 任务描述兜底）
    assert "Python 项目只能使用" not in prompt
    assert "不得构造越过工作区的路径" not in prompt
    assert "edit_files，搜索文本必须唯一" not in prompt
    # 不枚举意图分类表/触发词（避免把答案喂给模型）
    assert "信息咨询" not in prompt
    assert "最新/外部信息" not in prompt


def test_runtime_reserves_enough_tokens_for_file_edit_tool_arguments(tmp_path: Path):
    captured = {}
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "完成。", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)

    collect(runtime.run("session", "创建一个 HTML 网页"))

    assert captured["max_tokens"] >= 8_000


def test_runtime_retries_one_model_read_timeout(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    attempts = 0

    async def flaky_llm(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("model stalled")
        return {"text": "recovered", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), flaky_llm)

    events = collect(runtime.run("session", "check status"))

    assert attempts == 2
    assert events[-1]["status"] == "completed"
    assert events[-1]["content"] == "recovered"


def test_runtime_describes_empty_model_read_timeout():
    error = LocalAgentRuntime._terminal_error(httpx.ReadTimeout(""), {"summary": {}})

    assert "模型服务读取超时" in error
    assert "未提供错误信息" not in error


def test_status_followup_answers_from_previous_task_without_calling_model(tmp_path: Path):
    from agent.memory import Memory

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "status.db")
    store.save_agent_task({
        "task_id": "previous-task",
        "session_id": "session",
        "user_message": "部署项目",
        "status": "incomplete",
        "final_text": "仓库已克隆，但依赖安装超时。",
        "summary": {
            "changed_files": ["ai-agent-book"],
            "verification": [],
            "successful_tools": ["clone_repo"],
        },
    })
    store.save_agent_task({
        "task_id": "current-task",
        "session_id": "session",
        "user_message": "",
        "status": "pending",
        "summary": {},
    })

    async def unexpected_llm(**_kwargs):
        raise AssertionError("状态追问不应调用模型或工具")

    runtime = LocalAgentRuntime(
        workspaces, lambda _: ToolRegistry(), unexpected_llm, task_store=store,
    )

    events = collect(runtime.run(
        "session", "失败了吗？原因是什么", task_id="current-task",
    ))

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[-1]["status"] == "completed"
    assert "上一项任务未完成" in events[-1]["content"]
    assert "仓库已克隆，但依赖安装超时" in events[-1]["content"]
    assert "ai-agent-book" in events[-1]["content"]


def test_empty_final_model_text_uses_execution_facts_and_is_incomplete(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "edit_files", "input": {}}],
            "stop_reason": "tool_use",
        },
        {"text": "", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="edit_files",
        description="Edit files",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda _args: ToolResult.ok(
            output="written",
            changed_files=["app.py"],
            data={"verification": [{
                "path": "app.py", "success": True, "kind": "write_readback",
            }]},
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "修改 app.py"))

    assert events[-1]["status"] == "incomplete"
    assert "模型未返回最终说明" in events[-1]["content"]
    assert "已修改 1 个文件" in events[-1]["content"]
    assert "1 项验证通过" in events[-1]["content"]


def test_runtime_defaults_to_128k_context_and_trims_old_history(tmp_path: Path):
    captured = {}
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "done.", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)
    history = [
        {"role": "user", "content": "old-a" * 100_000},
        {"role": "assistant", "content": "old-b" * 100_000},
    ]

    collect(runtime.run("session", "latest request", history=history))

    assert runtime.max_context_tokens == 128_000
    assert runtime.tool_result_preview_chars == 12_000
    # 真实用户消息存在（末尾可能是 harness 注入的 reminder/instructions，跳过 _kind 合成消息）
    assert any(
        m.get("content") == "latest request" and not m.get("_kind")
        for m in captured["messages"]
    )
    assert runtime._estimate_tokens(captured["system"], captured["messages"]) <= 116_000


def test_runtime_loads_workspace_agents_md_into_messages(tmp_path: Path):
    captured = {}
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENTS.md").write_text("Always run the focused test first.", encoding="utf-8")
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "done", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)

    collect(runtime.run("session", "检查项目"))

    # AGENTS.md 作为带 content kind 的 user 消息注入（对齐 Claude Code），不进 system 缓存前缀
    assert "Always run the focused test first." not in captured["system"]
    injected = [m for m in captured["messages"] if m.get("_kind") == "instructions.project"]
    assert injected and "Always run the focused test first." in injected[0]["content"]
    state = next(iter(runtime._task_cache.values()))
    assert state["instruction_sources"][0]["scope"] == "project"


def test_runtime_compacts_model_input_but_preserves_full_task_messages(tmp_path: Path):
    from agent.memory import Memory

    captured = {}
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        if kwargs.get("system", "").startswith("CRITICAL: Respond with TEXT ONLY"):
            # 摘要器失败 → 回退确定性 ContextHandoff（本测试验证的正是该路径）
            raise RuntimeError("summarizer unavailable")
        return {"text": "done", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: ToolRegistry(),
        fake_llm,
        max_context_tokens=4_000,
        max_output_tokens=200,
        task_store=store,
    )
    history = [
        {"role": "user", "content": "old request " * 2_000},
        {"role": "assistant", "content": "old result " * 2_000},
    ]

    collect(runtime.run("session", "latest request", history=history, task_id="compact-task"))

    stored = store.get_agent_task("compact-task")
    assert len(stored["messages"]) == 3
    assert stored["messages"][-1]["content"] == "latest request"
    assert stored["context_handoff"]["goal"] == "latest request"
    assert stored["run"]["compaction_count"] == 1
    assert stored["run"]["compacted_message_count"] == 3
    assert any("ContextHandoff" in str(message["content"]) for message in captured["messages"])
    # captured 是 fit 后 + harness 注入（reminder），不包含完整原始历史；对比原始历史长度
    assert len(captured["messages"]) <= len(stored["messages"])
    compacted = next(
        event for event in store.get_agent_events("compact-task")
        if event["type"] == "context_compacted"
    )
    assert compacted["payload"]["summary_version"] == 1
    assert compacted["payload"]["source_sequence_start"] == 1
    assert compacted["payload"]["source_sequence_end"] >= compacted["payload"]["source_sequence_start"]
    assert compacted["payload"]["pending_requirements"] == []
    assert compacted["payload"]["open_tool_call_ids"] == []


def test_runtime_uses_injected_compaction_engine(tmp_path: Path):
    class RecordingCompactionEngine(CompactionEngine):
        def __init__(self):
            super().__init__()
            self.called = False

        def compact(self, system, messages, state, **kwargs):
            self.called = True
            return super().compact(system, messages, state, **kwargs)

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    engine = RecordingCompactionEngine()

    async def fake_llm(**kwargs):
        return {"text": "done", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: ToolRegistry(),
        fake_llm,
        max_context_tokens=2_000,
        max_output_tokens=200,
        compaction_engine=engine,
    )
    history = [{"role": "user", "content": "old request " * 2_000}]

    collect(runtime.run("session", "latest request", history=history))

    assert engine.called is True


def test_runtime_persists_ordered_lifecycle_and_tool_events(tmp_path: Path):
    from agent.memory import Memory

    root = tmp_path / "project"
    root.mkdir()
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads" / "main").write_text("a" * 40 + "\n", encoding="utf-8")
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "events.db")
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "create_directory", "input": {"path": "src"}}],
            "stop_reason": "tool_use",
        },
        {"text": "目录已创建。", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    async def fake_llm(**kwargs):
        return responses.pop(0)

    definition = ToolDefinition(
        name="create_directory",
        description="Create directory",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(output="created", changed_files=["src"]),
    )
    registry = ToolRegistry()
    registry.register(definition)
    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: registry,
        fake_llm,
        task_store=store,
    )

    collect(runtime.run("session", "创建目录", task_id="event-task"))

    events = store.get_agent_events("event-task")
    types = [event["type"] for event in events]
    assert types[0] == "task_started"
    assert "tool_call" in types
    assert "tool_result" in types
    assert "file_changed" in types
    assert types[-1] == "task_completed"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    stream_call = next(event for event in events if event["type"] == "tool_call")
    stream_result = next(event for event in events if event["type"] == "tool_result")
    assert stream_call["payload"]["call_id"] == stream_result["payload"]["call_id"] == "tool-1"
    assert stream_call["payload"]["batch_id"] == stream_result["payload"]["batch_id"]
    ledger = store.get_agent_tool_calls("event-task")
    assert len(ledger) == 1
    assert ledger[0]["call_id"] == "tool-1"
    assert ledger[0]["status"] == "succeeded"
    memories = store.search_project_memories(str(root.resolve()), "src")
    assert len(memories) == 1
    assert memories[0]["source_ref"] == "event-task"
    assert "Git分支: main" in memories[0]["content"]
    assert f"Git基线: {'a' * 40}" in memories[0]["content"]


def test_runtime_keeps_internal_ids_out_of_provider_tool_result_blocks(tmp_path: Path):
    captured_messages = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "read_file", "input": {"path": "a"}}],
            "stop_reason": "tool_use",
        },
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="content"),
    ))
    runtime = LocalAgentRuntime(workspaces, lambda _: registry, fake_llm)

    collect(runtime.run("session", "read a"))

    tool_result = captured_messages[1][-1]["content"][0]
    assert tool_result["tool_use_id"] == "tool-1"
    assert set(tool_result) == {"type", "tool_use_id", "content"}


def test_runtime_persists_large_tool_output_and_sends_bounded_preview(tmp_path: Path):
    from agent.memory import Memory

    captured_messages = []
    large_output = "begin-" + ("x" * 400) + "-unseen-tail"
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-large", "name": "read_file", "input": {"path": "large.txt"}}],
            "stop_reason": "tool_use",
        },
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output=large_output),
    )
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")

    async def fake_llm(**kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    registry = ToolRegistry()
    registry.register(definition)
    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: registry,
        fake_llm,
        task_store=store,
        tool_result_preview_chars=100,
    )

    events = collect(runtime.run("session", "read large file", task_id="large-task"))

    provider_block = captured_messages[1][-1]["content"][0]
    provider_content = json.loads(provider_block["content"])
    result_event = next(event for event in events if event["type"] == "tool_result")
    activity = store.get_agent_task_activity("large-task")
    artifacts = activity["artifacts"]

    assert set(provider_block) == {"type", "tool_use_id", "content"}
    assert provider_content["truncated"] is True
    assert provider_content["artifact"]["artifact_id"] == artifacts[0]["artifact_id"]
    assert provider_content["original_size"] > provider_content["preview_chars"]
    assert "unseen-tail" not in provider_block["content"]
    assert result_event["artifact"]["artifact_id"] == artifacts[0]["artifact_id"]
    assert "unseen-tail" not in json.dumps(result_event, ensure_ascii=False)
    assert "unseen-tail" not in json.dumps(activity["tool_runs"], ensure_ascii=False)
    assert "unseen-tail" not in json.dumps(activity["events"], ensure_ascii=False)

    restored = store.get_agent_artifact("large-task", artifacts[0]["artifact_id"])
    assert restored is not None
    assert "unseen-tail" in restored["content"]


def test_runtime_keeps_small_tool_output_inline_without_artifact(tmp_path: Path):
    from agent.memory import Memory

    captured_messages = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-small", "name": "read_file", "input": {"path": "small.txt"}}],
            "stop_reason": "tool_use",
        },
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="small"),
    )
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")

    async def fake_llm(**kwargs):
        captured_messages.append(kwargs["messages"])
        return responses.pop(0)

    registry = ToolRegistry()
    registry.register(definition)
    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: registry,
        fake_llm,
        task_store=store,
        tool_result_preview_chars=1_000,
    )

    collect(runtime.run("session", "read small file", task_id="small-task"))

    provider_content = captured_messages[1][-1]["content"][0]["content"]
    assert json.loads(provider_content)["output"] == "small"
    assert store.get_agent_task_activity("small-task")["artifacts"] == []


def test_runtime_marks_tool_running_before_handler_executes(tmp_path: Path):
    from agent.memory import Memory

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "agent.db")
    observed_statuses = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "read_file", "input": {}}],
            "stop_reason": "tool_use",
        },
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]

    async def fake_llm(**kwargs):
        return responses.pop(0)

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: observed_statuses.append(
            store.get_agent_tool_calls("running-task")[0]["status"]
        ) or ToolResult.ok(output="content"),
    ))
    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: registry,
        fake_llm,
        task_store=store,
    )

    collect(runtime.run("session", "read", task_id="running-task"))

    assert observed_statuses == ["running"]


def test_runtime_injects_relevant_workspace_memory_into_system_prompt(tmp_path: Path):
    from agent.memory import Memory

    captured = {}
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "memory.db")
    store.remember_project_fact(
        workspace_root=str(root.resolve()),
        content="Use pytest -q for project verification",
        source_type="task",
        source_ref="previous-task",
        confidence=1.0,
        verification_status="verified",
    )

    async def fake_llm(**kwargs):
        captured.update(kwargs)
        return {"text": "done", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: ToolRegistry(),
        fake_llm,
        task_store=store,
    )

    collect(runtime.run("session", "运行 pytest 验证"))

    assert "Use pytest -q for project verification" in captured["system"]


def test_runtime_persists_model_and_workspace_snapshot_without_keys(tmp_path: Path):
    from agent.memory import Memory

    root = tmp_path / "project"
    root.mkdir()
    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / ".git" / "refs" / "heads" / "main").write_text("b" * 40 + "\n", encoding="utf-8")
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "snapshot.db")

    async def fake_llm(**kwargs):
        return {"text": "done", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm, task_store=store)
    collect(runtime.run(
        "session",
        "检查快照",
        task_id="snapshot-task",
        model_context={"id": "model-a", "protocol": "openai", "base_url": "http://127.0.0.1:9000"},
    ))

    state = store.get_agent_task("snapshot-task")
    assert state["model_context"] == {
        "id": "model-a",
        "protocol": "openai",
        "base_url": "http://127.0.0.1:9000",
    }
    assert state["workspace_snapshot"]["branch"] == "main"
    assert state["workspace_snapshot"]["head"] == "b" * 40
    assert "api_key" not in state["model_context"]


def test_runtime_pauses_when_tool_requires_confirmation(tmp_path: Path):
    responses = [{
        "text": "",
        "tool_uses": [{"id": "tool-1", "name": "delete_path", "input": {"path": "old"}}],
        "stop_reason": "tool_use",
    }]
    definition = ToolDefinition(
        name="delete_path",
        description="Delete path",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: ToolResult.ok(output="deleted"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "删除 old"))

    assert [event["type"] for event in events] == ["context_usage", "plan", "narration", "tool_call", "approval_required", "done"]
    assert events[3]["tool_name"] == "delete_path"
    assert events[3]["call_id"] == events[4]["call_id"] == "tool-1"
    assert events[3]["batch_id"] == events[4]["batch_id"]
    assert events[-1]["status"] == "waiting_approval"


def test_runtime_resumes_same_task_after_confirmation(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "delete_path", "input": {"path": "old"}}],
            "stop_reason": "tool_use",
        },
        {"text": "删除完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    calls = []
    definition = ToolDefinition(
        name="delete_path",
        description="Delete path",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: calls.append(args) or ToolResult.ok(output="deleted"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    paused = collect(runtime.run("session", "删除 old", task_id="task-1"))
    resumed = collect(runtime.resume("session", "task-1", approved=True))

    assert paused[-1]["status"] == "waiting_approval"
    assert [event["task_id"] for event in resumed] == ["task-1"] * len(resumed)
    assert [event["type"] for event in resumed] == ["tool_result", "context_usage", "finalization", "token", "done"]
    assert resumed[-1]["status"] == "completed"
    assert [event["type"] for event in paused] == ["context_usage", "plan", "narration", "tool_call", "approval_required", "done"]
    assert paused[3]["call_id"] == paused[4]["call_id"] == resumed[0]["call_id"] == "tool-1"
    assert paused[3]["batch_id"] == paused[4]["batch_id"] == resumed[0]["batch_id"]
    assert calls == [{"path": "old"}]


def test_runtime_records_approval_resolved_event_when_resuming(tmp_path: Path):
    from agent.memory import Memory

    store = Memory(tmp_path / "agent.db")
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "delete-1", "name": "delete_path", "input": {"path": "old"}}],
            "stop_reason": "tool_use",
        },
        {"text": "删除完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="delete_path",
        description="Delete path",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: ToolResult.ok(output="deleted"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition, task_store=store)

    collect(runtime.run("session", "删除 old", task_id="approval-task"))
    collect(runtime.resume("session", "approval-task", approved=True))

    resolved = [
        event for event in store.get_agent_events("approval-task")
        if event["type"] == "approval_resolved"
    ]
    assert len(resolved) == 1
    assert resolved[0]["payload"] == {
        "approved": True,
        "batch_id": resolved[0]["payload"]["batch_id"],
        "call_id": "delete-1",
        "tool_name": "delete_path",
    }


def test_runtime_restores_waiting_task_from_sqlite(tmp_path: Path):
    from agent.memory import Memory

    store = Memory(tmp_path / "agent.db")
    responses = [{
        "text": "",
        "tool_uses": [{"id": "tool-1", "name": "delete_path", "input": {"path": "old"}}],
        "stop_reason": "tool_use",
    }]
    definition = ToolDefinition(
        name="delete_path",
        description="Delete path",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: ToolResult.ok(output="deleted"),
    )
    runtime, root = make_runtime(tmp_path, responses, definition, task_store=store)
    collect(runtime.run("session", "删除 old", task_id="persisted-task"))

    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def final_llm(**kwargs):
        return {"text": "已恢复并完成。", "tool_uses": [], "stop_reason": "end_turn"}

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(definition)
        return registry

    restored = LocalAgentRuntime(workspaces, registry_factory, final_llm, task_store=store)
    events = collect(restored.resume("session", "persisted-task", approved=True))

    assert events[-1]["status"] == "completed"
    assert store.get_agent_task("persisted-task")["status"] == "completed"
    calls = store.get_agent_tool_calls("persisted-task")
    assert len(calls) == 1
    assert calls[0]["call_id"] == "tool-1"
    assert calls[0]["status"] == "succeeded"


def test_runtime_resume_reanchors_workspace_from_task_snapshot(tmp_path: Path):
    from agent.memory import Memory

    task_root = tmp_path / "task-project"
    fallback_root = tmp_path / "fallback"
    task_root.mkdir()
    fallback_root.mkdir()
    store = Memory(tmp_path / "agent.db")
    responses = [{
        "text": "",
        "tool_uses": [{"id": "tool-1", "name": "inspect_cwd", "input": {}}],
        "stop_reason": "tool_use",
    }]
    workspaces = WorkspaceManager()
    workspaces.bind("session", task_root)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="inspect_cwd",
            description="Inspect cwd",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.DESTRUCTIVE,
            handler=lambda args: ToolResult.ok(
                output=str(workspaces.current_path(session_id)),
            ),
        ))
        return registry

    async def first_llm(**kwargs):
        return responses.pop(0)

    runtime = LocalAgentRuntime(workspaces, registry_factory, first_llm, task_store=store)
    paused = collect(runtime.run("session", "inspect", task_id="anchored-task"))
    assert paused[-1]["status"] == "waiting_approval"

    workspaces.bind("session", fallback_root)

    async def final_llm(**kwargs):
        return {"text": "done", "tool_uses": [], "stop_reason": "end_turn"}

    restored = LocalAgentRuntime(workspaces, registry_factory, final_llm, task_store=store)
    events = collect(restored.resume("session", "anchored-task", approved=True))

    tool_result = next(event for event in events if event["type"] == "tool_result")
    assert tool_result["output"] == str(task_root.resolve())
    assert workspaces.get("session").root == task_root.resolve()


def test_runtime_marks_closed_stream_interrupted(tmp_path: Path):
    from agent.memory import Memory

    store = Memory(tmp_path / "agent.db")
    responses = [{
        "text": "",
        "tool_uses": [{"id": "tool-1", "name": "read_value", "input": {}}],
        "stop_reason": "tool_use",
    }]
    definition = ToolDefinition(
        name="read_value",
        description="Read value",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="value"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition, task_store=store)

    async def close_after_first_event():
        stream = runtime.run("session", "read", task_id="closed-task")
        await anext(stream)
        await stream.aclose()

    asyncio.run(close_after_first_event())

    assert store.get_agent_task("closed-task")["status"] == "interrupted"


def test_runtime_can_resume_interrupted_task_from_persisted_state(tmp_path: Path):
    from agent.memory import Memory

    store = Memory(tmp_path / "resume.db")
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store.save_agent_task({
        "task_id": "resume-task",
        "session_id": "session",
        "user_message": "完成一个小项目",
        "status": "interrupted",
        "resume_available": True,
        "resume_count": 0,
        "round": 1,
        "round_limit": 2,
        "messages": [{"role": "user", "content": "完成一个小项目"}],
        "summary": {"changed_files": [], "verification": [], "processes": [], "successful_tools": []},
        "plan": [],
        "acceptance_criteria": [],
        "session_requirements": [],
        "implicit_requirement_positions": [],
        "tool_call_ledger": {},
        "active_batch": None,
        "allow_tools": False,
        "workspace_root": str(root),
        "current_path": str(root),
        "repo_map": "",
        "instruction_context": "",
        "project_memories": [],
    })

    async def final_llm(**kwargs):
        return {"text": "项目已完成", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), final_llm, task_store=store)
    events = collect(runtime.resume_interrupted("session", "resume-task"))

    assert events[-1]["type"] == "done"
    assert events[-1]["status"] == "completed"
    state = store.get_agent_task("resume-task")
    assert state["status"] == "completed"
    assert state["run"]["resume_available"] is False
    assert state["run"]["resume_count"] == 1
    assert any(event["type"] == "task_resumed" for event in store.get_agent_events("resume-task"))


def test_runtime_cancel_marks_live_task_cancelled(tmp_path: Path):
    from agent.memory import Memory

    async def scenario():
        root = tmp_path / "project"
        root.mkdir()
        workspaces = WorkspaceManager()
        workspaces.bind("session", root)
        store = Memory(tmp_path / "agent.db")
        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_llm(**kwargs):
            entered.set()
            await release.wait()
            return {"text": "late", "tool_uses": [], "stop_reason": "end_turn"}

        runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), slow_llm, task_store=store)

        async def consume():
            return [event async for event in runtime.run(
                "session", "执行长任务", task_id="cancel-task",
            )]

        consumer = asyncio.create_task(consume())
        await entered.wait()
        result = runtime.cancel("session", "cancel-task")
        release.set()
        events = await consumer
        return result, events, store.get_agent_task("cancel-task")

    result, events, stored = asyncio.run(scenario())

    assert result.success is True
    assert events[-1]["status"] == "cancelled"
    assert stored["status"] == "cancelled"


def test_runtime_can_cancel_registered_task_before_first_llm_call(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    calls = []

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return {"text": "不应执行", "tool_uses": [], "stop_reason": "end_turn"}

    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)
    runtime.register_task("session", "early-cancel")

    result = runtime.cancel("session", "early-cancel")
    events = collect(runtime.run("session", "执行长任务", task_id="early-cancel"))

    assert result.success is True
    assert calls == []
    assert events[-1]["status"] == "cancelled"


def test_runtime_replans_after_diagnostic_budget_and_uses_extra_rounds(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "a.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "read-2", "name": "read_file", "input": {"path": "b.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "write-1", "name": "edit_files", "input": {"edits": []}}], "stop_reason": "tool_use"},
        {"text": "已完成。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file", description="Read file",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="content"),
    )
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(session_id: str):
        registry = ToolRegistry()
        registry.register(definition)
        registry.register(ToolDefinition(
            name="edit_files", description="Edit files",
            input_schema={"type": "object", "properties": {}}, risk=ToolRisk.WRITE_SAFE,
            handler=lambda args: ToolResult.ok(output="changed", changed_files=["a.py"]),
        ))
        return registry

    runtime = LocalAgentRuntime(
        workspaces, registry_factory, fake_llm,
        max_rounds=2, diagnostic_tool_budget=2, replan_extra_rounds=2,
    )

    events = collect(runtime.run("session", "检查后修复文件"))

    warning = next(event for event in events if event["type"] == "budget_warning")
    assert warning["diagnostic_tool_count"] == 2
    assert any(event["type"] == "file_changed" for event in events)
    assert events[-1]["status"] == "completed"


def test_runtime_diagnostic_budget_counts_unique_file_observations(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "same.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "read-2", "name": "read_file", "input": {"path": "same.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "read-3", "name": "read_file", "input": {"path": "other.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "edit-1", "name": "edit_files", "input": {"edits": []}}], "stop_reason": "tool_use"},
        {"text": "done", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file", description="Read file", input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ, handler=lambda args: ToolResult.ok(output="content"),
    )
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(_session_id):
        registry = ToolRegistry()
        registry.register(definition)
        registry.register(ToolDefinition(
            name="edit_files", description="Edit files", input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.WRITE_SAFE, handler=lambda args: ToolResult.ok(output="changed", changed_files=["same.py"]),
        ))
        return registry

    runtime = LocalAgentRuntime(workspaces, registry_factory, fake_llm, diagnostic_tool_budget=2, max_rounds=5)
    events = collect(runtime.run("session", "Fix the file"))

    assert any(event["type"] == "file_changed" for event in events)
    warning = next(event for event in events if event["type"] == "budget_warning")
    assert warning["diagnostic_tool_count"] == 3
    assert warning["diagnostic_unique_count"] == 2


def test_runtime_stops_repeated_diagnostics_after_replan_budget(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "a.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "read-2", "name": "read_file", "input": {"path": "b.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "read-3", "name": "read_file", "input": {"path": "c.py"}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "read-4", "name": "read_file", "input": {"path": "d.py"}}], "stop_reason": "tool_use"},
    ]
    reads = []
    definition = ToolDefinition(
        name="read_file", description="Read file",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.READ,
        handler=lambda args: reads.append(args["path"]) or ToolResult.ok(output="content"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)
    runtime.max_rounds = 2
    runtime.diagnostic_tool_budget = 2
    runtime.replan_extra_rounds = 4

    events = collect(runtime.run("session", "检查后修复文件"))

    # 重规划后宽限一轮：第 3 轮纯诊断被执行，第 4 轮仍纯诊断被拦停（不执行）
    assert reads == ["a.py", "b.py", "c.py"]
    assert [event["type"] for event in events].count("budget_warning") == 2
    assert events[-1]["status"] == "incomplete"
    assert "重规划后仍只请求诊断工具" in events[-1]["content"]


def test_runtime_does_not_complete_change_request_after_diagnostics_only(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "app.py"}}],
            "stop_reason": "tool_use",
        },
        {"text": "Task completed.", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="content"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "Fix the broken application"))

    assert events[-1]["status"] == "incomplete"
    assert "未记录任何文件或项目变更" in events[-1]["content"]


def test_runtime_allows_read_only_inspection_without_file_changes(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "read-1", "name": "read_file", "input": {"path": "app.py"}}],
            "stop_reason": "tool_use",
        },
        {"text": "Inspection complete.", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="content"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "Inspect app.py and report the findings"))

    assert events[-1]["status"] == "completed"


def test_runtime_reports_exception_type_when_finalization_error_is_empty(tmp_path: Path):
    calls = 0

    async def fake_llm(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "text": "",
                "tool_uses": [{"id": "write-1", "name": "edit_files", "input": {"edits": []}}],
                "stop_reason": "tool_use",
            }
        raise RuntimeError()

    definition = ToolDefinition(
        name="edit_files",
        description="Edit file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(output="changed", changed_files=["app.py"]),
    )
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)
    registry = runtime.registry_factory("session")
    registry.register(definition)
    runtime.registry_factory = lambda _: registry

    events = collect(runtime.run("session", "Fix app.py"))

    assert events[-2]["type"] == "error"
    assert events[-1]["status"] == "failed"
    assert "RuntimeError" in events[-1]["content"]
    assert "app.py" in events[-1]["content"]


def test_runtime_accepts_bold_bracketed_acceptance_numbers(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "write-1", "name": "edit_files", "input": {"edits": []}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "**[1] 页面 - [完成]** [证据:file:templates/index.html]",
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]
    definition = ToolDefinition(
        name="edit_files",
        description="Edit file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(
            output="changed", changed_files=["templates/index.html"],
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "最终验收：1) 提供网页。"))

    assert events[-1]["status"] == "completed"
    assert "未逐项覆盖验收清单" not in events[-1]["content"]


def test_runtime_extracts_numbered_acceptance_criteria():
    criteria = LocalAgentRuntime._extract_acceptance_criteria(
        "最终验收：1) 提供网页；2) 支持中文搜索；3) 清洗 script 标签。"
    )

    assert criteria == [
        {"id": 1, "text": "提供网页"},
        {"id": 2, "text": "支持中文搜索"},
        {"id": 3, "text": "清洗 script 标签"},
    ]


def test_runtime_never_marks_explicit_unfinished_final_reply_completed(tmp_path: Path):
    responses = [{
        "text": "已完成主要检查。\n\n移动端与可访问性：`[未完成]`",
        "tool_uses": [],
        "stop_reason": "end_turn",
    }]
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="unused"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "检查当前项目"))

    assert events[-1]["status"] == "incomplete"


def test_runtime_records_local_model_request_lifecycle_without_prompts_or_keys(tmp_path: Path):
    from agent.memory import Memory

    store = Memory(tmp_path / "model-events.db")
    definition = ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="unused"),
    )
    responses = [{
        "text": "done",
        "tool_uses": [],
        "stop_reason": "end_turn",
        "usage_metadata": {
            "input_tokens": 120,
            "output_tokens": 8,
            "total_tokens": 128,
        },
    }]
    runtime, _ = make_runtime(tmp_path, responses, definition, task_store=store)

    collect(runtime.run(
        "session",
        "inspect private source",
        task_id="model-task",
        model_context={
            "id": "deepseek-v4-flash",
            "protocol": "openai",
            "base_url": "https://example.invalid/v1",
            "api_key": "must-not-be-recorded",
        },
    ))

    events = store.get_agent_events("model-task")
    started = next(event for event in events if event["type"] == "model_request_started")
    completed = next(event for event in events if event["type"] == "model_request_completed")
    assert started["payload"] == {
        "round": 1,
        "phase": "reasoning",
        "model": "deepseek-v4-flash",
        "protocol": "openai",
        "base_url": "https://example.invalid/v1",
    }
    assert completed["payload"]["round"] == 1
    assert completed["payload"]["phase"] == "reasoning"
    assert completed["payload"]["stop_reason"] == "end_turn"
    assert completed["payload"]["latency_ms"] >= 0
    assert completed["payload"]["usage"] == {
        "input_tokens": 120,
        "output_tokens": 8,
        "total_tokens": 128,
    }
    serialized = json.dumps(events, ensure_ascii=False)
    assert "must-not-be-recorded" not in serialized
    assert "inspect private source" not in serialized
    assert '"messages"' not in serialized
    assert '"system"' not in serialized


def test_runtime_records_local_model_request_failure(tmp_path: Path):
    from agent.memory import Memory

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "failed-model-events.db")

    async def failed_llm(**kwargs):
        raise RuntimeError("provider unavailable")

    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: ToolRegistry(),
        failed_llm,
        task_store=store,
    )
    events = collect(runtime.run("session", "hello", task_id="failed-model-task"))

    assert events[-1]["status"] == "failed"
    failed = next(
        event for event in store.get_agent_events("failed-model-task")
        if event["type"] == "model_request_failed"
    )
    assert failed["payload"]["round"] == 1
    assert failed["payload"]["phase"] == "reasoning"
    assert failed["payload"]["error_type"] == "RuntimeError"
    assert failed["payload"]["latency_ms"] >= 0


def test_runtime_extracts_inline_criteria_without_treating_later_item_references_as_markers():
    criteria = LocalAgentRuntime._extract_acceptance_criteria(
        "从零开发一个简单但可用的书签管理器。累计验收要求："
        "1) 先实现 Flask + SQLite 的新增、列表和持久化；"
        "2) 支持标题或 URL 搜索；"
        "3) 支持收藏状态筛选和数量统计。"
        "本轮只完成第 1 项，第 2、3 项明确保留为未完成。"
    )

    assert criteria == [
        {"id": 1, "text": "先实现 Flask + SQLite 的新增、列表和持久化"},
        {"id": 2, "text": "支持标题或 URL 搜索"},
        {"id": 3, "text": "支持收藏状态筛选和数量统计"},
    ]


def test_runtime_injects_acceptance_checklist_and_marks_uncovered_reply_incomplete(tmp_path: Path):
    captured = {}

    async def fake_llm(**kwargs):
        captured["system"] = kwargs["system"]
        return {
            "text": "1. 网页已完成。\n2. 中文搜索已完成。",
            "tool_uses": [],
            "stop_reason": "end_turn",
        }

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)

    events = collect(runtime.run(
        "session",
        "最终验收：1) 提供网页；2) 支持中文搜索；3) 清洗 script 标签。",
    ))

    assert "验收清单（3 项）" in captured["system"]
    assert "[1] 提供网页" in captured["system"]
    assert events[-1]["status"] == "incomplete"
    assert "未逐项覆盖验收清单：3" in events[-1]["content"]


def test_runtime_marks_numbered_acceptance_reply_without_evidence_incomplete(tmp_path: Path):
    async def fake_llm(**kwargs):
        return {
            "text": "1. [完成] 网页已提供。\n2. [完成] 中文搜索已验证。\n3. [完成] script 标签已清洗。",
            "tool_uses": [],
            "stop_reason": "end_turn",
        }

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)

    events = collect(runtime.run(
        "session",
        "最终验收：1) 提供网页；2) 支持中文搜索；3) 清洗 script 标签。",
    ))

    assert events[-1]["status"] == "incomplete"
    acceptance = next(event for event in events if event["type"] == "acceptance")
    assert [item["status"] for item in acceptance["items"]] == [
        "unverified", "unverified", "unverified",
    ]


def test_runtime_rejects_acceptance_evidence_not_present_in_task_summary(tmp_path: Path):
    async def fake_llm(**kwargs):
        return {
            "text": "1. [完成] 网页已提供。[证据:file:templates/missing.html]",
            "tool_uses": [],
            "stop_reason": "end_turn",
        }

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm)

    events = collect(runtime.run("session", "最终验收：1) 提供网页。"))

    acceptance = next(event for event in events if event["type"] == "acceptance")
    assert events[-1]["status"] == "incomplete"
    assert acceptance["items"] == [{
        "id": 1,
        "text": "提供网页",
        "status": "unverified",
        "evidence": [{"type": "file", "ref": "templates/missing.html", "valid": False}],
        "reason": "完成声明缺少有效执行证据",
    }]


def test_runtime_completes_when_every_acceptance_item_has_real_evidence(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "edit_files", "input": {"edits": []}}],
            "stop_reason": "tool_use",
        },
        {
            "text": (
                "1. [完成] 网页已提供。[证据:file:templates/index.html]\n"
                "2. [完成] HTTP 已验证。[证据:check:http]"
            ),
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]
    definition = ToolDefinition(
        name="edit_files",
        description="Edit files",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(
            output="written",
            changed_files=["templates/index.html"],
            data={"verification": [{
                "kind": "http",
                "command": "GET /",
                "success": True,
                "status": 200,
            }]},
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run(
        "session",
        "最终验收：1) 提供网页；2) HTTP 返回成功。",
    ))

    acceptance = next(event for event in events if event["type"] == "acceptance")
    assert events[-1]["status"] == "completed"
    assert "[证据:" not in events[-1]["content"]
    assert [item["status"] for item in acceptance["items"]] == ["passed", "passed"]
    assert acceptance["success"] is True


def test_runtime_does_not_treat_http_liveness_as_search_acceptance(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "verify_project", "input": {}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "1. [完成] 中文搜索可用。[证据:check:http]",
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]
    definition = ToolDefinition(
        name="verify_project",
        description="Verify project",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(
            output="HTTP 200",
            data={"checks": [{
                "kind": "http",
                "command": "GET /",
                "success": True,
            }]},
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "最终验收：1) 支持中文搜索。"))

    acceptance = next(event for event in events if event["type"] == "acceptance")
    assert events[-1]["status"] == "incomplete"
    assert acceptance["items"][0]["status"] == "unverified"
    assert acceptance["items"][0]["evidence"][0]["sufficient"] is False


def test_runtime_carries_unfinished_session_requirement_into_continue_turn(tmp_path: Path):
    from agent.memory import Memory

    captured_systems = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "edit-search", "name": "edit_files", "input": {"target": "search.py"}}],
            "stop_reason": "tool_use",
        },
        {
            "text": (
                "[1] [完成] 中文搜索已实现。[证据:check:unit]\n"
                "[2] [未完成] 筛选统计尚未实现。"
            ),
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
        {
            "text": "",
            "tool_uses": [{"id": "edit-stats", "name": "edit_files", "input": {"target": "stats.py"}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "[2] [完成] 筛选统计已实现。[证据:check:unit]",
            "tool_uses": [],
            "stop_reason": "end_turn",
        },
    ]

    async def fake_llm(**kwargs):
        captured_systems.append(kwargs["system"])
        return responses.pop(0)

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "requirements.db")
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="edit_files",
        description="Edit files",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(
            output="changed",
            changed_files=[args["target"]],
            data={"verification": [{
                "kind": "unit",
                "command": "pytest",
                "success": True,
            }]},
        ),
    ))
    runtime = LocalAgentRuntime(
        workspaces,
        lambda _: registry,
        fake_llm,
        task_store=store,
    )

    first_events = collect(runtime.run(
        "session",
        "请实现：1) 支持中文搜索；2) 增加筛选统计。",
        task_id="task-1",
    ))
    first_completed = store.list_session_requirements("session", status="completed")
    first_pending = store.list_session_requirements("session", status="pending")
    second_events = collect(runtime.run(
        "session",
        "按 TODO 继续推进吧",
        task_id="task-2",
    ))

    assert first_events[-1]["status"] == "incomplete"
    assert [(item["position"], item["text"]) for item in first_completed] == [
        (1, "支持中文搜索"),
    ]
    assert [(item["position"], item["text"]) for item in first_pending] == [
        (2, "增加筛选统计"),
    ]
    assert [(item["position"], item["text"]) for item in store.list_session_requirements(
        "session", status="completed",
    )] == [(1, "支持中文搜索"), (2, "增加筛选统计")]
    assert "[2] 增加筛选统计" in captured_systems[2]
    assert "[1] 支持中文搜索" not in captured_systems[2]
    assert second_events[-1]["status"] == "completed"
    assert store.list_session_requirements("session", status="pending") == []


def test_runtime_stops_repeating_identical_failed_call(tmp_path: Path):
    failed_call = {
        "text": "",
        "tool_uses": [{"id": "tool-x", "name": "fail", "input": {"value": 1}}],
        "stop_reason": "tool_use",
    }
    responses = [failed_call, failed_call, failed_call, failed_call]
    definition = ToolDefinition(
        name="fail",
        description="Fail",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.fail("same failure"),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "失败任务"))

    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert len(tool_results) == 3
    assert events[-2]["type"] == "error"
    assert "重复失败" in events[-2]["content"]
    assert events[-1]["status"] == "failed"


def test_runtime_reserves_final_response_after_tool_round_limit(tmp_path: Path):
    calls = []
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "read_file", "input": {"path": "one"}}],
            "stop_reason": "tool_use",
        },
        {
            "text": "",
            "tool_uses": [{"id": "tool-2", "name": "read_file", "input": {"path": "two"}}],
            "stop_reason": "tool_use",
        },
        {"text": "Both files were inspected.", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output=args["path"]),
    ))
    runtime = LocalAgentRuntime(workspaces, lambda _: registry, fake_llm, max_rounds=2)

    events = collect(runtime.run("session", "Inspect two files"))

    assert len(calls) == 3
    assert calls[2]["tools"] == []
    assert len([event for event in events if event["type"] == "tool_result"]) == 2
    assert [event["type"] for event in events[-2:]] == ["token", "done"]
    assert events[-1]["status"] == "incomplete"
    assert "Both files were inspected." in events[-1]["content"]


def test_runtime_reports_readback_verification_for_file_edits(tmp_path: Path):
    responses = [
        {
            "text": "",
            "tool_uses": [{"id": "tool-1", "name": "edit_files", "input": {"edits": []}}],
            "stop_reason": "tool_use",
        },
        {"text": "小猫网页已创建。", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="edit_files",
        description="Edit files",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(
            output="已写入 cute-cat.html",
            changed_files=["cute-cat.html"],
            data={"diff": "+<html></html>\n", "verification": [{
                "path": "cute-cat.html", "success": True, "detail": "文件存在，已回读",
                "kind": "write_readback",
            }]},
        ),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)

    events = collect(runtime.run("session", "创建一个可爱的小猫 html 网页"))

    verification = next(event for event in events if event["type"] == "verification")
    assert verification["success"] is True
    assert verification["checks"][0]["path"] == "cute-cat.html"
    assert verification["checks"][0]["kind"] == "write_readback"
    assert "## 验证" in events[-1]["content"]


def test_runtime_records_http_tool_as_http_verification(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "tool-1", "name": "wait_http", "input": {"url": "http://127.0.0.1:5000/health"}}], "stop_reason": "tool_use"},
        {"text": "service ready", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    definition = ToolDefinition(
        name="wait_http", description="Wait for HTTP",
        input_schema={"type": "object", "properties": {}}, risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="ready", data={"url": args["url"], "status": 200}),
    )
    runtime, _ = make_runtime(tmp_path, responses, definition)
    events = collect(runtime.run("session", "check service"))
    verification = next(event for event in events if event["type"] == "verification")
    assert verification["checks"][0]["kind"] == "http"
    assert verification["checks"][0]["status"] == 200


def test_runtime_merges_http_ownership_into_managed_process_snapshot(tmp_path: Path):
    responses = [
        {"text": "", "tool_uses": [{"id": "start", "name": "start_process", "input": {}}], "stop_reason": "tool_use"},
        {"text": "", "tool_uses": [{"id": "wait", "name": "wait_http", "input": {"process_id": "p1"}}], "stop_reason": "tool_use"},
        {"text": "ready", "tool_uses": [], "stop_reason": "end_turn"},
    ]
    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    async def fake_llm(**kwargs):
        return responses.pop(0)

    def registry_factory(_session_id):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name="start_process", description="Start", input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.PROCESS,
            handler=lambda args: ToolResult.ok(
                process_id="p1", output="started", data={"status": "running", "launcher_pid": 42},
            ),
        ))
        registry.register(ToolDefinition(
            name="wait_http", description="Wait", input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ,
            handler=lambda args: ToolResult.ok(output="ready", data={
                "process_id": "p1", "owned": True, "listener_pids": [84], "process_tree_pids": [42, 84],
            }),
        ))
        return registry

    runtime = LocalAgentRuntime(workspaces, registry_factory, fake_llm)
    events = collect(runtime.run("session", "启动并确认服务"))

    finalization = next(event for event in events if event["type"] == "finalization")
    process = finalization["facts"]["processes"][0]
    assert process["owned"] is True
    assert process["listener_pids"] == [84]


def test_boundary_command_hard_blocked_in_auto_and_allowed_in_full(tmp_path: Path):
    """边界拦截：判分脚本路径/全局写入命令在非 full 档被硬拦（不进入审批），full 档放行。"""
    from agent.runtime.tooling import classify_command_risk

    executed: list[str] = []

    def handler(args):
        executed.append(str(args.get("command", "")))
        return ToolResult.ok(output="ok")

    definition = ToolDefinition(
        name="run_command",
        description="Run a command",
        input_schema={"type": "object", "properties": {"command": {"type": "string"}}},
        risk=ToolRisk.PROCESS,
        risk_resolver=classify_command_risk,
        handler=handler,
    )

    def build_responses():
        return [
            {"text": "", "tool_uses": [{"id": "t1", "name": "run_command",
                                        "input": {"command": r"type D:\GE-eval-projects\checks\pdfcpu\check.py"}}],
             "stop_reason": "tool_use"},
            {"text": "完成", "tool_uses": [], "stop_reason": "end_turn"},
        ]

    # auto 档：边界命令被硬拦，处理器从未执行
    runtime, _ = make_runtime(tmp_path, build_responses(), definition)
    events = collect(runtime.run("session", "跑命令", approval_mode="auto"))
    results = [e for e in events if e["type"] == "tool_result"]
    assert len(results) == 1
    assert results[0].get("success") is False
    assert results[0].get("error_kind") == "boundary"
    assert executed == []

    # full 档：同一命令放行执行
    executed.clear()
    full_dir = tmp_path / "full"
    full_dir.mkdir(parents=True, exist_ok=True)
    runtime2, _ = make_runtime(full_dir, build_responses(), definition)
    events2 = collect(runtime2.run("session", "跑命令", approval_mode="full"))
    results2 = [e for e in events2 if e["type"] == "tool_result"]
    assert results2[0].get("success") is True
    assert executed, "full 档应放行边界命令"
