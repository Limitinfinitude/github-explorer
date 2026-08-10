from contextlib import contextmanager
from typing import Any, Iterator

from langsmith import trace
from langsmith.run_helpers import tracing_context


_SENSITIVE_KEYS = {"api_key", "authorization", "token", "password", "secret"}


def sanitize(value: Any, key: str = "") -> Any:
    if key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): sanitize(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 4_000:
        return value[:4_000] + "...[truncated]"
    return value


class _NoopRun:
    def set(self, **kwargs) -> None:
        return None


@contextmanager
def agent_workflow(
    *,
    session_id: str,
    task_id: str,
    workspace: str,
    model: dict,
    message: str,
) -> Iterator[Any]:
    trace_cm = None
    context_cm = None
    try:
        trace_cm = trace(
            "Agent_Workflow",
            run_type="chain",
            inputs={"message": message},
            metadata={
                "session_id": session_id,
                "task_id": task_id,
                "initial_workspace": workspace,
                "model_config": sanitize({
                    key: value for key, value in model.items() if key.lower() not in _SENSITIVE_KEYS
                }),
            },
        )
        run = trace_cm.__enter__()
        context_cm = tracing_context(parent=run)
        context_cm.__enter__()
    except Exception:
        yield _NoopRun()
        return

    try:
        yield run
    except BaseException as exc:
        if context_cm is not None:
            try:
                context_cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
        if trace_cm is not None:
            try:
                trace_cm.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
        raise
    else:
        if context_cm is not None:
            try:
                context_cm.__exit__(None, None, None)
            except Exception:
                pass
        if trace_cm is not None:
            try:
                trace_cm.__exit__(None, None, None)
            except Exception:
                pass


@contextmanager
def tool_span(name: str, args: dict, metadata: dict) -> Iterator[None]:
    trace_cm = None
    try:
        trace_cm = trace(
            f"Tool:{name}",
            run_type="tool",
            inputs=sanitize(args),
            metadata=sanitize(metadata),
        )
        trace_cm.__enter__()
    except Exception:
        yield
        return
    try:
        yield
    except BaseException as exc:
        try:
            trace_cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
        raise
    else:
        try:
            trace_cm.__exit__(None, None, None)
        except Exception:
            pass
