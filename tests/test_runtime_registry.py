from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.permissions import PermissionGate
from agent.runtime.registry import ToolDefinition, ToolRegistry


def test_permission_gate_uses_declared_risk_level():
    gate = PermissionGate()

    assert gate.requires_confirmation(ToolRisk.READ) is False
    assert gate.requires_confirmation(ToolRisk.WRITE_SAFE) is False
    assert gate.requires_confirmation(ToolRisk.PROCESS) is False
    assert gate.requires_confirmation(ToolRisk.DESTRUCTIVE) is True
    assert gate.requires_confirmation(ToolRisk.PRIVILEGED) is True
    assert gate.requires_confirmation(ToolRisk.EXTERNAL) is True


def test_registry_exports_anthropic_tool_schema():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="echo",
        description="Echo text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output=args["text"]),
    ))

    assert registry.schemas() == [{
        "name": "echo",
        "description": "Echo text",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }]


def test_registry_executes_safe_tool_without_confirmation():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="echo",
        description="Echo text",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="ok"),
    ))

    result = registry.execute("echo", {})

    assert result.success is True
    assert result.output == "ok"


def test_registry_rejects_unknown_tool_with_stable_error_kind():
    result = ToolRegistry().execute("missing", {})

    assert result.success is False
    assert result.error_kind == "unknown_tool"


def test_registry_rejects_invalid_input_before_handler_runs():
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ,
        handler=lambda args: calls.append(args) or ToolResult.ok(output="read"),
    ))

    missing = registry.execute("read_file", {})
    wrong_type = registry.execute("read_file", {"path": "a.py", "start_line": "1"})
    extra = registry.execute("read_file", {"path": "a.py", "unexpected": True})

    assert missing.error_kind == "invalid_input"
    assert "$.path" in missing.error
    assert wrong_type.error_kind == "invalid_input"
    assert "$.start_line" in wrong_type.error
    assert extra.error_kind == "invalid_input"
    assert "$.unexpected" in extra.error
    assert calls == []


def test_registry_returns_field_level_schema_repair_guidance():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="read_file",
        description="Read file",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult.ok(output="read"),
    ))

    result = registry.execute("read_file", {})

    assert result.error_kind == "invalid_input"
    assert result.data["validation"] == {
        "code": "required",
        "path": "$.path",
        "message": "缺少必填字段",
        "expected": "string",
        "actual": "missing",
        "suggestion": "补充字段 $.path，值类型应为 string",
    }


def test_registry_reports_nested_array_validation_path():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="edit_files",
        description="Edit files",
        input_schema={
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "operation": {"type": "string", "enum": ["write", "replace"]},
                        },
                        "required": ["path", "operation"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["edits"],
            "additionalProperties": False,
        },
        risk=ToolRisk.WRITE_SAFE,
        handler=lambda args: ToolResult.ok(output="edited"),
    ))

    result = registry.execute("edit_files", {
        "edits": [{"path": "a.py", "operation": "append"}],
    })

    assert result.error_kind == "invalid_input"
    assert "$.edits[0].operation" in result.error


def test_registry_pauses_destructive_tool_until_confirmed():
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="delete_path",
        description="Delete a path",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: calls.append(args) or ToolResult.ok(output="deleted"),
    ))

    pending = registry.execute("delete_path", {"path": "tmp"})

    assert pending.success is False
    assert pending.requires_confirmation is True
    assert pending.error_kind == "permission_denied"
    assert calls == []

    completed = registry.execute("delete_path", {"path": "tmp"}, confirmed=True)
    assert completed.success is True
    assert calls == [{"path": "tmp"}]


def test_registry_converts_tool_exception_to_failure():
    def explode(args):
        raise RuntimeError("broken")

    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="explode",
        description="Fail",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=explode,
    ))

    result = registry.execute("explode", {})

    assert result.success is False
    assert result.error == "broken"
    assert result.error_kind == "tool_error"


def test_registry_rejects_invalid_tool_result_model():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="bad_result",
        description="Return invalid result",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.READ,
        handler=lambda args: ToolResult(success=True, data="not-a-dict"),
    ))

    result = registry.execute("bad_result", {})

    assert result.success is False
    assert result.error_kind == "invalid_result"
    assert "data" in result.error


def test_registry_validates_all_tool_result_fields():
    invalid_results = [
        (ToolResult(success="yes"), "success"),
        (ToolResult(success=True, output=123), "output"),
        (ToolResult(success=False, error=123), "error"),
        (ToolResult(success=True, changed_files=[1]), "changed_files"),
        (ToolResult(success=True, process_id=123), "process_id"),
        (ToolResult(success=True, requires_confirmation="yes"), "requires_confirmation"),
        (ToolResult(success=True, confirmation_reason=123), "confirmation_reason"),
        (ToolResult(success=False, error="bad", error_kind=123), "error_kind"),
    ]

    for index, (invalid, field) in enumerate(invalid_results):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name=f"bad_{index}",
            description="Return invalid result",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ,
            handler=lambda args, value=invalid: value,
        ))

        result = registry.execute(f"bad_{index}", {})

        assert result.success is False
        assert result.error_kind == "invalid_result"
        assert field in result.error


def test_registry_rejects_semantically_inconsistent_tool_results():
    invalid_results = [
        (ToolResult(success=True, error="unexpected"), "成功结果不能包含 error"),
        (ToolResult(success=True, error_kind="tool_error"), "成功结果不能包含 error_kind"),
        (ToolResult(success=True, requires_confirmation=True, confirmation_reason="confirm"), "成功结果不能等待确认"),
        (ToolResult(success=False), "失败结果必须包含 error"),
        (ToolResult(success=False, requires_confirmation=True), "等待确认必须包含 confirmation_reason"),
    ]

    for index, (invalid, expected) in enumerate(invalid_results):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            name=f"inconsistent_{index}",
            description="Return inconsistent result",
            input_schema={"type": "object", "properties": {}},
            risk=ToolRisk.READ,
            handler=lambda args, value=invalid: value,
        ))

        result = registry.execute(f"inconsistent_{index}", {})

        assert result.success is False
        assert result.error_kind == "invalid_result"
        assert expected in result.error


def test_registry_validates_destructive_input_before_permission_prompt():
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="delete_path",
        description="Delete a path",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        risk=ToolRisk.DESTRUCTIVE,
        handler=lambda args: ToolResult.ok(output="deleted"),
    ))

    result = registry.execute("delete_path", {})

    assert result.error_kind == "invalid_input"
    assert result.requires_confirmation is False


def test_registry_can_upgrade_risk_from_tool_arguments():
    calls = []
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        name="run_command",
        description="Run command",
        input_schema={"type": "object", "properties": {}},
        risk=ToolRisk.PROCESS,
        risk_resolver=lambda args: (
            ToolRisk.DESTRUCTIVE if "Remove-Item" in args.get("command", "") else ToolRisk.PROCESS
        ),
        handler=lambda args: calls.append(args) or ToolResult.ok(output="ran"),
    ))

    pending = registry.execute("run_command", {"command": "Remove-Item file.txt"})

    assert pending.requires_confirmation is True
    assert calls == []
