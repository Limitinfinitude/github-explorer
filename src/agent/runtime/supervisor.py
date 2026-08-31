import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from .state_schema import normalize_state


_TERMINAL_STATUSES = {
    "completed", "incomplete", "failed", "blocked", "cancelled", "interrupted",
}
_TERMINAL_EVENT_TYPES = {
    "task_completed": "completed",
    "task_failed": "failed",
    "task_waiting_approval": "waiting_approval",
    "task_cancelled": "cancelled",
    "task_finished": None,
}


class AgentTaskSupervisor:
    """Own Agent coroutines independently from HTTP event subscribers."""

    def __init__(self, runtime: Any, task_store: Any, *, poll_interval: float = 0.1) -> None:
        self.runtime = runtime
        self.task_store = task_store
        self.poll_interval = poll_interval
        self._tasks: dict[str, asyncio.Task] = {}

    def start(
        self,
        session_id: str,
        message: str,
        *,
        history: list[dict] | None = None,
        model_context: dict | None = None,
        task_id: str | None = None,
        approval_mode: str = "confirm",
        plan_mode: bool = False,
    ) -> str:
        active = self.task_store.get_latest_nonterminal_agent_task(session_id)
        if active is not None:
            raise ValueError(f"当前会话已有任务正在运行: {active['task_id']}")

        task_id = task_id or uuid.uuid4().hex
        self.runtime.register_task(session_id, task_id)
        worker = asyncio.create_task(self._consume(
            session_id,
            message,
            history=history or [],
            task_id=task_id,
            model_context=model_context or {},
            approval_mode=approval_mode,
            plan_mode=plan_mode,
        ))
        self._tasks[task_id] = worker
        worker.add_done_callback(lambda _: self._tasks.pop(task_id, None))
        return task_id

    def resume_interrupted(
        self,
        session_id: str,
        task_id: str,
        *,
        model_context: dict | None = None,
    ) -> str:
        state = self.task_store.get_agent_task(task_id)
        state = normalize_state(state) if state is not None else None
        if (
            state is None
            or state.get("session_id") != session_id
            or state.get("status") != "interrupted"
            or not state["run"].get("resume_available")
        ):
            raise ValueError(f"任务不可恢复: {task_id}")
        worker = self._tasks.get(task_id)
        if worker is not None and not worker.done():
            raise ValueError(f"任务正在恢复: {task_id}")
        worker = asyncio.create_task(self._consume_resume(
            session_id,
            task_id,
            model_context=model_context or {},
        ))
        self._tasks[task_id] = worker
        worker.add_done_callback(lambda _: self._tasks.pop(task_id, None))
        return task_id

    async def wait(self, task_id: str) -> None:
        worker = self._tasks.get(task_id)
        if worker is not None:
            await asyncio.shield(worker)

    async def subscribe(
        self,
        task_id: str,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[dict]:
        cursor = max(0, int(after_sequence))
        while True:
            task = self.task_store.get_agent_task(task_id)
            if task is None:
                raise KeyError(f"任务不存在: {task_id}")

            events = self.task_store.get_agent_events(task_id, after_sequence=cursor)
            for stored in events:
                sequence = int(stored["sequence"])
                if sequence <= cursor:
                    continue
                cursor = sequence
                event = self._project_event(stored, task)
                yield event
                if event["type"] == "done":
                    return

            if task.get("status") in _TERMINAL_STATUSES:
                yield {
                    "type": "done",
                    "task_id": task_id,
                    "session_id": task.get("session_id", ""),
                    "sequence": cursor + 1,
                    "content": task.get("final_text", ""),
                    "status": task.get("status"),
                }
                return
            await asyncio.sleep(self.poll_interval)

    async def _consume(self, session_id: str, message: str, **kwargs) -> None:
        task_id = kwargs["task_id"]
        # 预热 MCP 连接（首次启动可能拉取 npx 包，限时避免拖慢任务；失败不阻断）。
        # 测试环境通过 GE_DISABLE_MCP_PREWARM 关闭，避免 npx 拖住测试套件。
        if os.environ.get("GE_DISABLE_MCP_PREWARM", "").casefold() not in {"1", "true", "yes"}:
            try:
                from agent.mcp_client import ensure_mcp_connected
                await asyncio.wait_for(ensure_mcp_connected(), timeout=6)
            except Exception:
                pass
        try:
            async for _ in self.runtime.run(session_id, message, **kwargs):
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state = self.task_store.get_agent_task(task_id) or {
                "task_id": task_id,
                "session_id": session_id,
                "user_message": message,
                "summary": {},
            }
            normalize_state(state)
            if state.get("status") not in _TERMINAL_STATUSES:
                state["status"] = "failed"
                state["final_text"] = f"Agent 后台任务失败（{type(exc).__name__}）：{str(exc).strip() or '未提供错误信息'}"
                self.task_store.save_agent_task(state)
                self.task_store.record_agent_event({
                    "task_id": task_id,
                    "session_id": session_id,
                    "type": "task_failed",
                    "content": state["final_text"],
                    "status": "failed",
                })

    async def _consume_resume(self, session_id: str, task_id: str, *, model_context: dict) -> None:
        try:
            async for _ in self.runtime.resume_interrupted(
                session_id,
                task_id,
                model_context=model_context,
            ):
                pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state = self.task_store.get_agent_task(task_id)
            if state is not None and state.get("status") not in _TERMINAL_STATUSES:
                normalize_state(state)
                state["status"] = "failed"
                state["run"]["resume_available"] = False
                state["final_text"] = f"恢复任务失败（{type(exc).__name__}）：{str(exc).strip() or '未提供错误信息'}"
                self.task_store.save_agent_task(state)
                self.task_store.record_agent_event({
                    "task_id": task_id,
                    "session_id": session_id,
                    "type": "task_failed",
                    "content": state["final_text"],
                    "status": "failed",
                })

    @staticmethod
    def _project_event(stored: dict, task: dict) -> dict:
        event_type = stored["type"]
        payload = stored.get("payload") or {}
        projected = {
            "task_id": stored["task_id"],
            "session_id": stored.get("session_id", ""),
            "sequence": stored["sequence"],
            **payload,
            "type": event_type,
        }
        if event_type in _TERMINAL_EVENT_TYPES:
            projected["type"] = "done"
            projected["status"] = (
                _TERMINAL_EVENT_TYPES[event_type]
                or payload.get("status")
                or task.get("status")
            )
            projected["content"] = payload.get("content") or task.get("final_text", "")
        return projected
