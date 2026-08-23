import asyncio
import datetime
import json
import platform
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
from ..llm import ModelBinding, capture_model_binding
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
_CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "context window",
    "context_window",
    "maximum context",
    "prompt is too long",
    "context_length_exceeded",
    "exceeds the maximum",
    "too many tokens",
)
_VERIFICATION_COMMAND_RE = re.compile(
    r"\b(build|test|pytest|vitest|compileall|lint|tsc|go\s+vet|npm\s+run|pnpm\s+run)\b",
    re.IGNORECASE,
)
_DIAGNOSTIC_TOOLS = {
    "list_directory", "read_file", "search_text", "repo_map", "detect_project",
    "get_process", "list_processes", "check_port", "wait_http",
}
_STAGE_TOOLS = {
    "inspect": _DIAGNOSTIC_TOOLS - {"check_port", "wait_http"},
    "test": {"verify_project"},
    "run": {"start_process", "get_process", "list_processes", "stop_process", "check_port", "wait_http", "http_request", "http_request_batch"},
}
_STAGE_LIMITS = {"inspect": 36, "implement": 24, "test": 8, "run": 8}
_CONTINUE_TASK_RE = re.compile(
    r"^\s*(?:(?:继续|接着)(?:做|来|进行|推进|优化|完善)?(?:一下)?(?:吧)?|"
    r"按(?:照)?\s*(?:todo|计划)\s*继续(?:进行|推进)?(?:吧)?)\s*[。.!！]*\s*$",
    re.IGNORECASE,
)
_STATUS_FOLLOWUP_RE = re.compile(
    r"^\s*(?:(?:失败|成功|完成)了?吗(?:[？?，,、 ]*(?:原因|结果|情况)(?:是)?什么?)?|"
    r"(?:完成|做|处理)(?:得|的)?(?:如何|怎么样)|"
    r"(?:当前|现在|刚才|上次|上一项|前一个)?(?:任务|工作)?(?:的)?"
    r"(?:进度|结果|状态|情况|原因)(?:是)?(?:什么|如何|怎么样)?|"
    r"你说的什么(?:[？?，,、 ]*(?:完成|结果)(?:得|的)?(?:如何|怎么样))?)"
    r"\s*[。.!！？?]*\s*$",
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
        diagnostic_tool_budget: int = 28,
        replan_extra_rounds: int = 4,
        tool_result_preview_chars: int = 12_000,
        tool_execution_timeout: float = 300,
        llm_stream_call: Callable[..., Any] | None = None,
        task_store: Any | None = None,
        context_engine: ContextEngine | None = None,
        compaction_engine: CompactionEngine | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.registry_factory = registry_factory
        self.llm_call = llm_call
        self.llm_stream_call = llm_stream_call
        self.max_rounds = max_rounds
        self.max_identical_failures = max_identical_failures
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens
        self.diagnostic_tool_budget = diagnostic_tool_budget
        self.replan_extra_rounds = replan_extra_rounds
        self.tool_result_preview_chars = max(1, int(tool_result_preview_chars))
        self.tool_execution_timeout = float(tool_execution_timeout or 180)
        self.task_store = task_store
        self.context_engine = context_engine
        self.compaction_engine = compaction_engine or CompactionEngine()
        self.work_product_evaluator = WorkProductEvaluator()
        self._task_cache: dict[str, dict] = {}
        self._cancelled_tasks: set[str] = set()
        self._model_bindings: dict[str, ModelBinding] = {}

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
        approval_mode: str = "confirm",
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
        if (
            _STATUS_FOLLOWUP_RE.fullmatch(user_message)
            and self.task_store is not None
            and hasattr(self.task_store, "get_previous_agent_task")
        ):
            previous = self.task_store.get_previous_agent_task(
                session_id,
                exclude_task_id=task_id,
            )
            if previous is not None:
                final_text = self._previous_task_status_response(previous)
                state = {
                    "task_id": task_id,
                    "session_id": session_id,
                    "user_message": user_message,
                    "status": "completed",
                    "final_text": final_text,
                    "workspace_root": str(workspace.root),
                    "current_path": str(current_path),
                    "summary": {"changed_files": [], "verification": [], "processes": [], "successful_tools": []},
                    "plan": [],
                    "tool_call_ledger": {},
                    "active_batch": None,
                    "allow_tools": False,
                }
                self._save_task(state)
                self._record_event({
                    "session_id": session_id,
                    "task_id": task_id,
                    "type": "task_started",
                    "workspace_root": str(workspace.root),
                    "current_path": str(current_path),
                    "status_followup_for": previous.get("task_id"),
                })
                token = {"session_id": session_id, "task_id": task_id, "type": "token", "content": final_text}
                done = {**token, "type": "done", "status": "completed"}
                self._record_event(token)
                self._record_event(done)
                yield token
                yield done
                return
        instruction_context = InstructionLoader(workspace.root, current_path).load()
        project_memories = self._search_project_memories(str(workspace.root), user_message)
        workspace_snapshot = self._workspace_snapshot(workspace.root, current_path)
        safe_model_context = {
            key: str(value)
            for key, value in (model_context or {}).items()
            if key in {"id", "protocol", "base_url", "thinking_effort"} and value is not None
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
        requirement_context = {"pending": [], "pending_total": 0, "pending_truncated": False, "recent_completed": []}
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
            if hasattr(self.task_store, "get_session_requirement_context"):
                requirement_context = self.task_store.get_session_requirement_context(session_id)
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
            "approval_mode": approval_mode if approval_mode in {"confirm", "auto", "open"} else "confirm",
            "messages": [*(history or []), {"role": "user", "content": user_message}],
            "round": 0,
            "round_limit": self.max_rounds,
            "diagnostic_tool_count": 0,
            "diagnostic_unique_count": 0,
            "diagnostic_observations": [],
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
            "requirement_context": requirement_context,
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
            "requirement_backlog": requirement_context,
        })
        if session_requirements:
            self._record_event({
                **base_event,
                "type": "requirement_backlog_loaded",
                "items": requirement_context,
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
                state["resume_available"] = True
                state["resume_count"] = int(state.get("resume_count", 0))
                state["resume_reason"] = "stream_closed"
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

    async def resume_interrupted(
        self,
        session_id: str,
        task_id: str,
        model_context: dict | None = None,
    ) -> AsyncIterator[dict]:
        """Continue a restart-reconciled task from its persisted state.

        The interrupted batch is never replayed blindly. Its settled tool-call
        ledger is kept as evidence, the open batch is discarded, and the model
        receives a bounded continuation budget on the same task and workspace.
        """
        state = self._load_task(task_id)
        base_event = {"session_id": session_id, "task_id": task_id}
        if state is None or state.get("session_id") != session_id:
            error = f"任务不存在或不属于当前会话: {task_id}"
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}
            return
        if state.get("status") != "interrupted" or not state.get("resume_available"):
            error = f"任务不可恢复: {task_id}"
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}
            return

        workspace = self._restore_task_workspace(state)
        registry = self.registry_factory(session_id)
        discarded_batch = state.get("active_batch")
        state.setdefault("messages", [])
        state.setdefault("tool_call_ledger", {})
        state["active_batch"] = None
        state["status"] = "running"
        state["resume_available"] = False
        state["resume_count"] = int(state.get("resume_count", 0)) + 1
        state["resume_reason"] = "manual_resume"
        if model_context:
            state["model_context"] = {
                key: str(value)
                for key, value in model_context.items()
                if key in {"id", "protocol", "base_url"} and value is not None
            }
        state["round_limit"] = max(
            int(state.get("round_limit", self.max_rounds)),
            int(state.get("round", 0)) + max(1, self.replan_extra_rounds),
        )
        self._save_task(state)
        self._record_event({
            **base_event,
            "type": "task_resumed",
            "resume_count": state["resume_count"],
            "discarded_batch_id": (discarded_batch or {}).get("batch_id"),
            "round_limit": state["round_limit"],
        })

        try:
            async for event in self._drive(state, registry, str(workspace.root)):
                self._record_stream_event(state, event)
                yield event
        finally:
            if state.get("status") == "running":
                self._settle_open_tool_calls(state, "Resumed task stream closed before completion.")
                state["status"] = "interrupted"
                state["resume_available"] = True
                state["resume_reason"] = "resume_stream_closed"
                self._save_task(state)
                self._record_event({
                    **base_event,
                    "type": "task_interrupted",
                    "content": "Resumed task stream closed before completion.",
                })

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
        task_id = state["task_id"]
        if task_id not in self._model_bindings:
            self._model_bindings[task_id] = self._capture_task_binding(state)
        system = self._system_prompt(
            workspace_root,
            state.get("repo_map", ""),
            state.get("instruction_context", ""),
            state.get("project_memories", []),
            state.get("acceptance_criteria", []),
            state.get("requirement_context"),
        )
        try:
            while state["round"] < state.get("round_limit", self.max_rounds):
                state["round"] += 1
                self._save_task(state)
                # 动态提醒不进 system（会破坏 provider 前缀缓存，miss input
                # 占成本约 90%）：openai 协议注入消息尾部，anthropic 回退追加
                reminder = self._round_reminder(state)
                binding = self._model_bindings.get(task_id)
                is_anthropic = binding is not None and binding.protocol == "anthropic"
                system_round = (
                    system + f"\n\n{reminder}"
                    if (reminder and is_anthropic)
                    else system
                )
                if self.llm_stream_call is not None:
                    response = {}
                    async for chunk in self._stream_reasoning(
                        state,
                        base_event,
                        system=system_round,
                        messages=self._with_reminder(self._fit_context(
                            system_round,
                            reconcile_tool_messages(
                                state["messages"], state.get("tool_call_ledger", {}),
                            ),
                            self.max_output_tokens,
                            state,
                        ), reminder if not is_anthropic else ""),
                        tools=registry.schemas() if state.get("allow_tools", True) else [],
                        max_tokens=self.max_output_tokens,
                        temperature=0.2,
                    ):
                        if chunk.get("type") == "done":
                            response = chunk.get("response") or {}
                        else:
                            yield chunk
                    if not response:
                        # 流式异常回退：整块重取一次，避免任务静默中断
                        response = await self._call_model(
                            state,
                            "reasoning",
                            system=system_round,
                            messages=self._fit_context(
                                system_round,
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
                else:
                    response = await self._call_model(
                        state,
                        "reasoning",
                        system=system_round,
                        messages=self._with_reminder(self._fit_context(
                            system_round,
                            reconcile_tool_messages(
                                state["messages"], state.get("tool_call_ledger", {}),
                            ),
                            self.max_output_tokens,
                            state,
                        ), reminder if not is_anthropic else ""),
                        tools=registry.schemas() if state.get("allow_tools", True) else [],
                        max_tokens=self.max_output_tokens,
                        temperature=0.2,
                    )
                    thinking = response.get("thinking")
                    if thinking:
                        yield {**base_event, "type": "thinking", "content": thinking}
                self._track_token_scale(state, system_round, response)
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
                        self._record_event({
                            **base_event,
                            "type": "model_response_truncated",
                            "stop_reason": stop_reason,
                            "phase": "reasoning",
                        })
                        continuation = await self._call_model(
                            state,
                            "continuation",
                            system=(
                                system_round
                                + "\n\n上一段回复意外中断。请仅从中断处继续，把当前回答完整结束；"
                                "不要重复已经输出的内容，不要调用工具。"
                            ),
                            messages=self._fit_context(system_round, reconcile_tool_messages([
                                *state["messages"],
                                {"role": "assistant", "content": response_text},
                                {"role": "user", "content": "请从中断处继续并完整结束回答。"},
                            ], state.get("tool_call_ledger", {})), 4_000),
                            tools=[],
                            max_tokens=4_000,
                            temperature=0.2,
                        )
                        response_text = response_text.rstrip() + continuation.get("text", "").lstrip()
                    events = self._finalize(
                        state, base_event, response_text, settle_implicit=True,
                    )
                    if self._needs_acceptance_reformat(state, response_text):
                        # 结果证据已齐（验证全过+有变更）但最终回复未按验收格式
                        # 逐条陈述（fusion fx3：模型声称全部通过却判 incomplete）。
                        # 结果优先：格式可补写，给一次机会而不是让结果证据作废。
                        reformatted = await self._call_model(
                            state,
                            "acceptance_reformat",
                            system=(
                                system_round
                                + "\n\n你的工作成果已验证通过（工具验证全部成功）。"
                                "但最终回复没有按验收清单编号逐条陈述。"
                                "请只输出验收陈述：按编号每项一行，完成项写 `[完成]` 并附 `[evidence:check:验证的 kind]`"
                                "或 `[evidence:file:路径]`；未完成项写 `[未完成]` 并说明原因。"
                                "不要重复修复过程，不要调用工具。"
                            ),
                            messages=self._fit_context(system_round, reconcile_tool_messages([
                                *state["messages"],
                                {"role": "assistant", "content": response_text},
                                {"role": "user", "content": "请按验收清单编号逐条输出验收陈述（[完成]/[未完成] + 证据标记）。"},
                            ], state.get("tool_call_ledger", {})), 4_000),
                            tools=[],
                            max_tokens=4_000,
                            temperature=0.2,
                        )
                        ref_text = reformatted.get("text", "").strip()
                        if ref_text:
                            self._record_event({
                                **base_event,
                                "type": "acceptance_reformatted",
                                "original_status": state.get("status"),
                            })
                            events = self._finalize(
                                state, base_event, ref_text, settle_implicit=True,
                            )
                    for event in events:
                        yield event
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
                    and int(state.get("diagnostic_unique_count", 0)) >= self.diagnostic_tool_budget
                    and tool_uses
                    and all(tool_use["name"] in _DIAGNOSTIC_TOOLS for tool_use in tool_uses)
                    # 重规划后宽限一轮：修复型任务常需最后一两次确认读取，
                    # 立即判死太急（fx11-13 fusion 连续三轮卡在这里）
                    and int(state.get("round", 0)) > int(state.get("replan_round", 0)) + 1
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
                        "diagnostic_unique_count": state.get("diagnostic_unique_count", 0),
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

                # 边说话边干活：把模型本轮的过程文字实时推给前端；
                # 模型只发工具调用不说话时，由 harness 生成一句“正在做什么”的旁白。
                if response_text.strip():
                    yield {**base_event, "type": "token", "content": response_text}
                else:
                    for tool_use in tool_uses:
                        yield {
                            **base_event,
                            "type": "narration",
                            "tool_name": tool_use["name"],
                            "content": self._tool_narration(tool_use),
                        }

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
                    system_round
                    + "\n\n工具执行轮次已经结束。请根据已有工具结果给出最终答复，"
                    "总结已验证的事实，并明确说明任何未完成事项或阻塞；不要虚构完成结果。"
                ),
                messages=self._fit_context(
                    system_round,
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
            if not response_text.strip():
                # 收尾返回空：再给一次机会直接回答用户最初的问题，避免出现
                # “模型未返回最终说明”这种对用户无信息量的终态。
                retry = await self._call_model(
                    state,
                    "finalization_retry",
                    system=(
                        system_round
                        + "\n\n上一轮收尾没有返回内容。请直接回答用户最初的问题/请求，"
                        "说明完成了什么、未完成什么及原因，不要调用工具。"
                    ),
                    messages=self._fit_context(
                        system_round,
                        reconcile_tool_messages([
                            *state["messages"],
                            {"role": "user", "content": f"请直接回答最初的问题：{state.get('user_message', '')}"},
                        ], state.get("tool_call_ledger", {})),
                        self.max_output_tokens,
                        state,
                    ),
                    tools=[],
                    max_tokens=self.max_output_tokens,
                    temperature=0.2,
                )
                response_text = retry.get("text", "")
            events = self._finalize(state, base_event, response_text, require_acceptance=True)
            if self._needs_acceptance_reformat(state, response_text):
                # 轮次耗尽路径同样需要验收补写（fx7 pdfcpu：修复完成+验证全过，
                # 37 轮到顶后收尾回复未按格式逐条陈述 → 补写一次）
                reformatted = await self._call_model(
                    state,
                    "acceptance_reformat",
                    system=(
                        system_round
                        + "\n\n你的工作成果已验证通过（工具验证全部成功）。"
                        "但最终回复没有按验收清单编号逐条陈述。"
                        "请只输出验收陈述：按编号每项一行，完成项写 `[完成]` 并附 `[evidence:check:验证的 kind]`"
                        "或 `[evidence:file:路径]`；未完成项写 `[未完成]` 并说明原因。"
                        "不要重复修复过程，不要调用工具。"
                    ),
                    messages=self._fit_context(system_round, reconcile_tool_messages([
                        *state["messages"],
                        {"role": "assistant", "content": response_text},
                        {"role": "user", "content": "请按验收清单编号逐条输出验收陈述（[完成]/[未完成] + 证据标记）。"},
                    ], state.get("tool_call_ledger", {})), 4_000),
                    tools=[],
                    max_tokens=4_000,
                    temperature=0.2,
                )
                ref_text = reformatted.get("text", "").strip()
                if ref_text:
                    self._record_event({
                        **base_event,
                        "type": "acceptance_reformatted",
                        "original_status": state.get("status"),
                    })
                    events = self._finalize(
                        state, base_event, ref_text, require_acceptance=True,
                    )
            for event in events:
                yield event
        except Exception as exc:
            error = self._terminal_error(exc, state)
            self._settle_open_tool_calls(state, error)
            state["status"] = "failed"
            state["final_text"] = error
            self._save_task(state)
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}

    def _finalize(
        self,
        state: dict,
        base_event: dict,
        response_text: str,
        *,
        require_acceptance: bool = False,
        settle_implicit: bool = False,
    ) -> list[dict]:
        """终态仲裁与最终回复：独立于产出模型的单点判定，避免重复实现走样。

        require_acceptance=True 时（轮次耗尽收尾）只有同时满足验收与全部证据才判完成；
        否则（模型主动结束）按失败恢复、验证、缺失修改等信号判定。
        """
        events: list[dict] = []
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
                r"\s*\[(?:证据|evidence):(?:file|check|process):[^\]]+\]",
                "",
                response_text,
                flags=re.IGNORECASE,
            )
        final_text = format_final_response(response_text, state["summary"])
        # 验证按 family 归并：http 系（wait_http/http_request/http_request_batch）
        # 取“成功优先”——HTTP 验收的目标是证明服务可用，只要有一次成功
        # 的端到端验证即达成；其后的失败多为清理时序噪音（如停服后的探活）。
        # 非 http 系仍取最后状态（r11a buku：最后一次 batch 在验收后失败，
        # 前面有成功批次，按最后状态误判死）。
        latest_by_family: dict[str, dict] = {}
        for check in state["summary"].get("verification", []):
            if not isinstance(check, dict):
                continue
            kind = str(check.get("kind") or "command").casefold()
            family = "http" if kind.startswith("http") else kind
            if family == "http":
                latest_by_family[family] = check
                continue
            latest_by_family[family] = check
        if any(
            family == "http" and check.get("success")
            for family, check in latest_by_family.items()
        ):
            latest_by_family["http"] = {
                "kind": "http",
                "success": True,
                "command": "http (successful)",
            }
        # 起服前的 port 检查失败是过程性观察（服务还没起必然不通）；
        # 若之后有成功的 HTTP 验证，port 失败已被结果超越，不作为判死依据
        if any(
            family == "http" and check.get("success")
            for family, check in latest_by_family.items()
        ):
            latest_by_family = {
                family: check
                for family, check in latest_by_family.items()
                if not (family == "port" and not check.get("success"))
            }
        has_failed_verification = any(
            not check.get("success", False)
            for check in latest_by_family.values()
        )
        # 结果优先：有成功的结果证据（验证通过/验收通过）时，过程失败痕迹
        # 降级为诊断，不判死。（Terminal-Bench：grading outcomes, not the process）
        # 注意：无任何验证证据时 result_ok 不为真——"没失败"不等于"成功了"。
        successful_evidence = (
            any(
                isinstance(check, dict) and check.get("success")
                for check in state["summary"].get("verification", [])
            )
            or bool(acceptance and all(item["status"] == "passed" for item in acceptance))
        )
        result_ok = successful_evidence and not has_failed_verification
        critical_failures = {} if result_ok else state.get("unrecovered_failures") or {}
        if require_acceptance:
            status = "completed" if (
                acceptance
                and not critical_failures
                and not has_failed_verification
                and not missing_criteria
                and not self._has_explicit_unfinished(response_text)
                and all(item["status"] == "passed" for item in acceptance)
            ) else "incomplete"
        else:
            missing_material_change = (
                state.get("requires_material_change", False)
                and not state.get("material_tool_seen", False)
            )
            if missing_material_change:
                final_text = (
                    final_text.rstrip()
                    + "\n\n未完成：本次任务要求执行修改，但未记录任何文件或项目变更。"
                )
            empty_model_response = not response_text.strip()
            status = "incomplete" if (
                critical_failures
                or has_failed_verification
                or missing_material_change
                or missing_criteria
                or empty_model_response
                or self._has_explicit_unfinished(response_text)
                or (acceptance and not all(
                    item["status"] == "passed" for item in acceptance
                ))
            ) else "completed"
        state["status"] = status
        state["final_text"] = final_text
        if settle_implicit and status == "completed" and state.get("implicit_requirement_positions"):
            self._settle_implicit_session_requirements(state)
        self._save_task(state)
        if acceptance:
            events.append({
                **base_event,
                "type": "acceptance",
                "success": all(item["status"] == "passed" for item in acceptance),
                "items": acceptance,
            })
        finalization = self._finalization_event(base_event, status, final_text, state["summary"])
        if self._has_finalization_facts(finalization):
            events.append(finalization)
        events.append({**base_event, "type": "token", "content": final_text})
        events.append({**base_event, "type": "done", "content": final_text, "status": status})
        return events

    @staticmethod
    def _estimate_tokens(system: str, messages: list[dict]) -> int:
        return CompactionEngine.estimate_tokens(system, messages)

    @staticmethod
    def _needs_acceptance_reformat(state: dict, response_text: str = "") -> bool:
        """结果证据齐备但最终回复未按验收格式逐条陈述时，给一次补写机会。

        结果优先仲裁：验证全过 + 有实际变更时，验收格式是可补写的陈述问题，
        不应让真实完成的工作因格式缺失被判 incomplete。
        """
        if state.get("status") != "incomplete":
            return False
        if response_text and LocalAgentRuntime._has_explicit_unfinished(response_text):
            # 模型明确声明了未完成项：这是真实未完成，不是格式问题
            return False
        acceptance = state.get("summary", {}).get("acceptance") or []
        if not acceptance or all(item.get("status") == "passed" for item in acceptance):
            return False
        latest_by_family: dict[str, dict] = {}
        for check in state.get("summary", {}).get("verification", []):
            if not isinstance(check, dict):
                continue
            kind = str(check.get("kind") or "command").casefold()
            family = "http" if kind.startswith("http") else kind
            latest_by_family[family] = check
        if any(not check.get("success", False) for check in latest_by_family.values()):
            return False
        if not state.get("summary", {}).get("changed_files"):
            # 只认真实文件变更：verify_project 等验证工具的成功不应算作材料变更
            return False
        return True

    @staticmethod
    def _is_context_overflow(exc: Exception) -> bool:
        text = str(exc).casefold()
        return any(marker in text for marker in _CONTEXT_OVERFLOW_MARKERS)

    @staticmethod
    def _has_explicit_unfinished(response_text: str) -> bool:
        return bool(_EXPLICIT_UNFINISHED_RE.search(response_text))

    async def _stream_reasoning(self, state: dict, base_event: dict, **kwargs) -> AsyncIterator[dict]:
        """流式模型调用：token/thinking 逐块 yield 给 SSE，结束 yield done 携带完整响应。

        观测事件与 _call_model 对齐（started/completed/failed），供活动页回放。
        """
        model_context = state.get("model_context", {})
        event = {
            "session_id": state["session_id"],
            "task_id": state["task_id"],
            "round": state.get("round", 0),
            "phase": "reasoning",
            "model": model_context.get("id", ""),
            "protocol": model_context.get("protocol", ""),
            "base_url": model_context.get("base_url", ""),
        }
        binding = self._model_bindings.get(state["task_id"])
        if binding is None:
            response = await self.llm_call(**kwargs)
            yield {"type": "done", "response": response}
            return
        self._record_event({**event, "type": "model_request_started"})
        started_at = time.perf_counter()
        stream_retried = False
        try:
            response = {}
            async for chunk in self.llm_stream_call(**kwargs, binding=binding):
                ctype = chunk.get("type")
                if ctype == "token":
                    yield {**base_event, "type": "token", "content": chunk.get("content", "")}
                elif ctype == "thinking":
                    yield {**base_event, "type": "thinking", "content": chunk.get("content", "")}
                elif ctype == "done":
                    response = chunk.get("response") or {}
            if not response:
                # 流正常结束但没有 done chunk（SSE 提前关闭）：静默整块重取
                # 会多消耗一次 API 请求（账本只见一次 completed），必须可观测
                self._record_event({
                    **event,
                    "type": "model_request_retrying",
                    "attempt": 2,
                    "reason": "stream_empty_response_fallback",
                })
                response = await self.llm_call(**kwargs, binding=binding)
            self._record_event({
                **event,
                "type": "model_request_completed",
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "stop_reason": str(response.get("stop_reason", "")),
                "usage": response.get("usage_metadata") or {},
            })
            yield {"type": "done", "response": response}
        except Exception as exc:
            error_type = type(exc).__name__
            self._record_event({
                **event,
                "type": "model_request_failed",
                "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                "error_type": error_type,
            })
            # 流中途断线（如 SSE 连接被对端关闭）：整块重取一次，避免任务被
            # 连接问题误杀（fx1 中 pdfcpu 修复正确却因 RemoteProtocolError 判 failed）。
            if not stream_retried:
                stream_retried = True
                self._record_event({
                    **event,
                    "type": "model_request_retrying",
                    "attempt": 2,
                    "error_type": error_type,
                    "reason": "stream_interrupted_fallback",
                })
                try:
                    response = await self.llm_call(**kwargs, binding=binding)
                    self._record_event({
                        **event,
                        "type": "model_request_completed",
                        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                        "stop_reason": str(response.get("stop_reason", "")),
                        "usage": response.get("usage_metadata") or {},
                        "stream_fallback": True,
                    })
                    yield {"type": "done", "response": response}
                    return
                except Exception as fallback_exc:
                    self._record_event({
                        **event,
                        "type": "model_request_failed",
                        "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                        "error_type": type(fallback_exc).__name__,
                        "stream_fallback": True,
                    })
            raise

    def _capture_task_binding(self, state: dict) -> ModelBinding:
        """冻结任务启动时的模型绑定：身份以任务记录为准，密钥取当前环境。

        这样运行中的任务不会因全局模型切换而被静默改道；
        API key 只保存在内存中，不写入任务状态或数据库。
        """
        captured = capture_model_binding()
        model_context = state.get("model_context") or {}
        if not model_context.get("id"):
            return captured
        return ModelBinding(
            model=str(model_context["id"]),
            protocol=str(model_context.get("protocol") or captured.protocol),
            base_url=str(model_context.get("base_url") or captured.base_url),
            api_key=captured.api_key,
            thinking_effort=str(model_context.get("thinking_effort") or captured.thinking_effort),
            thinking_budget_tokens=captured.thinking_budget_tokens,
        )

    @staticmethod
    def _tool_narration(tool_use: dict) -> str:
        """模型静默调用工具时，生成一句用户可读的“正在做什么”旁白。"""
        name = str(tool_use.get("name") or "")
        args = tool_use.get("input") or {}
        if name == "read_file":
            return f"正在读取 {args.get('path', '?')}"
        if name == "list_directory":
            return f"正在查看目录 {args.get('path', '?')}"
        if name == "search_text":
            return f"正在搜索 {args.get('query', '?')}"
        if name == "run_command":
            return f"正在执行 {args.get('command', '?')}"
        if name == "edit_files":
            edits = args.get("edits") or []
            count = len(edits) if isinstance(edits, list) else 0
            return f"正在修改 {count} 个文件" if count else "正在修改文件"
        if name == "start_process":
            return f"正在启动 {args.get('command', '进程')}"
        if name == "wait_http":
            return f"正在等待服务就绪 {args.get('url', '?')}"
        if name == "http_request":
            return f"正在请求 {args.get('url', '?')}"
        if name == "check_port":
            return f"正在检查端口 {args.get('port', '?')}"
        if name == "create_directory":
            return f"正在创建目录 {args.get('path', '?')}"
        if name in {"detect_project", "repo_map", "verify_project"}:
            return f"正在分析项目（{name}）"
        if name in {"ensure_venv", "install_dependencies"}:
            return f"正在准备环境（{name}）"
        if name in {"get_process", "list_processes", "stop_process"}:
            return f"正在处理进程（{name}）"
        return f"正在调用 {name}"

    @staticmethod
    def _previous_task_status_response(previous: dict) -> str:
        labels = {
            "completed": "已完成",
            "incomplete": "未完成",
            "failed": "失败",
            "blocked": "受阻",
            "cancelled": "已取消",
            "interrupted": "已中断",
            "waiting_approval": "等待确认",
            "running": "仍在运行",
            "pending": "等待执行",
        }
        status = str(previous.get("status") or "unknown")
        lines = [f"上一项任务{labels.get(status, status)}。"]
        goal = str(previous.get("user_message") or "").strip()
        if goal:
            lines.append(f"任务：{goal}")
        final_text = str(previous.get("final_text") or "").strip()
        if final_text:
            lines.append(final_text)
        summary = previous.get("summary") or {}
        files = list(dict.fromkeys(str(path) for path in summary.get("changed_files", [])))
        if files:
            lines.append("已记录文件变更：" + "、".join(files))
        checks = [item for item in summary.get("verification", []) if isinstance(item, dict)]
        if checks:
            passed = sum(bool(item.get("success")) for item in checks)
            lines.append(f"验证：{passed}/{len(checks)} 项通过。")
        return "\n\n".join(lines)

    def _fit_context(
        self,
        system: str,
        messages: list[dict],
        output_tokens: int,
        state: dict | None = None,
        *,
        budget_ratio: float = 1.0,
    ) -> list[dict]:
        fitted = list(messages)
        input_budget = max(1, int((self.max_context_tokens - output_tokens) * budget_ratio))
        scale = float(state.get("tokens_scale", 1.0)) if state else 1.0
        if int(self._estimate_tokens(system, fitted) * scale) <= input_budget:
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
            scale=scale,
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
                approval_mode = state.get("approval_mode", "confirm")
                requires_confirmation = (
                    not is_resumed_tool
                    and hasattr(registry, "requires_confirmation")
                    and registry.requires_confirmation(name, args)
                )
                # 权限模式：confirm 走人工审批；auto 自动批准高风险操作；open 完全开放
                if requires_confirmation and approval_mode in {"auto", "open"}:
                    self._record_event({
                        **base_event,
                        "type": "approval_auto",
                        "tool_name": name,
                        "args": args,
                        "mode": approval_mode,
                    })
                    requires_confirmation = False
                if is_resumed_tool or not requires_confirmation:
                    self._transition_tool_call(state, call_id, "running")
                confirmed = (
                    (is_resumed_tool and approval_decision is True)
                    or approval_mode in {"auto", "open"}
                )
                with tool_call_context(
                    task_id=state["task_id"],
                    call_id=call_id,
                    batch_id=batch_id,
                ):
                    try:
                        result = await asyncio.wait_for(
                            asyncio.to_thread(
                                registry.execute,
                                name,
                                args,
                                confirmed=confirmed,
                            ),
                            timeout=self.tool_execution_timeout,
                        )
                    except asyncio.TimeoutError:
                        # to_thread 无法中断底层线程，但任务流程必须继续：
                        # 以超时失败进入既有恢复机制，避免整个任务永久卡住。
                        result = ToolResult.fail(
                            f"工具执行超时（>{self.tool_execution_timeout}s）：{name}",
                            error_kind="timeout",
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
                state.pop("step_back", None)
                for failed_call_id, failure in list(
                    state.setdefault("unrecovered_failures", {}).items()
                ):
                    same_lineage = bool(
                        recovery_key
                        and failure.get("recovery_key") == recovery_key
                    )
                    corrected_schema = (
                        failure.get("tool_name") == name
                        and failure.get("error_kind") == "invalid_input"
                    )
                    if not same_lineage and not corrected_schema:
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
                    "error_kind": result.error_kind,
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
                observation = self._diagnostic_observation_key(name, args)
                observations = state.setdefault("diagnostic_observations", [])
                if observation not in observations:
                    observations.append(observation)
                state["diagnostic_unique_count"] = len(observations)
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
                if failure_counts[signature] >= 2:
                    # 未到硬停阈值：先给下一轮模型注入"退一步"提醒，让它主动换思路
                    state["step_back"] = {
                        "tool_name": name,
                        "count": failure_counts[signature],
                        "error": result.error or "",
                    }

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
        count = int(state.get("diagnostic_unique_count", 0))
        if (
            state.get("replanned")
            or state.get("material_tool_seen")
            or count < self.diagnostic_tool_budget
        ):
            return None

        message = f"诊断操作已达到 {count} 次，已停止继续扩散读取并重新规划。"
        replan_step = "基于现有证据重新规划，并优先执行最小改动与验证"
        state["replanned"] = True
        state["replan_round"] = int(state.get("round", 0))
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
            "diagnostic_tool_count": int(state.get("diagnostic_tool_count", count)),
            "diagnostic_unique_count": count,
            "round_limit": state["round_limit"],
            "message": message,
            "plan": state["plan"],
        }

    @staticmethod
    def _terminal_error(exc: Exception, state: dict) -> str:
        error_type = type(exc).__name__
        detail = str(exc).strip()
        if not detail and error_type in {"ReadTimeout", "ConnectTimeout"}:
            detail = "模型服务读取超时，请稍后重试"
        detail = detail or "未提供错误信息"
        error = f"Agent 运行失败（{error_type}）：{detail}"
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
        elif name == "run_command" and result.success and _VERIFICATION_COMMAND_RE.search(
            str((result.data or {}).get("original_command") or (result.data or {}).get("executed_command") or "")
        ):
            # 模型用 run_command 直接跑构建/测试并成功时，同样记录为验证证据
            # （fusion fx7：模型跑 pnpm build/go test 成功，但只有 write_readback
            # 被记录，验收引用的 check:build 匹配不到）。
            command = str(
                (result.data or {}).get("original_command")
                or (result.data or {}).get("executed_command")
                or ""
            )
            lowered = command.casefold()
            kind = "build" if "build" in lowered else (
                "unit" if any(t in lowered for t in ("test", "pytest", "vitest", "tsc")) else (
                    "static" if "compileall" in lowered else "lint"
                )
            )
            check = {
                "kind": kind,
                "command": command,
                "success": True,
                "cwd": str(result.data.get("cwd") or ""),
                "python_executable": result.data.get("python_executable"),
            }
            summary["verification"] = [*summary.get("verification", []), check]
            events.append({
                **base_event,
                "type": "verification",
                "success": True,
                "checks": [check],
            })
        elif (
            name == "http_request_batch"
            and isinstance(result.data, dict)
            and isinstance(result.data.get("checks"), list)
        ):
            check = {
                "kind": "http_batch",
                "group_id": result.data.get("group_id"),
                "success": result.success,
                "total": result.data.get("total", 0),
                "passed": result.data.get("passed", 0),
                "failed": result.data.get("failed", 0),
                "checks": result.data.get("checks", []),
            }
            summary["verification"] = [*summary.get("verification", []), check]
            events.append({
                **base_event,
                "type": "verification",
                "success": result.success,
                "checks": [check],
            })
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
    def _diagnostic_observation_key(name: str, args: dict) -> str:
        stable = dict(args or {})
        for key in ("timeout", "limit", "offset"):
            stable.pop(key, None)
        return json.dumps({"name": name, "args": stable}, ensure_ascii=False, sort_keys=True, default=str)

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
        if state.get("status") in {"completed", "failed", "cancelled", "incomplete", "blocked", "interrupted"}:
            self._model_bindings.pop(state["task_id"], None)

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
        for attempt in range(1, 4):
            started_event = {**event, "type": "model_request_started"}
            if attempt > 1:
                started_event["attempt"] = attempt
            self._record_event(started_event)
            started_at = time.perf_counter()
            try:
                binding = self._model_bindings.get(state["task_id"])
                if binding is None:
                    response = await self.llm_call(**kwargs)
                else:
                    response = await self.llm_call(**kwargs, binding=binding)
                break
            except Exception as exc:
                error_type = type(exc).__name__
                self._record_event({
                    **event,
                    "type": "model_request_failed",
                    "attempt": attempt,
                    "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    "error_type": error_type,
                })
                retryable_rate_limit = (
                    "429" in str(exc)
                    or "too many requests" in str(exc).casefold()
                    or "rate limit" in str(exc).casefold()
                )
                if attempt < 3 and retryable_rate_limit:
                    # 限流退避：连续评测很容易触顶（r11b 全军覆没于 429）。
                    # 指数退避 20s/40s，重试期间事件账本可观测。
                    backoff = 20 * attempt
                    self._record_event({
                        **event,
                        "type": "model_request_retrying",
                        "attempt": attempt + 1,
                        "error_type": error_type,
                        "reason": "rate_limited_backoff",
                        "backoff_seconds": backoff,
                    })
                    await asyncio.sleep(backoff)
                    continue
                if attempt == 1 and error_type in {"ReadTimeout", "ConnectTimeout"}:
                    self._record_event({
                        **event,
                        "type": "model_request_retrying",
                        "attempt": attempt + 1,
                        "error_type": error_type,
                    })
                    continue
                if attempt == 1 and self._is_context_overflow(exc):
                    # 主流做法（Cline/pi/OpenCode）：provider 拒绝超限请求时，
                    # 收紧预算强制压缩后重试一次，而不是直接让任务失败。
                    kwargs["messages"] = self._fit_context(
                        kwargs.get("system", ""),
                        kwargs.get("messages", []),
                        int(kwargs.get("max_tokens") or self.max_output_tokens),
                        state,
                        budget_ratio=0.5,
                    )
                    self._record_event({
                        **event,
                        "type": "model_request_retrying",
                        "attempt": attempt + 1,
                        "error_type": error_type,
                        "reason": "context_overflow_compacted",
                    })
                    continue
                raise
        completed_event = {
            **event,
            "type": "model_request_completed",
            "latency_ms": round((time.perf_counter() - started_at) * 1000, 2),
            "stop_reason": str(response.get("stop_reason", "")),
            "usage": response.get("usage_metadata") or {},
        }
        if attempt > 1:
            completed_event["attempt"] = attempt
        self._record_event(completed_event)
        return response

    def _record_stream_event(self, state: dict, event: dict) -> None:
        # thinking 事件必须落库：AgentTaskSupervisor.subscribe 从 SQLite 回放事件，
        # 不落库会导致 SSE 订阅者永远收不到思考过程。落库时由
        # Memory.record_agent_event 的 _sanitize_event 统一脱敏。
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
        if hasattr(self.task_store, "get_session_requirement_context"):
            state["requirement_context"] = self.task_store.get_session_requirement_context(state["session_id"])
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
        requirement_context: dict | None = None,
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
        display_criteria = acceptance_criteria or []
        if requirement_context and requirement_context.get("pending_truncated"):
            allowed = {int(item.get("position")) for item in requirement_context.get("pending", [])}
            display_criteria = [item for item in display_criteria if int(item.get("id", -1)) in allowed]
        if display_criteria:
            lines = [f"[{item['id']}] {item['text']}" for item in display_criteria]
            acceptance = (
                f"\n\n验收清单（{len(lines)} 项）：\n"
                + "\n".join(lines)
                + "\n执行中持续核对这些条目。最终回复必须按相同编号逐项说明结果与证据。"
                "完成项使用 `[完成]`，并引用至少一个真实执行证据："
                "优先使用 ASCII 标记 `[evidence:file:工具返回的文件路径]`、"
                "`[evidence:check:成功验证的 kind]` 或 `[evidence:process:进程ID]`；"
                "中文 `[证据:...]` 也兼容。无法完成的条目使用 `[未完成]` 并说明原因；不得省略。"
            )
        if requirement_context and requirement_context.get("pending_truncated"):
            acceptance += (
                f"\n需求账本共有 {requirement_context.get('pending_total', 0)} 条未完成项，"
                "本轮仅注入前 24 条；不要臆造未注入条目的完成状态。"
            )
        recent_completed = (requirement_context or {}).get("recent_completed") or []
        if recent_completed:
            acceptance += "\n最近已完成项证据摘要：" + "; ".join(
                f"完成项 {item.get('position')}：{item.get('text')} -> {item.get('evidence', [])}"
                for item in recent_completed
            )
        today = datetime.date.today().isoformat()
        os_name = platform.system() or "Unknown"
        if os_name == "Windows":
            shell_line = (
                "- 命令执行 Shell：PowerShell（Windows 下 run_command 默认按 PowerShell "
                "语法执行；CMD 语法的旧命令也会被转换）"
            )
            shell_rule = (
                "9. Windows 环境下命令统一使用 PowerShell 语法：环境变量用 `$env:NAME=\"值\"` 设置，"
                "禁止使用 CMD 的 `set NAME=值` 语法；PowerShell 中执行外部命令用 `& \"路径\"` 或直接命令名。"
            )
        else:
            shell_line = "- 命令执行 Shell：bash（run_command 按 POSIX shell 语法执行）"
            shell_rule = (
                "9. 命令统一使用 POSIX shell 语法：环境变量用 `NAME=\"值\"` 设置并以 `$NAME` 引用，"
                "多条命令用 `&&` 连接；路径使用正斜杠。"
            )
        return f"""你是一个通用本地操作 Agent。当前工作区根目录：{workspace_root}

# 环境上下文
- 平台：{os_name}（{platform.machine()}）
{shell_line}
- 当前日期：{today}

# 工作流程
1. 先判断用户请求是否需要本地操作。问候、闲聊和一般知识问题直接回答，不要调用任何工具、不要读取 Repo Map、不要展示执行计划；需要本地操作时先在心里规划再动手：先读取 Repo Map 和必要上下文，再决定工具序列。
2. 多步任务按"探索 → 分析 → 实施 → 验证"推进；动手前先规划，验证是唯一判定完成的方式。把独立的读、查、验证合并进尽量少的工具调用；只有后一步依赖前一步结果时才拆开执行。

# 执行规则
3. 文件修改使用 edit_files，搜索文本必须唯一；修改后运行 verify_project 或明确的检查命令。
4. Python 项目只能使用目标项目目录自己的 .venv；缺失时先对该项目调用 ensure_venv。不得复用父目录、兄弟目录、其他任务目录或系统 Python；验证结果必须以工具返回的 python_executable 和 cwd 为准。
5. Python 项目只能使用 ensure_venv 创建/复用项目目录内的 .venv；禁止手动删除 .venv、禁止尝试系统 Python 或其他目录的解释器路径。
6. 长时间服务使用 start_process，不要用前台命令阻塞。
7. 端到端验收包含多个 HTTP 请求时，优先使用 http_request_batch 一次提交检查清单；只有需要根据上一步响应动态决定下一步时才拆成多个 http_request。
8. 工具失败后根据错误改变策略，不要重复完全相同的失败调用；连续失败时停下，列出 3-5 种可能的原因并按可能性排序，然后选择与之前不同的方法；复测 http_request_batch 时沿用返回的 group_id。
{shell_rule}
10. 不得构造越过工作区的路径。需要确认的操作由系统暂停。
11. 最终只给出一次面向用户的结果，不要输出思考过程、用户意图复述、计划旁白或工具参数，不要使用 Emoji 作为标题或列表装饰。普通问答不要生成执行报告；运行时只在存在真实执行事实时汇总文件、验证和进程。
12. Markdown 代码围栏必须独占一行且不缩进，开始与结束围栏都从行首写起。
13. 列举能力或步骤时使用 Markdown 列表，每项先写简短名称，再写一句说明。{instructions}{memories}{acceptance}{context}"""

    def _round_reminder(self, state: dict) -> str:
        """构造当前轮次的动态提醒（预算收敛、循环退一步、探索推动）。

        借鉴 Goose 的 <turn-budget> 注入与 Gemini 的 loop-detection 恢复提示：
        在硬性轮数/重复失败终止之前，先给模型一次主动收敛的机会。
        注意：动态内容不进 system prompt（会破坏 provider 前缀缓存），
        由调用方注入消息尾部（anthropic 协议回退 system 追加）。
        """
        parts = []
        round_limit = int(state.get("round_limit", self.max_rounds))
        current = int(state.get("round", 0))
        if round_limit > 0 and current >= max(2, round_limit // 2):
            parts.append(
                f"（轮次预算：已用 {current}/{round_limit} 轮，剩余不足一半。"
                "请减少探索与重复尝试，合并必要的工具调用，优先完成并验证核心目标后收尾。）"
            )
        step_back = state.get("step_back")
        if step_back and int(step_back.get("count", 0)) >= 2:
            parts.append(
                f"（循环提醒：你已连续 {step_back.get('count')} 次调用 "
                f"{step_back.get('tool_name', '工具')} 得到相同失败："
                f"{str(step_back.get('error', ''))[:300]}。"
                "停止重复该调用；列出 3-5 种可能的原因并按可能性排序，选择与之前不同的方法。）"
            )
        # 探索推动：诊断调用已多但从未动手实施时，提醒直接开始修改
        diagnostic_count = int(state.get("diagnostic_tool_count", 0))
        if (
            not state.get("material_tool_seen")
            and diagnostic_count >= 8
            and int(state.get("round", 0)) >= 4
        ):
            parts.append(
                f"（进度提醒：你已执行 {diagnostic_count} 次读取/搜索类调用。"
                "通常这已足够定位问题；请基于已收集的信息直接实施修改"
                "（edit_files / 命令执行），而不是继续探索。"
                "如确有缺口，先说明还缺什么、为什么已有信息不够。）"
            )
        return "\n".join(parts)

    @staticmethod
    def _with_reminder(messages: list[dict], reminder: str) -> list[dict]:
        """把动态提醒作为末尾 user 消息注入（system 保持逐字节稳定以命中前缀缓存）。"""
        if not reminder:
            return messages
        return [*messages, {"role": "user", "content": reminder}]

    def _track_token_scale(self, state: dict, system: str, response: dict) -> None:
        """用上一次响应的真实 input_tokens 校准字符估算，避免过早/过晚压缩。

        pi 直接复用上一次 usage 做精确计数；这里折算成缩放因子
        （默认 1.0，仅在拿到真实 usage 后生效），供 _fit_context 使用。
        """
        usage = response.get("usage_metadata") or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        if input_tokens <= 0:
            return
        try:
            messages = reconcile_tool_messages(
                state["messages"], state.get("tool_call_ledger", {}),
            )
            estimate = self._estimate_tokens(system, messages)
        except Exception:
            return
        if estimate > 0:
            state["tokens_scale"] = round(min(2.0, max(0.5, estimate / input_tokens)), 3)
