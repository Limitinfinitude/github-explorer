"""In-process context propagated from the runtime to tool handlers."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_TOOL_CALL_CONTEXT: ContextVar[dict[str, str]] = ContextVar(
    "tool_call_context",
    default={},
)


@contextmanager
def tool_call_context(**metadata: str) -> Iterator[None]:
    token = _TOOL_CALL_CONTEXT.set({
        key: str(value) for key, value in metadata.items() if value is not None
    })
    try:
        yield
    finally:
        _TOOL_CALL_CONTEXT.reset(token)


def current_tool_call_context() -> dict[str, str]:
    return dict(_TOOL_CALL_CONTEXT.get())


def sanitize(value: Any, key: str = "") -> Any:
    normalized_key = key.casefold()
    usage_keys = {"input_tokens", "output_tokens", "total_tokens"}
    if (
        normalized_key not in usage_keys
        and any(marker in normalized_key for marker in ("key", "token", "secret", "password", "authorization"))
    ):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 4_000:
        return value[:4_000] + "...[truncated]"
    return value


@contextmanager
def tool_span(name: str, args: dict, metadata: dict) -> Iterator[None]:
    """Keep registry call sites stable; Runtime owns durable event recording."""
    yield
