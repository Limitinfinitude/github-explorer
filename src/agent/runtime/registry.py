from dataclasses import dataclass
from typing import Callable

from .models import ToolResult, ToolRisk
from .permissions import PermissionGate
from .schema import schema_issue
from .tracing import current_tool_call_context, tool_span


ToolHandler = Callable[[dict], ToolResult]
RiskResolver = Callable[[dict], ToolRisk]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    risk: ToolRisk
    handler: ToolHandler
    risk_resolver: RiskResolver | None = None


class ToolRegistry:
    def __init__(self, permission_gate: PermissionGate | None = None, trace_metadata=None) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        self._permission_gate = permission_gate or PermissionGate()
        self._trace_metadata = trace_metadata or (lambda: {})

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"工具已注册: {definition.name}")
        self._definitions[definition.name] = definition

    def schemas(self) -> list[dict]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
            }
            for definition in self._definitions.values()
        ]

    def requires_confirmation(self, name: str, args: dict) -> bool:
        definition = self._definitions.get(name)
        if definition is None:
            return False
        risk = definition.risk_resolver(args) if definition.risk_resolver else definition.risk
        return self._permission_gate.requires_confirmation(risk)

    def execute(self, name: str, args: dict, *, confirmed: bool = False) -> ToolResult:
        definition = self._definitions.get(name)
        if definition is None:
            return ToolResult.fail(f"未知工具: {name}", error_kind="unknown_tool")

        validation_issue = schema_issue(args, definition.input_schema)
        if validation_issue:
            return ToolResult.fail(
                f"工具参数无效: {validation_issue.path}: {validation_issue.message}",
                error_kind="invalid_input",
                data={"validation": validation_issue.to_dict()},
            )

        risk = definition.risk_resolver(args) if definition.risk_resolver else definition.risk
        if self._permission_gate.requires_confirmation(risk) and not confirmed:
            return ToolResult(
                success=False,
                requires_confirmation=True,
                confirmation_reason=self._permission_gate.reason(risk, name),
                error_kind="permission_denied",
            )

        try:
            metadata = {**self._trace_metadata(), **current_tool_call_context()}
            with tool_span(name, args, metadata):
                result = definition.handler(args)
        except Exception as exc:
            return ToolResult.fail(str(exc), error_kind="tool_error")
        if not isinstance(result, ToolResult):
            return ToolResult.fail(f"工具 {name} 返回了无效结果", error_kind="invalid_result")
        result_error = result.validation_error()
        if result_error:
            return ToolResult.fail(
                f"工具 {name} 返回了无效结果: {result_error}",
                error_kind="invalid_result",
            )
        if not result.success and result.error_kind is None:
            result.error_kind = "tool_error"
        return result
