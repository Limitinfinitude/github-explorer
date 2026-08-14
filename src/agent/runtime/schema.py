from dataclasses import asdict, dataclass
from typing import Any


_TYPE_NAMES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


@dataclass(frozen=True)
class SchemaIssue:
    code: str
    path: str
    message: str
    expected: str
    actual: str
    suggestion: str

    def to_dict(self) -> dict:
        return asdict(self)


def validate_schema(value: Any, schema: dict, path: str = "$") -> str | None:
    issue = schema_issue(value, schema, path)
    return f"{issue.path}: {issue.message}" if issue else None


def schema_issue(value: Any, schema: dict, path: str = "$") -> SchemaIssue | None:
    expected = schema.get("type")
    expected_type = _TYPE_NAMES.get(expected)
    if expected_type is not None and not _matches_type(value, expected):
        actual = type(value).__name__
        return SchemaIssue(
            code="type", path=path, message=f"期望 {expected}，实际为 {actual}",
            expected=expected, actual=actual,
            suggestion=f"将 {path} 改为 {expected} 类型",
        )

    if "enum" in schema and value not in schema["enum"]:
        allowed = schema["enum"]
        return SchemaIssue(
            code="enum", path=path, message=f"值必须是 {allowed} 之一",
            expected=" | ".join(map(str, allowed)), actual=repr(value),
            suggestion=f"将 {path} 改为允许值之一: {allowed}",
        )

    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                field_path = f"{path}.{name}"
                field_type = str(properties.get(name, {}).get("type", "value"))
                return SchemaIssue(
                    code="required", path=field_path, message="缺少必填字段",
                    expected=field_type, actual="missing",
                    suggestion=f"补充字段 {field_path}，值类型应为 {field_type}",
                )
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    field_path = f"{path}.{name}"
                    return SchemaIssue(
                        code="additional_property", path=field_path, message="不允许的字段",
                        expected="已声明字段", actual=name,
                        suggestion=f"删除未声明字段 {field_path}",
                    )
        for name, item in value.items():
            if name not in properties:
                continue
            issue = schema_issue(item, properties[name], f"{path}.{name}")
            if issue:
                return issue

    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issue = schema_issue(item, item_schema, f"{path}[{index}]")
                if issue:
                    return issue

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            return SchemaIssue(
                code="minimum", path=path, message=f"不能小于 {minimum}",
                expected=f">= {minimum}", actual=str(value),
                suggestion=f"将 {path} 调整为不小于 {minimum}",
            )
        if maximum is not None and value > maximum:
            return SchemaIssue(
                code="maximum", path=path, message=f"不能大于 {maximum}",
                expected=f"<= {maximum}", actual=str(value),
                suggestion=f"将 {path} 调整为不大于 {maximum}",
            )

    return None


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected_type = _TYPE_NAMES.get(expected)
    return expected_type is None or isinstance(value, expected_type)
