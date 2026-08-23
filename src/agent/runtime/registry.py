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
        # 工具按名字典序输出（DSH orderTools 同款）：工具 schema 是请求前缀的
        # 一部分，顺序一旦因注册时序漂移，整个前缀缓存失效，直接推高成本。
        definitions = sorted(
            self._definitions.values(),
            key=lambda definition: definition.name,
        )
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "input_schema": definition.input_schema,
            }
            for definition in definitions
        ]

    def requires_confirmation(self, name: str, args: dict) -> bool:
        definition = self._definitions.get(name)
        if definition is None:
            return False
        risk = definition.risk_resolver(args) if definition.risk_resolver else definition.risk
        return self._permission_gate.requires_confirmation(risk)

    @staticmethod
    def _schema_hint(definition: ToolDefinition) -> str:
        """从 input_schema 生成参数说明与最小示例，供参数校验失败时回显。

        模型传错参数结构时（如把 edits 数组的 path 提到顶层），光说
        "不允许的字段"不足以自我纠正；回显正确结构一次即可（Aider 失败
        反馈四件套的"参照"部分）。
        """
        props = definition.input_schema.get("properties") or {}
        required = set(definition.input_schema.get("required") or [])
        parts = []
        example: dict = {}
        for name, spec in props.items():
            kind = spec.get("type", "?")
            mark = "必填" if name in required else "可选"
            extra = ""
            if kind == "array" and isinstance(spec.get("items"), dict):
                item_props = spec["items"].get("properties") or {}
                inner = ", ".join(
                    f"{k}:{v.get('type', '?')}" for k, v in item_props.items()
                )
                if inner:
                    extra = f"，每项 {{{inner}}}"
                example[name] = []
            elif kind == "string":
                example[name] = "..."
            elif kind == "integer":
                example[name] = 1
            parts.append(f"{name}({kind},{mark}{extra})")
        if not parts:
            return ""
        return f"正确参数：{'; '.join(parts)}。最小示例：{example}"

    def execute(self, name: str, args: dict, *, confirmed: bool = False) -> ToolResult:
        definition = self._definitions.get(name)
        if definition is None:
            return ToolResult.fail(f"未知工具: {name}", error_kind="unknown_tool")

        validation_issue = schema_issue(args, definition.input_schema)
        if validation_issue:
            hint = self._schema_hint(definition)
            message = (
                f"工具参数无效: {validation_issue.path}: {validation_issue.message}"
            )
            if hint:
                message = f"{message}。{hint}"
            return ToolResult.fail(
                message,
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
