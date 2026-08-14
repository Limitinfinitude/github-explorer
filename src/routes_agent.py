"""
Agent 相关路由 — /api/agent/chat/stream (SSE), /api/agent/setup,
/api/agent/confirm, /api/agent/execute, /api/run-command
以及 Agent 相关的辅助端点
"""
import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router_agent = APIRouter()


# ========== 请求模型 ==========

class LocalChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    repo: Optional[str] = None
    workspace: Optional[str] = None
    agent_mode: bool = False


class ExecuteRequest(BaseModel):
    command: str
    cwd: Optional[str] = None
    session_id: str = "default"
    repo: Optional[str] = None


class CommandRequest(BaseModel):
    command: str
    cwd: Optional[str] = None


class WorkspaceRequest(BaseModel):
    session_id: str = "default"
    path: str


class DefaultWorkspaceRequest(BaseModel):
    path: str


class ApprovalRequest(BaseModel):
    session_id: str
    task_id: str
    approved: bool


class CancelTaskRequest(BaseModel):
    session_id: str


# 全局工作目录（命令执行 API 使用）
_work_dir = str(Path.cwd())
_fallback_workspace = Path(__file__).resolve().parent.parent / "cloned_repos"

_local_agent_services = None
_local_agent_runtime = None


def get_local_agent_services():
    global _local_agent_services
    if _local_agent_services is None:
        from agent.runtime.tooling import LocalAgentServices
        _local_agent_services = LocalAgentServices.create()
    return _local_agent_services


def get_local_agent_runtime():
    global _local_agent_runtime
    if _local_agent_runtime is None:
        from agent.llm import call_llm_with_tools
        from agent.runtime.runtime import LocalAgentRuntime
        from agent.runtime.tooling import build_tool_registry
        from agent.memory import memory

        services = get_local_agent_services()
        _local_agent_runtime = LocalAgentRuntime(
            services.workspaces,
            lambda session_id: build_tool_registry(session_id, services),
            call_llm_with_tools,
            task_store=memory,
            context_engine=services.context,
        )
    return _local_agent_runtime


def resolve_agent_workspace(session_id: str, requested_path: str | None = None):
    from agent.memory import memory

    services = get_local_agent_services()
    stored = memory.get_workspace_state(session_id)
    if requested_path:
        root = services.workspaces.bind(session_id, requested_path)
        memory.set_workspace(session_id, str(root.root))
        return root, "request"
    if stored:
        root = services.workspaces.bind(session_id, stored["root"])
        if stored.get("current_path"):
            services.workspaces.set_current_path(session_id, stored["current_path"])
            root = services.workspaces.get(session_id)
        return root, "session"
    default = memory.get_preference("default_workspace_root")
    if default and Path(default).is_dir():
        root = services.workspaces.bind(session_id, default)
        memory.set_workspace(session_id, str(root.root))
        return root, "default"
    _fallback_workspace.mkdir(parents=True, exist_ok=True)
    root = services.workspaces.bind(session_id, _fallback_workspace)
    memory.set_workspace(session_id, str(root.root))
    return root, "fallback"


def ensure_local_agent_workspace(session_id: str):
    return resolve_agent_workspace(session_id)[0]


async def run_local_agent_once(
    session_id: str,
    message: str,
    *,
    workspace: str | None = None,
    history: list[dict] | None = None,
) -> dict:
    bound_workspace, _ = resolve_agent_workspace(session_id, workspace)
    from agent.llm import get_model, get_protocol

    model_context = {
        "id": get_model(),
        "protocol": get_protocol(),
        "base_url": os.environ.get("LLM_BASE_URL", ""),
    }
    task_id = uuid.uuid4().hex
    final_event = None
    async for event in get_local_agent_runtime().run(
        session_id,
        message,
        history=history or [],
        task_id=task_id,
        model_context=model_context,
    ):
        if event.get("type") == "done":
            final_event = event
    return {
        "task_id": task_id,
        "workspace": str(bound_workspace.root),
        "status": (final_event or {}).get("status", "failed"),
        "response": (final_event or {}).get("content", ""),
    }


def require_agent_workspace(session_id: str, requested_path: str | None = None):
    from agent.memory import memory

    stored = memory.get_workspace_state(session_id)
    if stored is None and requested_path:
        return resolve_agent_workspace(session_id, requested_path)
    workspace, source = resolve_agent_workspace(session_id)
    if requested_path:
        requested = Path(requested_path).expanduser().resolve()
        if requested != workspace.root:
            raise ValueError(
                f"工作区已变化：当前会话为 {workspace.root}，请求仍携带 {requested}；"
                "请刷新工作区后重试"
            )
    return workspace, source


# 本地Agent对话 - SSE 流式输出（结构化工具调用 + 意图分类）
@router_agent.post("/api/agent/chat/stream")
async def local_agent_chat_stream(request: LocalChatRequest):
    from agent.llm import call_llm, call_llm_stream, call_llm_with_tools
    from agent.prompts import CHAT_SYSTEM_PROMPT, CLASSIFY_PROMPT
    from agent.tool_defs import get_tools, execute_tool, get_state, critique_and_retry
    from agent.tools import get_system_info
    from agent.memory import memory

    async def event_generator():
        try:
            session_id = request.session_id
            user_msg = request.message
            repo = request.repo

            # 加载对话历史
            history = memory.get_history(session_id, limit=10)
            messages = []
            for h in history[-6:]:
                messages.append({"role": h["role"], "content": h["content"]})

            if request.agent_mode:
                services = get_local_agent_services()
                try:
                    workspace, _ = require_agent_workspace(session_id, request.workspace)
                except Exception as exc:
                    yield f"data: {json.dumps({'type': 'error', 'content': str(exc)}, ensure_ascii=False)}\n\n"
                    return

                full_response = ""
                runtime = get_local_agent_runtime()
                final_status = None
                task_id = uuid.uuid4().hex
                if hasattr(runtime, "register_task"):
                    runtime.register_task(session_id, task_id)
                yield f"data: {json.dumps({'type': 'workspace', 'path': str(workspace.root), 'session_id': session_id, 'task_id': task_id}, ensure_ascii=False)}\n\n"
                from agent.llm import get_model, get_protocol
                model_context = {
                    "id": get_model(),
                    "protocol": get_protocol(),
                    "base_url": os.environ.get("LLM_BASE_URL", ""),
                }
                async for event in runtime.run(
                    session_id,
                    user_msg,
                    history=messages,
                    task_id=task_id,
                    model_context=model_context,
                ):
                    if event.get("type") == "done":
                        full_response = event.get("content", full_response)
                        final_status = event.get("status")
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
                if final_status == "completed":
                    memory.add_message(session_id, "user", user_msg, repo)
                    memory.add_message(session_id, "assistant", full_response, repo)
                return

            yield f"data: {json.dumps({'type': 'step', 'step': '分析问题', 'icon': 'search'}, ensure_ascii=False)}\n\n"
            messages.append({"role": "user", "content": user_msg})

            system_info = get_system_info()

            # 意图分类
            import re
            classify_result = await call_llm(
                system=CLASSIFY_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
                max_tokens=50,
                temperature=0.1,
            )
            intent = "chat"
            try:
                json_match = re.search(r'\{[^}]+\}', classify_result)
                if json_match:
                    parsed = json.loads(json_match.group())
                    intent = parsed.get("intent", "chat")
            except:
                pass

            yield f"data: {json.dumps({'type': 'step', 'step': f'意图: {intent}', 'icon': 'tag'}, ensure_ascii=False)}\n\n"

            # ====== 工具调用循环（execute 必走，analyze 在 Agent 模式下也走）======
            use_tools = (intent == "execute") or (request.agent_mode and intent == "analyze")
            if use_tools:
                yield f"data: {json.dumps({'type': 'step', 'step': '准备工具', 'icon': 'tools'}, ensure_ascii=False)}\n\n"

                tools_list = get_tools(agent_mode=request.agent_mode)

                if intent == "execute":
                    system = (
                        "你是一个本地执行助手，通过结构化工具调用来完成用户的操作请求。\n\n"
                        "任务生命周期：感知 → 决策 → 执行 → 验证\n"
                        "1. 感知：用 sense_* 工具预计算项目信息，结果写入共享状态\n"
                        "2. 决策：根据状态和用户需求，决定调用哪个工具\n"
                        "3. 执行：调用基础工具完成操作\n"
                        "4. 验证：检查执行结果，失败则触发 Critic 自愈（最多3次）\n\n"
                        "关键规则：\n"
                        "- 调用工具时，不要输出解释性文字，直接调用工具即可\n"
                        "- clone_repo 成功后，系统会自动触发 sense_repo_health 填充状态\n"
                        "- 执行失败时，分析错误类型（代码错误 vs 环境错误），不要重复同样的修复\n"
                        "- sense_devops 返回 passed:false 时，必须先解决 CI 问题再继续\n"
                        "- 只在最终回复时输出完整的总结，中间过程保持简洁\n"
                        f"当前系统: {system_info['os']} {system_info['python']}, shell={system_info['shell']}, home={system_info['home']}"
                    )
                else:
                    system = (
                        "你是一个技术分析助手，通过调用工具来深入分析 GitHub 项目。\n\n"
                        "任务生命周期：感知 → 决策 → 分析\n"
                        "1. 感知：用 sense_* 工具获取结构化分析数据\n"
                        "2. 决策：根据用户需求选择合适的分析维度\n"
                        "3. 分析：综合所有感知数据，给出深入解读\n\n"
                        "可用感知工具：\n"
                        "- sense_repo_health: 项目健康度（score, maintenance_status, learning_value）\n"
                        "- sense_architecture: 架构分析（patterns, tech_stack, modules）\n"
                        "- sense_issues: Issue 痛点（pain_points, suggested_solutions）\n"
                        "- sense_devops: CI/CD 门控（passed: bool，失败时强制回退）\n\n"
                        "所有工具共享状态容器——先前工具的输出会自动传递给后续工具。"
                    )
                if request.agent_mode:
                    system += (
                        "\n\n[Agent 模式] 你拥有完整的感知工具集。\n"
                        "系统会在检测到新仓库时自动触发预感知（sense_repo_health），\n"
                        "结果写入共享状态供后续工具使用。\n"
                        "自愈循环：执行失败 → Critic 分析错误原因 → 修正策略 → 重试（最多3次）。\n"
                        "Critic 会区分代码错误和环境错误，强制使用不同的修复策略。"
                    )
                if repo:
                    system += f"\n当前项目: {repo}"

                full_response = ""
                tool_messages = list(messages)
                tool_round = 0
                MAX_TOOL_ROUNDS = 10

                # 重置 Critic 重试计数器（每次新消息）
                state = get_state(session_id)
                state.fix_attempts = 0
                state.errors = []
                state.critic_feedback = ""

                while tool_round < MAX_TOOL_ROUNDS:
                    tool_round += 1
                    result = await call_llm_with_tools(
                        system=system,
                        messages=tool_messages,
                        tools=tools_list,
                        max_tokens=4000,
                    )

                    # 没有工具调用，结束循环 — 最终回复
                    if not result["tool_uses"]:
                        full_response = result["text"]
                        if result["text"]:
                            yield f"data: {json.dumps({'type': 'token', 'content': result['text']}, ensure_ascii=False)}\n\n"
                        break

                    # 有工具调用，逐个执行
                    assistant_content = []
                    if result["text"]:
                        assistant_content.append({"type": "text", "text": result["text"]})
                    for tu in result["tool_uses"]:
                        assistant_content.append({
                            "type": "tool_use",
                            "id": tu["id"],
                            "name": tu["name"],
                            "input": tu["input"],
                        })
                    tool_messages.append({"role": "assistant", "content": assistant_content})

                    tool_results = []

                    sense_tus = [tu for tu in result["tool_uses"] if tu["name"].startswith("sense_")]
                    regular_tus = [tu for tu in result["tool_uses"] if not tu["name"].startswith("sense_")]

                    # --- 并行执行感知工具 ---
                    if sense_tus:
                        names_str = ", ".join(tu["name"] for tu in sense_tus)
                        yield f"data: {json.dumps({'type': 'step', 'step': f'并行感知: {names_str}', 'icon': 'search'}, ensure_ascii=False)}\n\n"
                        for tu in sense_tus:
                            yield f"data: {json.dumps({'type': 'tool_call', 'name': tu['name'], 'args': tu['input']}, ensure_ascii=False)}\n\n"

                        sense_exec_results = await asyncio.gather(
                            *[execute_tool(tu["name"], tu["input"], session_id=session_id) for tu in sense_tus]
                        )
                        for tu, exec_result in zip(sense_tus, sense_exec_results):
                            success = exec_result.get("success", False)
                            output = exec_result.get("output", exec_result.get("content", str(exec_result)))
                            output_str = json.dumps(output, ensure_ascii=False, default=str) if isinstance(output, dict) else str(output)
                            yield f"data: {json.dumps({'type': 'tool_result', 'name': tu['name'], 'success': success, 'output': output_str[:500]}, ensure_ascii=False)}\n\n"
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": tu["id"],
                                "content": json.dumps(exec_result, ensure_ascii=False, default=str),
                            })

                    # --- 顺序执行非感知工具（含 Critic 自愈）---
                    for tu in regular_tus:
                        tool_name = tu["name"]
                        tool_input = tu["input"]

                        yield f"data: {json.dumps({'type': 'step', 'step': f'调用工具: {tool_name}', 'icon': 'play'}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'tool_call', 'name': tool_name, 'args': tool_input}, ensure_ascii=False)}\n\n"

                        # ── run_command：流式执行 + 危险检测 ──
                        if tool_name == "run_command":
                            from agent.tools import classify_command_risk, run_command_stream
                            cmd_str = tool_input.get("command", "")
                            risk = classify_command_risk(cmd_str)
                            yield f"data: {json.dumps({'type': 'cmd_preview', 'command': cmd_str, 'risk': risk['risk'], 'reason': risk.get('reason', '')}, ensure_ascii=False)}\n\n"

                            output_lines: list[str] = []
                            exec_result = {"success": False, "output": ""}
                            async for chunk in run_command_stream(
                                cmd_str,
                                cwd=tool_input.get("cwd"),
                                timeout=tool_input.get("timeout", 60),
                            ):
                                if chunk["type"] == "line":
                                    output_lines.append(chunk["text"])
                                    yield f"data: {json.dumps({'type': 'cmd_line', 'text': chunk['text']}, ensure_ascii=False)}\n\n"
                                elif chunk["type"] == "done":
                                    exec_result = {"success": chunk["success"], "output": "\n".join(output_lines)}
                                    yield f"data: {json.dumps({'type': 'cmd_done', 'success': chunk['success'], 'returncode': chunk['returncode']}, ensure_ascii=False)}\n\n"
                                elif chunk["type"] == "error":
                                    exec_result = {"success": False, "output": chunk["text"]}
                                    yield f"data: {json.dumps({'type': 'cmd_done', 'success': False, 'returncode': -1}, ensure_ascii=False)}\n\n"
                        else:
                            exec_result = await execute_tool(tool_name, tool_input, session_id=session_id)

                        success = exec_result.get("success", False)
                        output = exec_result.get("output", exec_result.get("content", str(exec_result)))

                        # Critic 自愈：run_command 失败时触发
                        if not success and tool_name == "run_command":
                            state = get_state(session_id)
                            if state.fix_attempts < 3:
                                original_code = tool_input.get("command", "")
                                critique = await critique_and_retry(state, str(output), original_code)
                                error_type = critique.get("error_type", "code_error")
                                attempt = critique["attempt"]

                                if not critique.get("should_retry"):
                                    yield f"data: {json.dumps({'type': 'step', 'step': f'Critic: 无法自动修复 ({error_type})', 'icon': 'tools'}, ensure_ascii=False)}\n\n"
                                    yield f"data: {json.dumps({'type': 'tool_result', 'name': 'critic', 'success': False, 'output': critique['output'][:500]}, ensure_ascii=False)}\n\n"
                                else:
                                    yield f"data: {json.dumps({'type': 'step', 'step': f'Critic 审查 #{attempt} [{error_type}]', 'icon': 'tools'}, ensure_ascii=False)}\n\n"
                                    yield f"data: {json.dumps({'type': 'tool_result', 'name': 'critic', 'success': True, 'output': critique['output'][:500]}, ensure_ascii=False)}\n\n"

                                    if state.fix_attempts >= 2:
                                        original_user_msg = tool_messages[0] if tool_messages else {"role": "user", "content": user_msg}
                                        failed_summary = "\n".join([
                                            f"- 第{i+1}次失败 ({e[:100]})" for i, e in enumerate(state.errors)
                                        ])
                                        tool_messages = [
                                            original_user_msg,
                                            {
                                                "role": "user",
                                                "content": (
                                                    f"[上下文剪枝] 之前 {state.fix_attempts} 次尝试均失败：\n{failed_summary}\n\n"
                                                    f"[Critic 分析 - 错误类型: {error_type}]\n{critique['output'][:600]}\n\n"
                                                    "请根据 Critic 的分析，采用完全不同的策略重试。"
                                                ),
                                            },
                                        ]
                                    else:
                                        tool_results.append({
                                            "type": "tool_result",
                                            "tool_use_id": tu["id"],
                                            "content": json.dumps(exec_result, ensure_ascii=False, default=str),
                                        })
                                        tool_results.append({
                                            "type": "text",
                                            "text": f"[Critic 审查 - {error_type}] {critique['output'][:600]}",
                                        })
                                    continue

                        # 自动预感知：clone_repo 成功后触发 sense_repo_health
                        if success and tool_name == "clone_repo":
                            state = get_state(session_id)
                            if state.health_score is None:
                                yield f"data: {json.dumps({'type': 'step', 'step': '自动预感知: 项目健康度', 'icon': 'search'}, ensure_ascii=False)}\n\n"
                                sense_result = await execute_tool("sense_repo_health", {"repo": tool_input.get("repo", "")}, session_id=session_id)
                                if sense_result.get("success"):
                                    yield f"data: {json.dumps({'type': 'tool_call', 'name': 'sense_repo_health', 'args': {'repo': tool_input.get('repo', '')}}, ensure_ascii=False)}\n\n"
                                    sense_out = json.dumps(sense_result.get("output", {}), ensure_ascii=False)
                                    yield f"data: {json.dumps({'type': 'tool_result', 'name': 'sense_repo_health', 'success': True, 'output': sense_out[:500]}, ensure_ascii=False)}\n\n"

                        # 输出工具结果
                        output_str = json.dumps(output, ensure_ascii=False, default=str) if isinstance(output, dict) else str(output)
                        yield f"data: {json.dumps({'type': 'tool_result', 'name': tool_name, 'success': success, 'output': output_str[:500] if output_str else ''}, ensure_ascii=False)}\n\n"

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu["id"],
                            "content": json.dumps(exec_result, ensure_ascii=False, default=str),
                        })

                    # 把 tool_result 追加到消息，继续循环
                    tool_messages.append({"role": "user", "content": tool_results})

                    if result["text"]:
                        yield f"data: {json.dumps({'type': 'token', 'content': result['text']}, ensure_ascii=False)}\n\n"

                    if result["stop_reason"] == "end_turn":
                        full_response = result["text"]
                        if result["text"]:
                            yield f"data: {json.dumps({'type': 'token', 'content': result['text']}, ensure_ascii=False)}\n\n"
                        break

                if tool_round >= MAX_TOOL_ROUNDS and not full_response:
                    full_response = "已达最大工具调用轮数（10次），任务可能未完全完成。"
                    yield f"data: {json.dumps({'type': 'token', 'content': full_response}, ensure_ascii=False)}\n\n"

            # ====== 对话/分析模式：流式 LLM 输出 ======
            else:
                from agent.llm import call_llm_stream
                from agent.prompts import CHAT_SYSTEM_PROMPT
                system = CHAT_SYSTEM_PROMPT + f"\n\n系统信息: {system_info['os']} {system_info['python']}"
                if repo:
                    system += f"\n\n当前项目: {repo}"

                yield f"data: {json.dumps({'type': 'step', 'step': '生成回复', 'icon': 'edit'}, ensure_ascii=False)}\n\n"

                full_response = ""
                async for chunk in call_llm_stream(system=system, messages=messages):
                    full_response += chunk
                    yield f"data: {json.dumps({'type': 'token', 'content': chunk}, ensure_ascii=False)}\n\n"

            # 保存到记忆
            memory.add_message(session_id, "user", user_msg, repo)
            memory.add_message(session_id, "assistant", full_response, repo)

            yield f"data: {json.dumps({'type': 'step', 'step': '完成', 'icon': 'check'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': full_response}, ensure_ascii=False)}\n\n"
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# 项目环境配置
@router_agent.post("/api/agent/setup")
async def agent_setup(request: LocalChatRequest):
    local_result = await run_local_agent_once(
        request.session_id,
        request.message or f"部署 {request.repo or '当前项目'}",
        workspace=request.workspace,
    )
    return {
        "success": local_result["status"] == "completed",
        "task_id": local_result["task_id"],
        "status": local_result["status"],
        "steps": [],
        "message": local_result["response"],
    }


# 用户确认后恢复执行
@router_agent.post("/api/agent/confirm")
async def agent_confirm(request: LocalChatRequest):
    raise HTTPException(
        status_code=410,
        detail="此端点已退役；请使用 /api/agent/approval 并提供 task_id 与 approved。",
    )


# 执行命令 - 直接调用工具
@router_agent.post("/api/agent/execute")
async def agent_execute(request: ExecuteRequest):
    from agent.tools import run_command
    result = run_command(request.command, cwd=request.cwd)
    return result


# 获取项目状态
@router_agent.get("/api/agent/projects")
async def get_projects():
    from agent.memory import memory
    return {"projects": memory.get_all_projects()}


# 获取对话历史
@router_agent.get("/api/agent/history/{session_id}")
async def get_history(session_id: str):
    from agent.memory import memory
    return {"history": memory.get_history(session_id)}


# 获取操作日志
@router_agent.get("/api/agent/logs")
async def get_logs(repo: str = None):
    from agent.memory import memory
    return {"logs": memory.get_action_logs(repo)}


@router_agent.post("/api/agent/workspace")
async def bind_agent_workspace(request: WorkspaceRequest):
    from agent.memory import memory

    services = get_local_agent_services()
    workspace = services.workspaces.bind(request.session_id, request.path)
    memory.set_workspace(request.session_id, str(workspace.root))
    return {
        "session_id": request.session_id,
        "workspace": str(workspace.root),
        "root": str(workspace.root),
        "current_path": str(workspace.root),
        "source": "session",
        "profile": services.workspaces.describe(workspace.root),
        "recent": memory.get_recent_workspaces(),
    }


@router_agent.get("/api/agent/workspace/default")
async def get_default_agent_workspace():
    from agent.memory import memory

    configured = memory.get_preference("default_workspace_root")
    if configured and Path(configured).is_dir():
        return {"path": str(Path(configured).resolve()), "source": "configured"}
    return {"path": str(_fallback_workspace), "source": "fallback"}


@router_agent.put("/api/agent/workspace/default")
async def set_default_agent_workspace(request: DefaultWorkspaceRequest):
    from agent.memory import memory

    path = Path(request.path).expanduser()
    if not path.is_absolute():
        raise HTTPException(status_code=400, detail="默认工作目录必须是绝对路径")
    try:
        path = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"默认工作目录无效: {exc}") from exc
    if not path.is_dir():
        raise HTTPException(status_code=400, detail=f"默认工作目录不存在: {path}")
    memory.set_preference("default_workspace_root", str(path))
    return {"path": str(path), "source": "configured"}


@router_agent.get("/api/agent/workspace/{session_id}")
async def get_agent_workspace(session_id: str):
    services = get_local_agent_services()
    from agent.memory import memory

    workspace, source = resolve_agent_workspace(session_id)
    state = memory.get_workspace_state(session_id) or {"root": str(workspace.root), "current_path": str(workspace.root)}
    return {
        "session_id": session_id,
        "workspace": str(workspace.root),
        "root": state["root"],
        "current_path": state["current_path"],
        "source": source,
        "profile": services.workspaces.describe(workspace.root),
        "recent": memory.get_recent_workspaces(),
    }


@router_agent.get("/api/agent/traces")
async def list_agent_traces(limit: int = 50):
    from agent.memory import memory

    return {"traces": memory.list_agent_traces(max(1, min(limit, 100)))}


def _project_task_states(memory, limit: int = 500):
    states = []
    for trace in memory.list_agent_traces(limit):
        state = memory.get_agent_task(trace["task_id"])
        if state is None:
            continue
        states.append({**state, "created_at": trace.get("created_at"), "updated_at": trace.get("updated_at")})
    return states


def _resolve_project_task(memory, project_id: str):
    # Keep task-id lookups working while callers migrate to stable project ids.
    direct = memory.get_agent_task(project_id)
    if direct is not None:
        return direct
    from agent.runtime.project_projection import project_id_for_workspace

    for state in _project_task_states(memory):
        if project_id_for_workspace(state.get("workspace_root", "")) == project_id:
            return state
    return None


def _project_history(memory, project_id: str):
    from agent.runtime.project_projection import project_id_for_workspace

    traces = {trace["task_id"]: trace for trace in memory.list_agent_traces(500)}
    records = []
    for state in _project_task_states(memory):
        if state.get("task_id") != project_id and project_id_for_workspace(state.get("workspace_root", "")) != project_id:
            continue
        records.append({
            "task": state,
            "activity": memory.get_agent_task_activity(state["task_id"]),
            "trace": traces.get(state["task_id"]),
        })
    return records


@router_agent.get("/api/projects")
async def list_project_workspaces():
    from agent.memory import memory
    from agent.runtime.project_projection import build_project_summary

    return {"projects": build_project_summary(_project_task_states(memory))}


@router_agent.get("/api/projects/{project_id}/overview")
async def get_project_overview(project_id: str):
    """Read-only project journey summary backed by persisted agent facts."""
    from agent.memory import memory
    from agent.runtime.project_projection import build_project_overview

    task = _resolve_project_task(memory, project_id)
    task_id = task.get("task_id") if task else None
    activity = memory.get_agent_task_activity(task_id) if task_id else {}
    workspace = memory.get_workspace_state(task.get("session_id", "")) if task else None
    return build_project_overview(
        project_id=project_id,
        workspace=workspace,
        task=task,
        activity=activity,
        traces=memory.list_agent_traces(500),
    )


@router_agent.get("/api/projects/{project_id}/evidence")
async def get_project_evidence(project_id: str):
    """Read-only developer evidence layer; no Agent work is started."""
    from agent.memory import memory
    from agent.runtime.project_projection import build_project_evidence

    task = _resolve_project_task(memory, project_id)
    if task is None:
        return build_project_evidence(project_id=project_id, workspace=None, task=None, activity={})
    activity = memory.get_agent_task_activity(task["task_id"])
    workspace = memory.get_workspace_state(task.get("session_id", ""))
    return build_project_evidence(
        project_id=project_id,
        workspace=workspace,
        task=task,
        activity=activity,
        history=_project_history(memory, project_id),
    )


@router_agent.get("/api/agent/memory/search")
async def search_agent_memory(
    workspace: str,
    q: str,
    limit: int = 8,
    verified_only: bool = False,
):
    from agent.memory import memory

    return {
        "memories": memory.search_project_memories(
            workspace,
            q,
            limit=max(1, min(limit, 50)),
            verified_only=verified_only,
        ),
    }


@router_agent.get("/api/agent/observability")
async def get_observability_status():
    return {
        "local": {
            "enabled": True,
            "storage": "SQLite",
            "retention": "agent_tasks + agent_events + project_memories",
            "coverage": ["model", "tool", "approval", "file", "verification", "process", "terminal"],
        },
    }


@router_agent.post("/api/agent/approval")
async def approve_agent_operation(request: ApprovalRequest):
    ensure_local_agent_workspace(request.session_id)
    result = await get_local_agent_runtime().confirm(
        request.session_id,
        request.task_id,
        request.approved,
    )
    return result.to_dict()


@router_agent.post("/api/agent/approval/stream")
async def approve_agent_operation_stream(request: ApprovalRequest):
    from agent.memory import memory

    async def event_generator():
        ensure_local_agent_workspace(request.session_id)
        runtime = get_local_agent_runtime()
        final_event = None
        async for event in runtime.resume(request.session_id, request.task_id, request.approved):
            if event.get("type") == "done":
                final_event = event
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        if final_event and final_event.get("status") == "completed":
            task = memory.get_agent_task(request.task_id)
            if task:
                memory.add_message(request.session_id, "user", task.get("user_message", ""))
                memory.add_message(request.session_id, "assistant", final_event.get("content", ""))

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router_agent.get("/api/agent/tasks/{task_id}")
async def get_agent_task(task_id: str):
    from agent.memory import memory

    task = memory.get_agent_task(task_id)
    if task is None:
        return {"task": None, "activity": {"events": [], "tool_runs": [], "changesets": []}}
    return {"task": task, "activity": memory.get_agent_task_activity(task_id)}


@router_agent.get("/api/agent/tasks/{task_id}/artifacts/{artifact_id}")
async def get_agent_artifact(task_id: str, artifact_id: str):
    from agent.memory import memory
    artifact = memory.get_agent_artifact(task_id, artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail="Artifact 不存在")
    return artifact


@router_agent.post("/api/agent/tasks/{task_id}/cancel")
async def cancel_agent_task(task_id: str, request: CancelTaskRequest):
    return get_local_agent_runtime().cancel(request.session_id, task_id).to_dict()


@router_agent.get("/api/agent/sessions/{session_id}/active-task")
async def get_active_agent_task(session_id: str):
    from agent.memory import memory

    task = memory.get_latest_agent_task(session_id, status="waiting_approval")
    if task is None:
        return {"task": None, "activity": {"events": [], "tool_runs": [], "changesets": []}}
    return {"task": task, "activity": memory.get_agent_task_activity(task["task_id"])}


@router_agent.get("/api/agent/processes/{session_id}")
async def list_agent_processes(session_id: str):
    result = get_local_agent_services().processes.list(session_id)
    return result.data


@router_agent.get("/api/agent/processes/{session_id}/{process_id}")
async def get_agent_process(session_id: str, process_id: str):
    return get_local_agent_services().processes.get(session_id, process_id).to_dict()


@router_agent.post("/api/agent/processes/{session_id}/{process_id}/stop")
async def stop_agent_process(session_id: str, process_id: str):
    return get_local_agent_services().processes.stop(session_id, process_id).to_dict()


# ========== Multi-Agent Swarm API ==========

@router_agent.post("/api/agent/swarm")
async def swarm_chat(request: LocalChatRequest):
    """Multi-Agent Swarm 端点 — 5 个子智能体协作"""
    raise HTTPException(
        status_code=410,
        detail="多智能体实验端点已退役；请使用 /api/agent/chat/stream。",
    )


# ========== MCP 工具 API ==========

@router_agent.get("/api/mcp/status")
async def mcp_status():
    """获取 MCP Server 连接状态"""
    from agent.mcp_client import get_mcp_client, init_mcp
    client = await get_mcp_client()
    if not client.is_connected():
        result = await init_mcp()
        return {
            "connected": client.is_connected(),
            "servers": client.get_server_names(),
            "tools_count": len(client.get_all_tools()),
            "init_result": result,
        }
    return {
        "connected": True,
        "servers": client.get_server_names(),
        "tools_count": len(client.get_all_tools()),
    }


@router_agent.get("/api/mcp/tools")
async def mcp_tools():
    """获取所有可用的 MCP 工具列表"""
    from agent.tools import get_mcp_tools_info
    return await get_mcp_tools_info()


@router_agent.post("/api/mcp/call")
async def mcp_call(request: dict):
    """调用 MCP 工具"""
    from agent.mcp_client import mcp_tool_call
    tool_name = request.get("tool")
    arguments = request.get("arguments", {})
    if not tool_name:
        return {"success": False, "error": "缺少 tool 参数"}
    return await mcp_tool_call(tool_name, arguments)


# ========== 命令执行 API ==========

@router_agent.post("/api/local/run")
async def run_command_endpoint(request: CommandRequest):
    """执行系统命令"""
    global _work_dir
    try:
        if request.cwd:
            _work_dir = request.cwd
        elif not Path(_work_dir).exists():
            _work_dir = str(Path.cwd())

        cmd = request.command.strip()
        if cmd.lower().startswith('cd '):
            new_dir = cmd[3:].strip().strip('"').strip("'")
            if new_dir == '..':
                _work_dir = str(Path(_work_dir).parent)
            elif Path(new_dir).is_absolute():
                _work_dir = new_dir
            else:
                _work_dir = str(Path(_work_dir) / new_dir)
            return {"success": True, "output": f"已切换到: {_work_dir}", "cwd": _work_dir}

        if os.name == 'nt':
            shell_cmd = ["cmd", "/c", cmd]
        else:
            shell_cmd = ["bash", "-c", cmd]

        result = subprocess.run(
            shell_cmd,
            cwd=_work_dir,
            capture_output=True,
            text=True,
            timeout=120,
            encoding='utf-8',
            errors='replace'
        )

        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr

        return {
            "success": result.returncode == 0,
            "output": output.strip(),
            "returncode": result.returncode,
            "cwd": _work_dir
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "命令执行超时", "cwd": _work_dir}
    except Exception as e:
        return {"success": False, "output": str(e), "cwd": _work_dir}


@router_agent.post("/api/local/reset-cwd")
async def reset_cwd():
    """重置工作目录"""
    global _work_dir
    _work_dir = str(Path.cwd())
    return {"cwd": _work_dir}
