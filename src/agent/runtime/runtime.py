import asyncio
import json
import re
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Callable
from typing import Any

from .context import ContextEngine
from .models import ToolResult
from .registry import ToolRegistry
from .response_format import format_final_response
from .workspace import WorkspaceManager


RegistryFactory = Callable[[str], ToolRegistry]

_DIRECT_CHAT_RE = re.compile(
    r"^\s*(?:你好|您好|嗨|hi|hello|"
    r"你(?:能|可以|会)做什么|你会哪些(?:事情|操作)|"
    r"你的(?:能力|功能|本地操作工具)有哪些|介绍一下你自己)\s*[?？!！。]*\s*$",
    re.IGNORECASE,
)
_UNFINISHED_TEXT_RE = re.compile(r"[\w\u3400-\u9fff]$")
_CLAUSE_MARK_RE = re.compile(r"[，。！？,.!?；;：:\n]")


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
        task_store: Any | None = None,
        context_engine: ContextEngine | None = None,
    ) -> None:
        self.workspaces = workspaces
        self.registry_factory = registry_factory
        self.llm_call = llm_call
        self.max_rounds = max_rounds
        self.max_identical_failures = max_identical_failures
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens
        self.task_store = task_store
        self.context_engine = context_engine
        self._task_cache: dict[str, dict] = {}

    async def confirm(self, session_id: str, task_id: str, approved: bool) -> ToolResult:
        events = [event async for event in self.resume(session_id, task_id, approved)]
        error = next((event.get("content") for event in events if event["type"] == "error"), None)
        if error:
            return ToolResult.fail(error, data={"events": events})
        done = next((event for event in reversed(events) if event["type"] == "done"), None)
        return ToolResult.ok(output=done.get("content", "") if done else "", data={"events": events})

    async def run(
        self,
        session_id: str,
        user_message: str,
        history: list[dict] | None = None,
        task_id: str | None = None,
    ) -> AsyncIterator[dict]:
        task_id = task_id or uuid.uuid4().hex
        workspace = self.workspaces.get(session_id)
        base_event = {"session_id": session_id, "task_id": task_id}
        direct_chat = bool(_DIRECT_CHAT_RE.fullmatch(user_message))
        repo_map = ""
        if self.context_engine is not None and not direct_chat:
            map_result = await asyncio.to_thread(self.context_engine.repo_map, session_id)
            if map_result.success:
                repo_map = map_result.output

        plan = self._build_plan(user_message)
        state = {
            "task_id": task_id,
            "session_id": session_id,
            "user_message": user_message,
            "status": "running",
            "messages": [*(history or []), {"role": "user", "content": user_message}],
            "round": 0,
            "failure_counts": {},
            "active_batch": None,
            "repo_map": repo_map,
            "summary": {"changed_files": [], "verification": [], "processes": []},
            "plan": plan,
            "context_emitted": False,
            "allow_tools": not direct_chat,
        }
        self._save_task(state)

        registry = self.registry_factory(session_id)
        async for event in self._drive(state, registry, str(workspace.root)):
            yield event

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

        workspace = self.workspaces.get(session_id)
        registry = self.registry_factory(session_id)
        state["status"] = "running"
        events, paused = await self._execute_active_batch(
            state, registry, base_event, approval_decision=approved,
        )
        for event in events:
            yield event
        if paused:
            return
        async for event in self._drive(state, registry, str(workspace.root)):
            yield event

    async def _drive(self, state: dict, registry: ToolRegistry, workspace_root: str) -> AsyncIterator[dict]:
        base_event = {"session_id": state["session_id"], "task_id": state["task_id"]}
        system = self._system_prompt(workspace_root, state.get("repo_map", ""))
        try:
            while state["round"] < self.max_rounds:
                state["round"] += 1
                self._save_task(state)
                response = await self.llm_call(
                    system=system,
                    messages=self._fit_context(system, state["messages"], self.max_output_tokens),
                    tools=registry.schemas() if state.get("allow_tools", True) else [],
                    max_tokens=self.max_output_tokens,
                    temperature=0.2,
                )
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
                        continuation = await self.llm_call(
                            system=(
                                system
                                + "\n\n上一段回复意外中断。请仅从中断处继续，把当前回答完整结束；"
                                "不要重复已经输出的内容，不要调用工具。"
                            ),
                            messages=self._fit_context(system, [
                                *state["messages"],
                                {"role": "assistant", "content": response_text},
                                {"role": "user", "content": "请从中断处继续并完整结束回答。"},
                            ], 4_000),
                            tools=[],
                            max_tokens=4_000,
                            temperature=0.2,
                        )
                        response_text = response_text.rstrip() + continuation.get("text", "").lstrip()
                    final_text = format_final_response(response_text, state["summary"])
                    state["status"] = "completed"
                    state["final_text"] = final_text
                    self._save_task(state)
                    yield {**base_event, "type": "token", "content": final_text}
                    yield {**base_event, "type": "done", "content": final_text, "status": "completed"}
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
                state["active_batch"] = {"tool_uses": tool_uses, "next_index": 0, "results": []}
                self._save_task(state)

                events, paused = await self._execute_active_batch(state, registry, base_event)
                for event in events:
                    yield event
                if paused:
                    return

            final_response = await self.llm_call(
                system=(
                    system
                    + "\n\n工具执行轮次已经结束。请根据已有工具结果给出最终答复，"
                    "总结已验证的事实，并明确说明任何未完成事项或阻塞；不要虚构完成结果。"
                ),
                messages=self._fit_context(system, state["messages"], self.max_output_tokens),
                tools=[],
                max_tokens=self.max_output_tokens,
                temperature=0.2,
            )
            final_text = format_final_response(final_response.get("text", ""), state["summary"])
            state["status"] = "completed"
            state["final_text"] = final_text
            self._save_task(state)
            yield {**base_event, "type": "token", "content": final_text}
            yield {**base_event, "type": "done", "content": final_text, "status": "completed"}
        except Exception as exc:
            error = f"Agent 运行失败: {exc}"
            state["status"] = "failed"
            state["final_text"] = error
            self._save_task(state)
            yield {**base_event, "type": "error", "content": error}
            yield {**base_event, "type": "done", "content": error, "status": "failed"}

    @staticmethod
    def _estimate_tokens(system: str, messages: list[dict]) -> int:
        payload = system + json.dumps(messages, ensure_ascii=False, default=str)
        return max((len(payload) + 3) // 4, (len(payload.encode("utf-8")) + 2) // 3)

    def _fit_context(self, system: str, messages: list[dict], output_tokens: int) -> list[dict]:
        fitted = list(messages)
        input_budget = max(1, self.max_context_tokens - output_tokens)
        while len(fitted) > 1 and self._estimate_tokens(system, fitted) > input_budget:
            fitted.pop(0)
        return fitted

    async def _execute_active_batch(
        self,
        state: dict,
        registry: ToolRegistry,
        base_event: dict,
        approval_decision: bool | None = None,
    ) -> tuple[list[dict], bool]:
        batch = state["active_batch"]
        events: list[dict] = []
        failure_counts = Counter(state.get("failure_counts", {}))
        decision_index = batch["next_index"] if approval_decision is not None else None

        while batch["next_index"] < len(batch["tool_uses"]):
            index = batch["next_index"]
            tool_use = batch["tool_uses"][index]
            name = tool_use["name"]
            args = tool_use["input"]
            is_resumed_tool = index == decision_index
            if not is_resumed_tool:
                events.append({**base_event, "type": "tool_call", "name": name, "args": args})

            if is_resumed_tool and approval_decision is False:
                result = ToolResult.fail("用户拒绝了该操作")
            else:
                result = await asyncio.to_thread(
                    registry.execute, name, args, confirmed=is_resumed_tool and approval_decision is True,
                )

            if result.requires_confirmation:
                state["status"] = "waiting_approval"
                state["failure_counts"] = dict(failure_counts)
                self._save_task(state)
                events.append({
                    **base_event,
                    "type": "approval_required",
                    "tool_use_id": tool_use["id"],
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
            self._record_tool_run(state["task_id"], name, args, result_dict)
            events.extend(self._result_events(base_event, state, name, result))
            batch["results"].append({
                "type": "tool_result",
                "tool_use_id": tool_use["id"],
                "content": json.dumps(result_dict, ensure_ascii=False, default=str)[:30_000],
            })
            batch["next_index"] += 1

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

    def _result_events(self, base_event: dict, state: dict, name: str, result: ToolResult) -> list[dict]:
        events = [{
            **base_event,
            "type": "tool_result",
            "name": name,
            "success": result.success,
            "output": result.output,
            "error": result.error,
            "data": result.data,
        }]
        summary = state["summary"]
        if result.changed_files:
            summary["changed_files"] = list(dict.fromkeys([
                *summary["changed_files"], *result.changed_files,
            ]))
            diff = result.data.get("diff", "")
            events.append({**base_event, "type": "file_changed", "files": result.changed_files, "diff": diff})
            self._record_changeset(state["task_id"], result.changed_files, diff)
        if result.process_id:
            process = {"process_id": result.process_id, **result.data}
            summary["processes"] = [
                item for item in summary["processes"] if item.get("process_id") != result.process_id
            ] + [process]
            events.append({**base_event, "type": "process_started", "process_id": result.process_id, "data": result.data})
        if name == "verify_project":
            checks = result.data.get("checks", [])
            summary["verification"] = checks
            events.append({**base_event, "type": "verification", "success": result.success, "checks": checks})
        elif result.data.get("verification"):
            checks = result.data["verification"]
            summary["verification"] = [*summary.get("verification", []), *checks]
            events.append({
                **base_event,
                "type": "verification",
                "success": all(check.get("success", False) for check in checks),
                "checks": checks,
            })
        return events

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
    def _system_prompt(workspace_root: str, repo_map: str = "") -> str:
        context = f"\n\n当前 Repo Map：\n{repo_map}" if repo_map else ""
        return f"""你是一个通用本地操作 Agent。当前工作区根目录：{workspace_root}

通过结构化工具完成任务，不要只给出用户需要自己执行的命令。
规则：
1. 先判断用户请求是否需要本地操作。问候、闲聊和一般知识问题直接回答，不要调用任何工具、不要读取 Repo Map、不要展示执行计划；需要本地操作时再读取 Repo Map，并读取或搜索必要上下文。
2. 文件修改使用 edit_files，搜索文本必须唯一；修改后运行 verify_project 或明确的检查命令。
3. Python 项目使用工作区自己的 .venv；缺失时先调用 ensure_venv。
4. 长时间服务使用 start_process，不要用前台命令阻塞。
5. 工具失败后根据错误改变策略，不要重复完全相同的失败调用。
6. 不得构造越过工作区的路径。需要确认的操作由系统暂停。
7. 最终只给出一次面向用户的结果，不要输出思考过程、用户意图复述、计划旁白或工具参数，不要使用 Emoji 作为标题或列表装饰。普通问答不要生成执行报告；运行时只在存在真实执行事实时汇总文件、验证和进程。
8. Markdown 代码围栏必须独占一行且不缩进，开始与结束围栏都从行首写起。
9. 列举能力或步骤时使用 Markdown 列表，每项先写简短名称，再写一句说明。{context}"""
