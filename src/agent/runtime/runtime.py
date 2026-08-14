import asyncio
import json
import re
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .compaction import CompactionEngine
from .acceptance import WorkProductEvaluator
from .context import ContextEngine
from .evidence import normalize_evidence_path
from .instructions import InstructionLoader
from .models import ToolResult
from .registry import ToolRegistry
from .response_format import format_final_response
from .tool_calls import (
    TERMINAL_TOOL_CALL_STATUSES,
    normalize_tool_calls,
    reconcile_tool_messages,
    tool_recovery_key,
)
from .tracing import tool_call_context
from .workspace import WorkspaceManager


RegistryFactory = Callable[[str], ToolRegistry]

_DIRECT_CHAT_RE = re.compile(
    r"^\s*(?:你好|您好|嗨|hi|hello|"
    r"你(?:能|可以|会)做什么|你会哪些(?:事情|操作)|"
    r"你的(?:能力|功能|本地操作工具)有哪些|介绍一下你自己)\s*[?？!！。]*\s*$",
    re.IGNORECASE,
)
_UNFINISHED_TEXT_RE = re.compile(r"[\w\u3400-\u9fff]$")
_EXPLICIT_UNFINISHED_RE = re.compile(
    r"(?:`?\[未完成\]`?|(?:^|[\s：:])未完成(?:$|[\s，。；;：:]))",
    re.IGNORECASE | re.MULTILINE,
)
_CLAUSE_MARK_RE = re.compile(r"[，。！？,.!?；;：:\n]")
_DIAGNOSTIC_TOOLS = {
    "list_directory", "read_file", "search_text", "repo_map", "detect_project",
    "get_process", "list_processes", "check_port", "wait_http",
}
_STAGE_TOOLS = {
    "inspect": _DIAGNOSTIC_TOOLS - {"check_port", "wait_http"},
    "test": {"verify_project"},
    "run": {"start_process", "get_process", "list_processes", "stop_process", "check_port", "wait_http", "http_request"},
}
_STAGE_LIMITS = {"inspect": 24, "implement": 16, "test": 8, "run": 8}
_CONTINUE_TASK_RE = re.compile(
    r"^\s*(?:(?:继续|接着)(?:做|来|进行|推进|优化|完善)?(?:一下)?(?:吧)?|"
    r"按(?:照)?\s*(?:todo|计划)\s*继续(?:进行|推进)?(?:吧)?)\s*[。.!！]*\s*$",
    re.IGNORECASE,
)


class LocalAgentRuntime:
    def __init__(
        self,
        workspaces: WorkspaceManager,
        registry_factory: RegistryFactory,
        llm_call: Callable[..., Any],
        max_rounds: int = 12,
        max_identical_failures: int = 3,
        max_context_tokens: int = 128_000,
        max_output_tokens: int = 12_000,
        diagnostic_tool_budget: int = 8,
        replan_extra_rounds: int = 4,
        tool_result_preview_chars: int = 30_000,
        task_store: Any | None = None,
        context_engine: ContextEngine | None = None,
        compaction_engine: CompactionEngine | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.registry_factory = registry_factory
        self.llm_call = llm_call
        self.max_rounds = max_rounds
        self.max_identical_failures = max_identical_failures
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens
        self.diagnostic_tool_budget = diagnostic_tool_budget
        self.replan_extra_rounds = replan_extra_rounds
        self.tool_result_preview_chars = max(1, int(tool_result_preview_chars))
        self.task_store = task_store
        self.context_engine = context_engine
        self.compaction_engine = compaction_engine or CompactionEngine()
        self.work_product_evaluator = WorkProductEvaluator()
        self._task_cache: dict[str, dict] = {}
        self._cancelled_tasks: set[str] = set()

    def register_task(self, session_id: str, task_id: str) -> None:
        if self._load_task(task_id) is not None:
            return
        self._save_task({
            "task_id": task_id,
            "session_id": session_id,
            "user_message": "",
            "status": "pending",
            "summary": {
                "changed_files": [], "verification": [], "processes": [], "successful_tools": [],
                "stage_budgets": self._new_stage_budgets(),
            },
            "plan": [],
            "tool_call_ledger": {},
            "active_batch": None,
        })

    async def confirm(self, session_id: str, task_id: str, approved: bool) -> ToolResult:
        events = [event async for event in self.resume(session_id, task_id, approved)]
        error = next((event.get("content") for event in events if event["type"] == "error"), None)
        if error:
            return ToolResult.fail(error, data={"events": events})
        done = next((event for event in reversed(events) if event["type"] == "done"), None)
        return ToolResult.ok(output=done.get("content", "") if done else "", data={"events": events})

    def cancel(self, session_id: str, task_id: str) -> ToolResult:
        state = self._load_task(task_id)
        if state is None or state.get("session_id") != session_id:
            return ToolResult.fail(f"任务不存在或不属于当前会话: {task_id}")
        if state.get("status") == "cancelled":
            return ToolResult.ok(output="任务已取消", data={"status": "cancelled"})
        if state.get("status") in {"completed", "incomplete", "failed", "blocked", "interrupted"}:
            return ToolResult.fail(f"任务已结束，无法取消: {task_id}")

        self._cancelled_tasks.add(task_id)
        state["status"] = "cancelled"
        state["final_text"] = "任务已由用户取消。"
        self._settle_open_tool_calls(state, "Task cancelled by user.")
        self._save_task(state)
        self._record_event({
            "session_id": session_id,
            "task_id": task_id,
            "type": "task_cancelled",
            "content": state["final_text"],
        })
        return ToolResult.ok(output=state["final_text"], data={"status": "cancelled"})

    async def run(
        self,
        session_id: str,
        user_message: str,
        history: list[dict] | None = None,
        task_id: str | None = None,
        model_context: dict | None = None,
    ) -> AsyncIterator[dict]:
        task_id = task_id or uuid.uuid4().hex
        registered = self._load_task(task_id)
        if (
            registered is not None
            and registered.get("session_id") == session_id
            and registered.get("status") == "cancelled"
        ):
            yield {
                "session_id": session_id,
                "task_id": task_id,
                "type": "done",
                "content": registered.get("final_text", "任务已由用户取消。"),
                "status": "cancelled",
            }
            return
        workspace = self.workspaces.get(session_id)
        current_path = workspace.current_path or workspace.root
        instruction_context = InstructionLoader(workspace.root, current_path).load()
        project_memories = self._search_project_memories(str(workspace.root), user_message)
        workspace_snapshot = self._workspace_snapshot(workspace.root, current_path)
        safe_model_context = {
            key: str(value)
            for key, value in (model_context or {}).items()
            if key in {"id", "protocol", "base_url"} and value is not None
        }
        base_event = {"session_id": session_id, "task_id": task_id}
        direct_chat = bool(_DIRECT_CHAT_RE.fullmatch(user_message))
        repo_map = ""
        if self.context_engine is not None and not direct_chat:
            map_result = await asyncio.to_thread(self.context_engine.repo_map, session_id)
            if map_result.success:
                repo_map = map_result.output

        plan = self._build_plan(user_message)
        requires_material_change = self._request_requires_material_change(user_message)
        acceptance_criteria = self._extract_acceptance_criteria(user_message)
        explicit_acceptance = bool(acceptance_criteria)
        session_requirements = []
        implicit_requirement_positions = []
        engages_backlog = bool(
            acceptance_criteria
            or requires_material_change
            or _CONTINUE_TASK_RE.fullmatch(user_message)
        )
        if (
            engages_backlog
            and self.task_store is not None
            and hasattr(self.task_store, "merge_session_requirements")
            and hasattr(self.task_store, "list_session_requirements")
        ):
            existing_requirements = self.task_store.list_session_requirements(
                session_id,
                status="pending",
            )
            new_requirements = [item["text"] for item in acceptance_criteria]
            if not new_requirements and requires_material_change:
                new_requirements = [user_message]
            if new_requirements:
                merged_requirements = self.task_store.merge_session_requirements(
                    session_id,
                    new_requirements,
                    source_task_id=task_id,
                )
                if not explicit_acceptance and not existing_requirements:
                    implicit_requirement_positions = [
                        item["position"] for item in merged_requirements
                    ]
            session_requirements = self.task_store.list_session_requirements(
                session_id,
                status="pending",
            )
            if explicit_acceptance or existing_requirements or _CONTINUE_TASK_RE.fullmatch(user_message):
                acceptance_criteria = [
                    {"id": item["position"], "text": item["text"]}
                    for item in session_requirements
                ]
            else:
                acceptance_criteria = []
        if acceptance_criteria:
            plan.append(f"逐项核对 {len(acceptance_criteria)} 条验收要求并报告证据")
        state = {
            "task_id": task_id,
            "session_id": session_id,
            "user_message": user_message,
            "status": "running",
            "messages": [*(history or []), {"role": "user", "content": user_message}],
            "round": 0,
            "round_limit": self.max_rounds,
            "diagnostic_tool_count": 0,
            "material_tool_seen": False,
            "requires_material_change": requires_material_change,
            "replanned": False,
            "failure_counts": {},
            "schema_repair_counts": {},
            "unrecovered_failures": {},
            "active_batch": None,
            "tool_call_ledger": {},
            "repo_map": repo_map,
            "workspace_root": str(workspace.root),
            "current_path": str(current_path),
            "workspace_snapshot": workspace_snapshot,
            "model_context": safe_model_context,
            "instruction_context": instruction_context.rendered,
            "instruction_sources": [asdict(source) for source in instruction_context.sources],
            "instruction_warnings": instruction_context.warnings,
            "project_memories": project_memories,
            "context_handoff": None,
            "compaction_count": 0,
            "compacted_message_count": 0,
            "summary": {
                "changed_files": [], "verification": [], "processes": [], "successful_tools": [],
                "stage_budgets": self._new_stage_budgets(),
            },
            "plan": plan,
            "acceptance_criteria": acceptance_criteria,
            "session_requirements": session_requirements,
            "implicit_requirement_positions": implicit_requirement_positions,
            "context_emitted": False,
            "allow_tools": not direct_chat,
        }
        self._save_task(state)
        self._record_event({
            **base_event,
            "type": "task_started",
            "workspace_root": str(workspace.root),
            "current_path": str(current_path),
            "workspace_snapshot": workspace_snapshot,
            "model_context": safe_model_context,
            "requirement_backlog": session_requirements,
        })
        if session_requirements:
            self._record_event({
                **base_event,
                "type": "requirement_backlog_loaded",
                "items": session_requirements,
            })

        try:
            registry = self.registry_factory(session_id)
            async for event in self._drive(state, registry, str(workspace.root)):
                self._record_stream_event(state, event)
                yield event
        finally:
            if state.get("status") == "running":
                self._settle_open_tool_calls(state, "Task stream closed before completion.")
                state["status"] = "interrupted"
                self._save_task(state)
                self._record_event({
                    **base_event,
                    "type": "task_interrupted",
                    "content": "Task stream closed before completion.",
                })

    async def resume(
        self,
        session_id: str,
        task_id: str,
        approved: bool,
    ) -> AsyncIterator[dict]:
        state = self._load_task(task_id)
        base_event = {"session_id": session_id, "task_id": task_id}
        if state is None or state.get("session_id") != session_id:
            error = f"没有待确认的任务: {task_id}"
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}
            return
        if state.get("status") != "waiting_approval" or not state.get("active_batch"):
            error = f"任务不在等待确认状态: {task_id}"
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}
            return

        workspace = self._restore_task_workspace(state)
        registry = self.registry_factory(session_id)
        pending_batch = state["active_batch"]
        pending_call = pending_batch["tool_uses"][pending_batch["next_index"]]
        self._record_event({
            **base_event,
            "type": "approval_resolved",
            "approved": approved,
            "batch_id": pending_batch["batch_id"],
            "call_id": pending_call["id"],
            "tool_name": pending_call["name"],
        })
        state["status"] = "running"
        events, paused = await self._execute_active_batch(
            state, registry, base_event, approval_decision=approved,
        )
        for event in events:
            self._record_stream_event(state, event)
            yield event
        if paused:
            return
        async for event in self._drive(state, registry, str(workspace.root)):
            self._record_stream_event(state, event)
            yield event

    def _restore_task_workspace(self, state: dict):
        session_id = state["session_id"]
        workspace = self.workspaces.bind(session_id, state["workspace_root"])
        current_path = state.get("current_path")
        if current_path:
            self.workspaces.set_current_path(session_id, current_path)
            workspace = self.workspaces.get(session_id)
        return workspace

    async def _drive(self, state: dict, registry: ToolRegistry, workspace_root: str) -> AsyncIterator[dict]:
        base_event = {"session_id": state["session_id"], "task_id": state["task_id"]}
        system = self._system_prompt(
            workspace_root,
            state.get("repo_map", ""),
            state.get("instruction_context", ""),
            state.get("project_memories", []),
            state.get("acceptance_criteria", []),
        )
        try:
            while state["round"] < state.get("round_limit", self.max_rounds):
                state["round"] += 1
                self._save_task(state)
                response = await self._call_model(
                    state,
                    "reasoning",
                    system=system,
                    messages=self._fit_context(
                        system,
                        reconcile_tool_messages(
                            state["messages"], state.get("tool_call_ledger", {}),
                        ),
                        self.max_output_tokens,
                        state,
                    ),
                    tools=registry.schemas() if state.get("allow_tools", True) else [],
                    max_tokens=self.max_output_tokens,
                    temperature=0.2,
                )
                cancelled = self._cancelled_event(state, base_event)
                if cancelled:
                    yield cancelled
                    return
                tool_uses = response.get("tool_uses", [])
                response_text = response.get("text", "")
                if not tool_uses:
                    stop_reason = str(response.get("stop_reason", ""))
                    explicit_truncation = stop_reason in {"max_tokens", "length"}
                    incomplete_plain_chat = (
                        not state.get("allow_tools", True)
                        and len(response_text.strip()) >= 8
                        and bool(_CLAUSE_MARK_RE.search(response_text[:-1]))
                        and bool(_UNFINISHED_TEXT_RE.search(response_text.rstrip()))
                    )
                    if explicit_truncation or incomplete_plain_chat:
                        continuation = await self._call_model(
                            state,
                            "continuation",
                            system=(
                                system
                                + "\n\n上一段回复意外中断。请仅从中断处继续，把当前回答完整结束；"
                                "不要重复已经输出的内容，不要调用工具。"
                            ),
                            messages=self._fit_context(system, reconcile_tool_messages([
                                *state["messages"],
                                {"role": "assistant", "content": response_text},
                                {"role": "user", "content": "请从中断处继续并完整结束回答。"},
                            ], state.get("tool_call_ledger", {})), 4_000),
                            tools=[],
                            max_tokens=4_000,
                            temperature=0.2,
                        )
                        response_text = response_text.rstrip() + continuation.get("text", "").lstrip()
                    missing_criteria = self._missing_acceptance_criteria(
                        response_text, state.get("acceptance_criteria", []),
                    )
                    if missing_criteria:
                        response_text = (
                            response_text.rstrip()
                            + "\n\n未逐项覆盖验收清单："
                            + "、".join(str(item) for item in missing_criteria)
                        )
                    evaluation = self.work_product_evaluator.evaluate(
                        criteria=state.get("acceptance_criteria", []),
                        response_text=response_text,
                        summary=state["summary"],
                    )
                    acceptance = evaluation["requirement_coverage"]["items"]
                    if acceptance:
                        state["summary"]["acceptance"] = acceptance
                        state["summary"]["work_product_evaluation"] = evaluation
                        self._settle_session_requirements(state, acceptance)
                        response_text = re.sub(
                            r"\s*\[证据:(?:file|check|process):[^\]]+\]",
                            "",
                            response_text,
                            flags=re.IGNORECASE,
                        )
                    final_text = format_final_response(response_text, state["summary"])
                    has_failed_verification = any(
                        not check.get("success", False)
                        for check in state["summary"].get("verification", [])
                    )
                    missing_material_change = (
                        state.get("requires_material_change", False)
                        and not state.get("material_tool_seen", False)
                    )
                    if missing_material_change:
                        final_text = (
                            final_text.rstrip()
                            + "\n\n未完成：本次任务要求执行修改，但未记录任何文件或项目变更。"
                        )
                    status = "incomplete" if (
                        state.get("unrecovered_failures")
                        or has_failed_verification
                        or missing_material_change
                        or missing_criteria
                        or self._has_explicit_unfinished(response_text)
                        or (acceptance and not all(
                            item["status"] == "passed" for item in acceptance
                        ))
                    ) else "completed"
                    state["status"] = status
                    state["final_text"] = final_text
                    if status == "completed" and state.get("implicit_requirement_positions"):
                        self._settle_implicit_session_requirements(state)
                    self._save_task(state)
                    if acceptance:
                        yield {
                            **base_event,
                            "type": "acceptance",
                            "success": all(item["status"] == "passed" for item in acceptance),
                            "items": acceptance,
                        }
                    finalization = self._finalization_event(base_event, status, final_text, state["summary"])
                    if self._has_finalization_facts(finalization):
                        yield finalization
                    yield {**base_event, "type": "token", "content": final_text}
                    yield {**base_event, "type": "done", "content": final_text, "status": status}
                    return

                if not state.get("context_emitted"):
                    state["context_emitted"] = True
                    self._save_task(state)
                    yield {**base_event, "type": "plan", "steps": state["plan"]}
                    if state.get("repo_map"):
                        yield {
                            **base_event,
                            "type": "repo_map",
                            "content": state["repo_map"],
                            "files_scanned": state["repo_map"].count("\n") + 1,
                        }

                tool_uses = normalize_tool_calls(
                    tool_uses,
                    set(state.get("tool_call_ledger", {})),
                )
                repeated_diagnostics = (
                    state.get("replanned")
                    and not state.get("material_tool_seen")
                    and int(state.get("diagnostic_tool_count", 0)) >= self.diagnostic_tool_budget
                    and tool_uses
                    and all(tool_use["name"] in _DIAGNOSTIC_TOOLS for tool_use in tool_uses)
                )
                if repeated_diagnostics:
                    message = (
                        "诊断预算已用尽，重规划后仍只请求诊断工具；"
                        "Harness 已停止继续扩散读取，本次任务未完成。"
                    )
                    state["status"] = "incomplete"
                    state["final_text"] = message
                    self._save_task(state)
                    yield {
                        **base_event,
                        "type": "budget_warning",
                        "diagnostic_tool_count": state["diagnostic_tool_count"],
                        "round_limit": state["round_limit"],
                        "message": message,
                        "plan": state["plan"],
                    }
                    yield {**base_event, "type": "token", "content": message}
                    yield {**base_event, "type": "done", "content": message, "status": "incomplete"}
                    return
                batch_id = f"batch_{uuid.uuid4().hex}"
                for tool_use in tool_uses:
                    self._create_tool_call(state, tool_use, batch_id)

                assistant_content = []
                if response_text:
                    assistant_content.append({"type": "text", "text": response_text})
                for tool_use in tool_uses:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tool_use["id"],
                        "name": tool_use["name"],
                        "input": tool_use["input"],
                    })
                state["messages"].append({"role": "assistant", "content": assistant_content})
                state["active_batch"] = {
                    "batch_id": batch_id,
                    "tool_uses": tool_uses,
                    "next_index": 0,
                    "results": [],
                }
                self._save_task(state)

                events, paused = await self._execute_active_batch(state, registry, base_event)
                for event in events:
                    yield event
                if paused:
                    if state.get("status") not in {"waiting_approval", "cancelled"}:
                        self._settle_open_tool_calls(
                            state,
                            "Task stopped before all tool calls completed.",
                        )
                    return
                warning = self._maybe_replan_after_diagnostics(state, base_event)
                if warning:
                    yield warning

            self._settle_open_tool_calls(
                state,
                "Maximum tool rounds reached before completion.",
            )
            final_response = await self._call_model(
                state,
                "finalization",
                system=(
                    system
                    + "\n\n工具执行轮次已经结束。请根据已有工具结果给出最终答复，"
                    "总结已验证的事实，并明确说明任何未完成事项或阻塞；不要虚构完成结果。"
                ),
                messages=self._fit_context(
                    system,
                    reconcile_tool_messages(
                        state["messages"], state.get("tool_call_ledger", {}),
                    ),
                    self.max_output_tokens,
                    state,
                ),
                tools=[],
                max_tokens=self.max_output_tokens,
                temperature=0.2,
            )
            response_text = final_response.get("text", "")
            missing_criteria = self._missing_acceptance_criteria(
                response_text, state.get("acceptance_criteria", []),
            )
            if missing_criteria:
                response_text = (
                    response_text.rstrip()
                    + "\n\n未逐项覆盖验收清单："
                    + "、".join(str(item) for item in missing_criteria)
                )
            evaluation = self.work_product_evaluator.evaluate(
                criteria=state.get("acceptance_criteria", []),
                response_text=response_text,
                summary=state["summary"],
            )
            acceptance = evaluation["requirement_coverage"]["items"]
            if acceptance:
                state["summary"]["acceptance"] = acceptance
                state["summary"]["work_product_evaluation"] = evaluation
                self._settle_session_requirements(state, acceptance)
                response_text = re.sub(
                    r"\s*\[证据:(?:file|check|process):[^\]]+\]",
                    "",
                    response_text,
                    flags=re.IGNORECASE,
                )
            final_text = format_final_response(response_text, state["summary"])
            has_failed_verification = any(
                not check.get("success", False)
                for check in state["summary"].get("verification", [])
            )
            status = "completed" if (
                acceptance
                and not state.get("unrecovered_failures")
                and not has_failed_verification
                and not missing_criteria
                and not self._has_explicit_unfinished(response_text)
                and all(item["status"] == "passed" for item in acceptance)
            ) else "incomplete"
            state["status"] = status
            state["final_text"] = final_text
            self._save_task(state)
            if acceptance:
                yield {
                    **base_event,
                    "type": "acceptance",
                    "success": all(item["status"] == "passed" for item in acceptance),
                    "items": acceptance,
                }
            finalization = self._finalization_event(base_event, status, final_text, state["summary"])
            if self._has_finalization_facts(finalization):
                yield finalization
            yield {**base_event, "type": "token", "content": final_text}
            yield {**base_event, "type": "done", "content": final_text, "status": status}
        except Exception as exc:
            error = self._terminal_error(exc, state)
            self._settle_open_tool_calls(state, error)
            state["status"] = "failed"
            state["final_text"] = error
            self._save_task(state)
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}

    @staticmethod
    def _estimate_tokens(system: str, messages: list[dict]) -> int:
        return CompactionEngine.estimate_tokens(system, messages)

    @staticmethod
    def _has_explicit_unfinished(response_text: str) -> bool:
        return bool(_EXPLICIT_UNFINISHED_RE.search(response_text))

    def _fit_context(
        self,
        system: str,
        messages: list[dict],
        output_tokens: int,
        state: dict | None = None,
    ) -> list[dict]:
        fitted = list(messages)
        input_budget = max(1, self.max_context_tokens - output_tokens)
        if self._estimate_tokens(system, fitted) <= input_budget:
            return fitted

        target_budget = max(1, int(input_budget * 0.75))
        compacted, handoff = self.compaction_engine.compact(
            system,
            fitted,
            state or {
                "user_message": self._latest_user_text(fitted),
                "summary": {},
                "messages": fitted,
            },
            max_tokens=target_budget,
        )
        if state is not None:
            source_count = len(fitted)
            if source_count > int(state.get("compacted_message_count", 0)):
                state["compaction_count"] = int(state.get("compaction_count", 0)) + 1
            state["compacted_message_count"] = max(
                source_count, int(state.get("compacted_message_count", 0)),
            )
            state["context_handoff"] = asdict(handoff)
            source_sequences = []
            if self.task_store is not None and hasattr(self.task_store, "get_agent_events"):
                source_sequences = [
                    int(event["sequence"])
                    for event in self.task_store.get_agent_events(state.get("task_id", ""))
                ]
            pending_requirements = [
                str(item.get("text") or "")
                for item in state.get("session_requirements", [])
                if item.get("status", "pending") == "pending"
            ]
            open_call_ids = [
                call_id for call_id, call in state.get("tool_call_ledger", {}).items()
                if call.get("status") not in TERMINAL_TOOL_CALL_STATUSES
            ]
            self._record_event({
                "task_id": state.get("task_id", ""),
                "session_id": state.get("session_id", ""),
                "type": "context_compacted",
                "compaction_count": state.get("compaction_count", 0),
                "source_message_count": handoff.source_message_count,
                "summary_version": 1,
                "source_sequence_start": min(source_sequences) if source_sequences else 1,
                "source_sequence_end": max(source_sequences) if source_sequences else 1,
                "pending_requirements": pending_requirements,
                "open_tool_call_ids": open_call_ids,
            })
        return compacted

    @staticmethod
    def _latest_user_text(messages: list[dict]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user" and isinstance(message.get("content"), str):
                return message["content"]
        return ""

    async def _execute_active_batch(
        self,
        state: dict,
        registry: ToolRegistry,
        base_event: dict,
        approval_decision: bool | None = None,
    ) -> tuple[list[dict], bool]:
        batch = state["active_batch"]
        batch_id = batch["batch_id"]
        events: list[dict] = []
        failure_counts = Counter(state.get("failure_counts", {}))
        decision_index = batch["next_index"] if approval_decision is not None else None

        while batch["next_index"] < len(batch["tool_uses"]):
            cancelled = self._cancelled_event(state, base_event)
            if cancelled:
                events.append(cancelled)
                return events, True
            index = batch["next_index"]
            tool_use = batch["tool_uses"][index]
            name = tool_use["name"]
            args = tool_use["input"]
            call_id = tool_use["id"]
            stage = self._tool_stage(name)
            stage_budget = state["summary"]["stage_budgets"][stage]
            if int(stage_budget["used"]) >= int(stage_budget["limit"]):
                message = f"{stage} 阶段预算已用尽，任务未继续执行: {name}"
                stage_budget["status"] = "exhausted"
                state["status"] = "incomplete"
                events.append({
                    **base_event,
                    "type": "stage_budget_exhausted",
                    "stage": stage,
                    "tool_name": name,
                    "budget": dict(stage_budget),
                })
                events.append({**base_event, "type": "done", "content": message, "status": "incomplete"})
                self._save_task(state)
                return events, True
            stage_budget["used"] = int(stage_budget["used"]) + 1
            stage_budget["status"] = "active"
            recovery_key = tool_recovery_key(name, args, Path(state["current_path"]))
            state["tool_call_ledger"][call_id]["recovery_key"] = recovery_key
            is_resumed_tool = index == decision_index
            if not is_resumed_tool:
                events.append({
                    **base_event,
                    "type": "tool_call",
                    "name": name,
                    "tool_name": name,
                    "args": args,
                    "call_id": call_id,
                    "batch_id": batch_id,
                })

            if is_resumed_tool and approval_decision is False:
                result = ToolResult.fail("用户拒绝了该操作", error_kind="rejected")
            else:
                requires_confirmation = (
                    not is_resumed_tool
                    and hasattr(registry, "requires_confirmation")
                    and registry.requires_confirmation(name, args)
                )
                if is_resumed_tool or not requires_confirmation:
                    self._transition_tool_call(state, call_id, "running")
                with tool_call_context(
                    task_id=state["task_id"],
                    call_id=call_id,
                    batch_id=batch_id,
                ):
                    result = await asyncio.to_thread(
                        registry.execute,
                        name,
                        args,
                        confirmed=is_resumed_tool and approval_decision is True,
                    )

            cancelled = self._cancelled_event(state, base_event)
            if cancelled:
                events.append(cancelled)
                return events, True

            if result.requires_confirmation:
                self._transition_tool_call(state, call_id, "awaiting_approval")
                state["status"] = "waiting_approval"
                state["failure_counts"] = dict(failure_counts)
                self._save_task(state)
                events.append({
                    **base_event,
                    "type": "approval_required",
                    "tool_use_id": tool_use["id"],
                    "call_id": call_id,
                    "batch_id": batch_id,
                    "tool_name": name,
                    "args": args,
                    "reason": result.confirmation_reason,
                })
                events.append({
                    **base_event,
                    "type": "done",
                    "content": result.confirmation_reason or "操作等待确认",
                    "status": "waiting_approval",
                })
                return events, True

            result_dict = result.to_dict()
            provider_content, persisted_result, event_result, artifact = self._prepare_tool_result(
                state["task_id"], call_id, name, result, result_dict,
            )
            if is_resumed_tool and approval_decision is False:
                terminal_status = "rejected"
                error_kind = "rejected"
            else:
                terminal_status = "succeeded" if result.success else "failed"
                error_kind = None if result.success else (result.error_kind or "tool_error")
            self._transition_tool_call(
                state,
                call_id,
                terminal_status,
                result=persisted_result,
                error_kind=error_kind,
            )
            if result.success:
                for failed_call_id, failure in list(
                    state.setdefault("unrecovered_failures", {}).items()
                ):
                    if not recovery_key or failure.get("recovery_key") != recovery_key:
                        continue
                    state["tool_call_ledger"][failed_call_id]["recovered_by_call_id"] = call_id
                    if self.task_store is not None and hasattr(
                        self.task_store, "mark_agent_tool_call_recovered"
                    ):
                        self.task_store.mark_agent_tool_call_recovered(
                            state["task_id"], failed_call_id, call_id,
                        )
                    del state["unrecovered_failures"][failed_call_id]
                    events.append({
                        **base_event,
                        "type": "tool_recovered",
                        "name": failure["tool_name"],
                        "failed_call_id": failed_call_id,
                        "recovered_by_call_id": call_id,
                        "recovery_key": recovery_key,
                    })
            else:
                state.setdefault("unrecovered_failures", {})[call_id] = {
                    "tool_name": name,
                    "recovery_key": recovery_key,
                    "error": result.error or "tool failed",
                }
            self._record_tool_run(
                state["task_id"], name, args,
                {**persisted_result, "call_id": call_id, "batch_id": batch_id},
            )
            events.extend(self._result_events(
                base_event, state, name, event_result,
                call_id=call_id, batch_id=batch_id, artifact=artifact,
            ))
            batch["results"].append({
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": provider_content,
            })
            batch["next_index"] += 1
            if not result.success and result.error_kind == "invalid_input":
                validation = result.data.get("validation", {})
                lineage = f"{name}:{validation.get('path', '$')}"
                repair_counts = state.setdefault("schema_repair_counts", {})
                repair_counts[lineage] = int(repair_counts.get(lineage, 0)) + 1
                if repair_counts[lineage] > 1:
                    message = f"工具参数自动修复已用尽: {name} {validation.get('path', '$')}"
                    state["status"] = "incomplete"
                    state["final_text"] = message
                    events.append({
                        **base_event,
                        "type": "tool_repair_exhausted",
                        "tool_name": name,
                        "lineage": lineage,
                        "attempts": repair_counts[lineage],
                        "validation": validation,
                    })
                    events.append({**base_event, "type": "done", "content": message, "status": "incomplete"})
                    self._save_task(state)
                    return events, True
            if name in _DIAGNOSTIC_TOOLS:
                state["diagnostic_tool_count"] = int(state.get("diagnostic_tool_count", 0)) + 1
            elif result.success:
                state["material_tool_seen"] = True
                state["summary"]["successful_tools"] = list(dict.fromkeys([
                    *state["summary"].get("successful_tools", []),
                    name,
                ]))

            if not result.success and approval_decision is not False:
                signature = json.dumps(
                    {"name": name, "args": args, "error": result.error},
                    ensure_ascii=False, sort_keys=True, default=str,
                )
                failure_counts[signature] += 1
                if failure_counts[signature] >= self.max_identical_failures:
                    error = f"工具调用重复失败 {self.max_identical_failures} 次，已停止: {name}"
                    state["status"] = "failed"
                    state["failure_counts"] = dict(failure_counts)
                    self._save_task(state)
                    events.append({**base_event, "type": "error", "content": error})
                    events.append({**base_event, "type": "done", "content": error, "status": "failed"})
                    return events, True

            approval_decision = None
            self._save_task(state)

        state["messages"].append({"role": "user", "content": batch["results"]})
        state["active_batch"] = None
        state["failure_counts"] = dict(failure_counts)
        self._save_task(state)
        return events, False

    def _cancelled_event(self, state: dict, base_event: dict) -> dict | None:
        if state["task_id"] not in self._cancelled_tasks and state.get("status") != "cancelled":
            return None
        state["status"] = "cancelled"
        state["final_text"] = "任务已由用户取消。"
        self._settle_open_tool_calls(state, "Task cancelled by user.")
        self._save_task(state)
        return {**base_event, "type": "done", "content": state["final_text"], "status": "cancelled"}

    def _maybe_replan_after_diagnostics(self, state: dict, base_event: dict) -> dict | None:
        count = int(state.get("diagnostic_tool_count", 0))
        if (
            state.get("replanned")
            or state.get("material_tool_seen")
            or count < self.diagnostic_tool_budget
        ):
            return None

        message = f"诊断操作已达到 {count} 次，已停止继续扩散读取并重新规划。"
        replan_step = "基于现有证据重新规划，并优先执行最小改动与验证"
        state["replanned"] = True
        state["round_limit"] = self.max_rounds + self.replan_extra_rounds
        if replan_step not in state["plan"]:
            state["plan"].append(replan_step)
        state["messages"].append({
            "role": "user",
            "content": (
                "诊断预算已用尽。下一轮必须先基于已有证据重新规划，"
                "优先执行能推进任务的最小实现或验证，不要继续重复读取。"
            ),
        })
        self._save_task(state)
        return {
            **base_event,
            "type": "budget_warning",
            "diagnostic_tool_count": count,
            "round_limit": state["round_limit"],
            "message": message,
            "plan": state["plan"],
        }

    @staticmethod
    def _terminal_error(exc: Exception, state: dict) -> str:
        detail = str(exc).strip() or "未提供错误信息"
        error = f"Agent 运行失败（{type(exc).__name__}）：{detail}"
        summary = state.get("summary") or {}
        facts = []
        changed_files = [str(path) for path in summary.get("changed_files", [])]
        if changed_files:
            facts.append("已记录文件变更：" + "、".join(changed_files[:20]))
        checks = [
            check for check in summary.get("verification", [])
            if isinstance(check, dict)
        ]
        if checks:
            passed = sum(bool(check.get("success")) for check in checks)
            facts.append(f"已记录验证：{passed}/{len(checks)} 项通过")
        processes = [
            process for process in summary.get("processes", [])
            if isinstance(process, dict) and process.get("process_id")
        ]
        if processes:
            facts.append("已记录进程：" + "、".join(
                str(process["process_id"]) for process in processes[:10]
            ))
        if facts:
            error += "\n\n执行事实已保留：\n- " + "\n- ".join(facts)
        return error

    def _result_events(
        self,
        base_event: dict,
        state: dict,
        name: str,
        result: ToolResult,
        *,
        call_id: str,
        batch_id: str,
        artifact: dict | None = None,
    ) -> list[dict]:
        events = [{
            **base_event,
            "type": "tool_result",
            "name": name,
            "tool_name": name,
            "call_id": call_id,
            "batch_id": batch_id,
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "data": result.data,
            "error_kind": result.error_kind,
            "artifact": artifact,
        }]
        summary = state["summary"]
        workspace_root = state["workspace_root"]
        if result.changed_files:
            changed_files = [
                normalize_evidence_path(workspace_root, path)
                for path in result.changed_files
            ]
            summary["changed_files"] = list(dict.fromkeys([
                *summary["changed_files"], *changed_files,
            ]))
            diff = result.data.get("diff", "")
            path_kinds = {
                normalize_evidence_path(workspace_root, path): kind
                for path, kind in result.data.get("path_kinds", {}).items()
            }
            events.append({
                **base_event,
                "type": "file_changed",
                "files": changed_files,
                "diff": diff,
                "path_kinds": path_kinds,
            })
            self._record_changeset(state["task_id"], changed_files, diff)
        if result.process_id:
            process_data = self._normalize_evidence_fields(result.data, workspace_root)
            process = {"process_id": result.process_id, **process_data}
            summary["processes"] = [
                item for item in summary["processes"] if item.get("process_id") != result.process_id
            ] + [process]
            events.append({**base_event, "type": "process_started", "process_id": result.process_id, "data": process_data})
        if name == "verify_project":
            checks = [
                self._normalize_evidence_fields(check, workspace_root)
                for check in result.data.get("checks", [])
            ]
            summary["verification"] = checks
            events.append({**base_event, "type": "verification", "success": result.success, "checks": checks})
        elif result.data.get("verification"):
            checks = [
                self._normalize_evidence_fields(check, workspace_root)
                for check in result.data["verification"]
            ]
            summary["verification"] = [*summary.get("verification", []), *checks]
            events.append({
                **base_event,
                "type": "verification",
                "success": all(check.get("success", False) for check in checks),
                "checks": checks,
            })
        elif name in {"wait_http", "check_port"}:
            check = {
                "kind": "http" if name == "wait_http" else "port",
                "success": result.success,
                **result.data,
            }
            summary["verification"] = [*summary.get("verification", []), check]
            events.append({
                **base_event,
                "type": "verification",
                "success": result.success,
                "checks": [check],
            })
            process_id = result.data.get("process_id")
            if name == "wait_http" and process_id:
                for process in summary.get("processes", []):
                    if process.get("process_id") != process_id:
                        continue
                    for field in ("owned", "listener_pids", "process_tree_pids", "url", "status"):
                        if field in result.data:
                            process[field] = result.data[field]
                    process["url"] = result.data.get("url", process.get("url"))
                    events.append({
                        **base_event,
                        "type": "process_verified",
                        "process_id": process_id,
                        "owned": result.data.get("owned", False),
                        "listener_pids": result.data.get("listener_pids", []),
                        "process_tree_pids": result.data.get("process_tree_pids", []),
                    })
                    break
        return events

    @staticmethod
    def _finalization_event(base_event: dict, status: str, explanation: str, summary: dict) -> dict:
        checks = [item for item in summary.get("verification", []) if isinstance(item, dict)]
        processes = [item for item in summary.get("processes", []) if isinstance(item, dict)]
        acceptance = [item for item in summary.get("acceptance", []) if isinstance(item, dict)]
        return {
            **base_event,
            "type": "finalization",
            "status": status,
            "facts": {
                "changed_files": list(dict.fromkeys(summary.get("changed_files", []))),
                "verification": {
                    "passed": sum(bool(item.get("success")) for item in checks),
                    "failed": sum(not bool(item.get("success")) for item in checks),
                    "total": len(checks),
                },
                "processes": processes,
                "acceptance": acceptance,
                "successful_tools": list(dict.fromkeys(summary.get("successful_tools", []))),
                "stage_budgets": summary.get("stage_budgets", {}),
            },
            "explanation": explanation,
        }

    @staticmethod
    def _has_finalization_facts(event: dict) -> bool:
        facts = event["facts"]
        return bool(
            facts["changed_files"]
            or facts["verification"]["total"]
            or facts["processes"]
            or facts["acceptance"]
            or facts["successful_tools"]
        )

    @staticmethod
    def _new_stage_budgets() -> dict:
        return {
            stage: {"used": 0, "limit": limit, "status": "pending"}
            for stage, limit in _STAGE_LIMITS.items()
        }

    @staticmethod
    def _tool_stage(name: str) -> str:
        for stage in ("inspect", "test", "run"):
            if name in _STAGE_TOOLS[stage]:
                return stage
        return "implement"

    @staticmethod
    def _normalize_evidence_fields(payload: dict, workspace_root: str) -> dict:
        normalized = dict(payload)
        for field in ("cwd", "path", "project_root"):
            value = normalized.get(field)
            if value:
                normalized[field] = normalize_evidence_path(workspace_root, value)
        return normalized

    def _prepare_tool_result(
        self,
        task_id: str,
        call_id: str,
        tool_name: str,
        result: ToolResult,
        result_dict: dict,
    ) -> tuple[str, dict, ToolResult, dict | None]:
        serialized = json.dumps(result_dict, ensure_ascii=False, default=str)
        if len(serialized) <= self.tool_result_preview_chars:
            return serialized, result_dict, result, None

        preview = serialized[:self.tool_result_preview_chars]
        artifact = None
        if self.task_store is not None and hasattr(self.task_store, "record_agent_artifact"):
            stored = self.task_store.record_agent_artifact(
                task_id=task_id,
                call_id=call_id,
                tool_name=tool_name,
                content=serialized,
                mime_type="application/json",
            )
            artifact = {key: value for key, value in stored.items() if key != "content"}

        preview_meta = {
            "truncated": True,
            "preview": preview,
            "preview_chars": len(preview),
            "original_chars": len(serialized),
            "original_size": len(serialized.encode("utf-8")),
            "artifact": artifact,
        }
        if artifact is None:
            preview_meta["artifact_unavailable"] = True
        provider_content = json.dumps(
            {
                "success": result.success,
                "error_kind": result.error_kind,
                **preview_meta,
            },
            ensure_ascii=False,
        )
        bounded_data = {
            "truncated": True,
            "preview_chars": len(preview),
            "original_chars": len(serialized),
            "original_size": len(serialized.encode("utf-8")),
            "artifact": artifact,
        }
        persisted_result = {
            "success": result.success,
            "data": bounded_data,
            "output": preview,
            "error": result.error[:self.tool_result_preview_chars] if result.error else None,
            "changed_files": result.changed_files,
            "process_id": result.process_id,
            "requires_confirmation": result.requires_confirmation,
            "confirmation_reason": result.confirmation_reason,
            "error_kind": result.error_kind,
        }
        event_result = ToolResult(
            success=result.success,
            data=bounded_data,
            output=preview,
            error=persisted_result["error"],
            changed_files=result.changed_files,
            process_id=result.process_id,
            requires_confirmation=result.requires_confirmation,
            confirmation_reason=result.confirmation_reason,
            error_kind=result.error_kind,
        )
        return provider_content, persisted_result, event_result, artifact

    def _create_tool_call(self, state: dict, call: dict, batch_id: str) -> None:
        recovery_key = tool_recovery_key(
            call["name"], call["input"], Path(state["current_path"]),
        )
        record = {
            "call_id": call["id"],
            "source_call_id": call.get("source_call_id"),
            "batch_id": batch_id,
            "tool_name": call["name"],
            "input": call["input"],
            "status": "parsed",
            "result": None,
            "error_kind": None,
            "recovery_key": recovery_key,
            "recovered_by_call_id": None,
        }
        state.setdefault("tool_call_ledger", {})[call["id"]] = record
        if self.task_store is not None and hasattr(self.task_store, "create_agent_tool_call"):
            self.task_store.create_agent_tool_call(
                task_id=state["task_id"],
                session_id=state["session_id"],
                call_id=call["id"],
                batch_id=batch_id,
                tool_name=call["name"],
                input=call["input"],
                recovery_key=recovery_key,
            )

    def _transition_tool_call(
        self,
        state: dict,
        call_id: str,
        status: str,
        *,
        result: dict | None = None,
        error_kind: str | None = None,
    ) -> None:
        record = state["tool_call_ledger"][call_id]
        record["status"] = status
        if result is not None:
            record["result"] = result
        record["error_kind"] = error_kind
        if self.task_store is not None and hasattr(self.task_store, "transition_agent_tool_call"):
            self.task_store.transition_agent_tool_call(
                state["task_id"],
                call_id,
                status,
                result=result,
                error_kind=error_kind,
            )

    def _settle_open_tool_calls(self, state: dict, error: str) -> None:
        result = {
            "success": False,
            "error": error,
            "error_kind": "interrupted",
        }
        for call_id, record in state.get("tool_call_ledger", {}).items():
            if record.get("status") in TERMINAL_TOOL_CALL_STATUSES:
                continue
            self._transition_tool_call(
                state,
                call_id,
                "interrupted",
                result=result,
                error_kind="interrupted",
            )
        self._save_task(state)

    def _save_task(self, state: dict) -> None:
        self._task_cache[state["task_id"]] = json.loads(json.dumps(state, ensure_ascii=False, default=str))
        if self.task_store is not None:
            self.task_store.save_agent_task(state)

    def _load_task(self, task_id: str) -> dict | None:
        if self.task_store is not None:
            stored = self.task_store.get_agent_task(task_id)
            if stored is not None:
                return stored
        return self._task_cache.get(task_id)

    def _record_tool_run(self, task_id: str, name: str, args: dict, result: dict) -> None:
        if self.task_store is not None:
            self.task_store.record_agent_tool_run(task_id, name, args, result)

    def _record_changeset(self, task_id: str, files: list[str], diff: str) -> None:
        if self.task_store is not None:
            self.task_store.record_agent_changeset(task_id, files, diff)

    def _record_event(self, event: dict) -> None:
        if self.task_store is None or not hasattr(self.task_store, "record_agent_event"):
            return
        event_type = event.get("type")
        if event_type == "done":
            status = event.get("status")
            event_type = {
                "completed": "task_completed",
                "failed": "task_failed",
                "waiting_approval": "task_waiting_approval",
                "cancelled": "task_cancelled",
            }.get(status, "task_finished")
        self.task_store.record_agent_event({
            **event,
            "type": event_type or "event",
        })

    async def _call_model(self, state: dict, phase: str, **kwargs) -> dict:
        model_context = state.get("model_context", {})
        event = {
            "session_id": state["session_id"],
            "task_id": state["task_id"],
            "round": state.get("round", 0),
            "phase": phase,
            "model": model_context.get("id", ""),
            "protocol": model_context.get("protocol", ""),
            "base_url": model_context.get("base_url", ""),
        }
        self._record_event({**event, "type": "model_request_started"})
        started_at = time.perf_counter()
        try:
            response = await self.llm_call(**kwargs)
        except Exception as exc:
            self._record_event({
                **event,
                "type": "model_request_failed",
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "error_type": type(exc).__name__,
            })
            raise
        self._record_event({
            **event,
            "type": "model_request_completed",
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "stop_reason": str(response.get("stop_reason", "")),
            "usage": response.get("usage_metadata") or {},
        })
        return response

    def _record_stream_event(self, state: dict, event: dict) -> None:
        self._record_event(event)
        if event.get("type") == "done" and event.get("status") == "completed":
            self._record_task_memory(state)

    def _settle_session_requirements(self, state: dict, acceptance: list[dict]) -> None:
        if (
            self.task_store is None
            or not hasattr(self.task_store, "settle_session_requirements")
            or not state.get("session_requirements")
        ):
            return
        self.task_store.settle_session_requirements(
            state["session_id"],
            state["task_id"],
            acceptance,
        )
        remaining = self.task_store.list_session_requirements(
            state["session_id"],
            status="pending",
        )
        state["session_requirements"] = remaining
        self._record_event({
            "session_id": state["session_id"],
            "task_id": state["task_id"],
            "type": "requirement_backlog_updated",
            "items": remaining,
        })

    def _settle_implicit_session_requirements(self, state: dict) -> None:
        summary = state.get("summary") or {}
        evidence = [
            {"type": "file", "ref": str(path)}
            for path in summary.get("changed_files", [])
        ]
        evidence.extend(
            {"type": "check", "ref": str(check.get("kind", "command"))}
            for check in summary.get("verification", [])
            if isinstance(check, dict) and check.get("success", False)
        )
        evidence.extend(
            {"type": "tool", "ref": str(name)}
            for name in summary.get("successful_tools", [])
        )
        self._settle_session_requirements(state, [
            {"id": position, "status": "passed", "evidence": evidence}
            for position in state.get("implicit_requirement_positions", [])
        ])

    def _search_project_memories(self, workspace_root: str, query: str) -> list[dict]:
        if self.task_store is None or not hasattr(self.task_store, "search_project_memories"):
            return []
        return self.task_store.search_project_memories(
            workspace_root,
            query,
            verified_only=True,
            limit=6,
        )

    def _record_task_memory(self, state: dict) -> None:
        if self.task_store is None or not hasattr(self.task_store, "remember_project_fact"):
            return
        summary = state.get("summary") or {}
        changed_files = [str(item) for item in summary.get("changed_files", [])]
        checks = [item for item in summary.get("verification", []) if isinstance(item, dict)]
        if not changed_files and not checks:
            return
        verification_status = "verified" if checks and all(
            item.get("success", False) for item in checks
        ) else "partial"
        parts = [f"任务目标: {state.get('user_message', '')}"]
        if changed_files:
            parts.append("已修改文件: " + ", ".join(changed_files[:50]))
        if checks:
            parts.append("验证结果: " + "; ".join(
                f"{item.get('command', 'check')}={'passed' if item.get('success') else 'failed'}"
                for item in checks[:20]
            ))
        snapshot = state.get("workspace_snapshot") or {}
        if snapshot.get("branch"):
            parts.append(f"Git分支: {snapshot['branch']}")
        if snapshot.get("head"):
            parts.append(f"Git基线: {snapshot['head']}")
        self.task_store.remember_project_fact(
            workspace_root=str(state.get("workspace_root", "")),
            content="\n".join(parts),
            source_type="task",
            source_ref=str(state.get("task_id", "")),
            confidence=0.9 if verification_status == "verified" else 0.7,
            verification_status=verification_status,
        )

    @staticmethod
    def _workspace_snapshot(root, current_path) -> dict:
        snapshot = {
            "root": str(root),
            "current_path": str(current_path),
            "profile": {},
            "branch": None,
            "head": None,
        }
        try:
            snapshot["profile"] = WorkspaceManager.describe(root)
            git_path = root / ".git"
            if git_path.is_file():
                marker = git_path.read_text(encoding="utf-8").strip()
                if marker.startswith("gitdir:"):
                    git_path = Path(marker.split(":", 1)[1].strip())
                    if not git_path.is_absolute():
                        git_path = (root / git_path).resolve()
            head_file = git_path / "HEAD"
            value = head_file.read_text(encoding="utf-8").strip()
            if value.startswith("ref: refs/heads/"):
                branch = value.removeprefix("ref: refs/heads/")
                snapshot["branch"] = branch
                ref_file = git_path / "refs" / "heads" / branch
                if ref_file.is_file():
                    snapshot["head"] = ref_file.read_text(encoding="utf-8").strip() or None
            elif value:
                snapshot["branch"] = "detached"
                snapshot["head"] = value
        except (OSError, UnicodeError, ValueError):
            pass
        return snapshot

    @staticmethod
    def _build_plan(user_message: str) -> list[str]:
        text = user_message.lower()
        def negated(word: str) -> bool:
            return bool(re.search(rf"(?:不要|禁止|无需|不需要)[^。！？!?，,；;]{{0,12}}{re.escape(word)}", text))

        steps = ["建立工作区上下文并定位相关文件"]
        asks_for_environment = any(word in text for word in ("克隆", "clone", "安装", "依赖", "venv", "虚拟环境"))
        asks_for_environment = asks_for_environment and not any(negated(word) for word in ("克隆", "clone", "安装", "依赖", "venv", "虚拟环境"))
        if asks_for_environment:
            steps.append("准备项目与隔离依赖环境")
        asks_for_change = any(word in text for word in ("修改", "实现", "修复", "创建", "优化", "改"))
        asks_for_change = asks_for_change and not any(negated(word) for word in ("修改", "实现", "修复", "创建", "优化", "改"))
        if asks_for_change:
            steps.append("执行文件与项目操作并记录变更")
        else:
            steps.append("执行所需本地操作")
        steps.append("运行验证并汇总文件与进程状态")
        return steps

    @staticmethod
    def _request_requires_material_change(user_message: str) -> bool:
        text = user_message.casefold()
        change_terms = (
            "修改", "实现", "修复", "创建", "新增", "添加", "开发", "优化", "改进",
            "删除", "清理", "安装", "克隆", "启动", "重启",
            "fix", "implement", "create", "add", "build", "develop", "optimize",
            "optimise", "delete", "remove", "install", "clone", "start", "restart",
        )
        negation = re.compile(r"(?:不要|无需|不需要|禁止|do not|don't)\s*.{0,16}")
        without_negated_phrases = negation.sub("", text)
        return any(term in without_negated_phrases for term in change_terms)

    @staticmethod
    def _extract_acceptance_criteria(user_message: str) -> list[dict]:
        marker = re.compile(r"(?:^|(?<=[：:；;\n]))\s*(\d{1,2})\s*[.)、]\s*", re.MULTILINE)
        matches = list(marker.finditer(user_message))
        if not matches or int(matches[0].group(1)) != 1:
            return []
        criteria = []
        for index, match in enumerate(matches):
            item_id = int(match.group(1))
            if item_id != index + 1:
                return []
            end = matches[index + 1].start() if index + 1 < len(matches) else len(user_message)
            text = user_message[match.end():end].split("。", 1)[0].strip(" \t\r\n；;。")
            if not text:
                return []
            criteria.append({"id": item_id, "text": text})
        return criteria

    @staticmethod
    def _missing_acceptance_criteria(response_text: str, criteria: list[dict]) -> list[int]:
        missing = []
        for item in criteria:
            item_id = int(item["id"])
            if not re.search(
                rf"(?m)^\s*(?:[-*]\s*)?(?:\*\*)?\[?{item_id}\]?\s*[.)、:]?",
                response_text,
            ):
                missing.append(item_id)
        return missing

    @staticmethod
    def _build_acceptance_ledger(
        response_text: str,
        criteria: list[dict],
        summary: dict,
    ) -> list[dict]:
        return WorkProductEvaluator().evaluate(
            criteria=criteria,
            response_text=response_text,
            summary=summary,
        )["requirement_coverage"]["items"]

    @staticmethod
    def _system_prompt(
        workspace_root: str,
        repo_map: str = "",
        instruction_context: str = "",
        project_memories: list[dict] | None = None,
        acceptance_criteria: list[dict] | None = None,
    ) -> str:
        context = f"\n\n当前 Repo Map：\n{repo_map}" if repo_map else ""
        instructions = (
            "\n\n当前项目指令（更具体目录的规则优先，用户本次明确要求优先级最高）：\n"
            + instruction_context
            if instruction_context else ""
        )
        memories = ""
        if project_memories:
            items = []
            for memory in project_memories:
                content = str(memory.get("content", "")).strip()
                source = str(memory.get("source_ref", "")).strip()
                if content:
                    items.append(f"- {content}（来源: {source}）")
            if items:
                memories = "\n\n相关项目记忆（仅作已来源事实参考）：\n" + "\n".join(items)
        acceptance = ""
        if acceptance_criteria:
            lines = [f"[{item['id']}] {item['text']}" for item in acceptance_criteria]
            acceptance = (
                f"\n\n验收清单（{len(lines)} 项）：\n"
                + "\n".join(lines)
                + "\n执行中持续核对这些条目。最终回复必须按相同编号逐项说明结果与证据。"
                "完成项使用 `[完成]`，并引用至少一个真实执行证据："
                "`[证据:file:工具返回的文件路径]`、`[证据:check:成功验证的 kind]` 或"
                " `[证据:process:进程ID]`。无法完成的条目使用 `[未完成]` 并说明原因；不得省略。"
            )
        return f"""你是一个通用本地操作 Agent。当前工作区根目录：{workspace_root}

通过结构化工具完成任务，不要只给出用户需要自己执行的命令。
规则：
1. 先判断用户请求是否需要本地操作。问候、闲聊和一般知识问题直接回答，不要调用任何工具、不要读取 Repo Map、不要展示执行计划；需要本地操作时再读取 Repo Map，并读取或搜索必要上下文。
2. 文件修改使用 edit_files，搜索文本必须唯一；修改后运行 verify_project 或明确的检查命令。
3. Python 项目只能使用目标项目目录自己的 .venv；缺失时先对该项目调用 ensure_venv。不得复用父目录、兄弟目录、其他任务目录或系统 Python；验证结果必须以工具返回的 python_executable 和 cwd 为准。
4. 长时间服务使用 start_process，不要用前台命令阻塞。
5. 工具失败后根据错误改变策略，不要重复完全相同的失败调用。
6. 不得构造越过工作区的路径。需要确认的操作由系统暂停。
7. 最终只给出一次面向用户的结果，不要输出思考过程、用户意图复述、计划旁白或工具参数，不要使用 Emoji 作为标题或列表装饰。普通问答不要生成执行报告；运行时只在存在真实执行事实时汇总文件、验证和进程。
8. Markdown 代码围栏必须独占一行且不缩进，开始与结束围栏都从行首写起。
9. 列举能力或步骤时使用 Markdown 列表，每项先写简短名称，再写一句说明。{instructions}{memories}{acceptance}{context}"""
