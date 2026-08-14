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


def validate_schema(value: Any, schema: dict, path: str = "$") -> str | None:
    expected = schema.get("type")
    expected_type = _TYPE_NAMES.get(expected)
    if expected_type is not None and not _matches_type(value, expected):
        return f"{path}: 期望 {expected}，实际为 {type(value).__name__}"

    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: 值必须是 {schema['enum']} 之一"

    if expected == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                return f"{path}.{name}: 缺少必填字段"
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    return f"{path}.{name}: 不允许的字段"
        for name, item in value.items():
            if name not in properties:
                continue
            error = validate_schema(item, properties[name], f"{path}.{name}")
            if error:
                return error

    if expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                error = validate_schema(item, item_schema, f"{path}[{index}]")
                if error:
                    return error

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            return f"{path}: 不能小于 {minimum}"
        if maximum is not None and value > maximum:
            return f"{path}: 不能大于 {maximum}"

    return None


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    expected_type = _TYPE_NAMES.get(expected)
    return expected_type is None or isinstance(value, expected_type)
