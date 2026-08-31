import json
import urllib.error
import uuid
from dataclasses import dataclass

from .commands import CommandRunner
from .context import ContextEngine
from .edits import EditEngine
from .file_tools import FileTools
from .models import ToolResult, ToolRisk
from .network import NetworkTools
from .processes import ProcessManager
from .project_tools import ProjectTools
from .registry import ToolDefinition, ToolRegistry
from .verifier import Verifier
from .workspace import WorkspaceManager


@dataclass
class LocalAgentServices:
    workspaces: WorkspaceManager
    files: FileTools
    edits: EditEngine
    commands: CommandRunner
    processes: ProcessManager
    projects: ProjectTools
    verifier: Verifier
    network: NetworkTools
    context: ContextEngine

    @classmethod
    def create(cls) -> "LocalAgentServices":
        from agent.memory import memory

        workspaces = WorkspaceManager()
        commands = CommandRunner(
            workspaces,
            on_current_path_change=lambda session_id, path: memory.set_current_path(session_id, str(path)),
        )
        processes = ProcessManager(workspaces)
        return cls(
            workspaces=workspaces,
            files=FileTools(workspaces),
            edits=EditEngine(workspaces),
            commands=commands,
            processes=processes,
            projects=ProjectTools(workspaces, commands),
            verifier=Verifier(workspaces, commands),
            network=NetworkTools(processes),
            context=ContextEngine(workspaces),
        )


# 运行时构造后注入：spawn_subagent 等需要模型循环的工具经此访问 runtime。
_active_runtime: "LocalAgentRuntime | None" = None


def set_active_runtime(runtime: "LocalAgentRuntime | None") -> None:
    global _active_runtime
    _active_runtime = runtime


# 治理的纯文本规则（boundary_violation / classify_command_risk）规范定义在
# guard.py（治理规则同源，审计 E 期）。这里保留兼容别名，避免全部调用点同步改。
from .guard import boundary_violation, classify_command_risk  # noqa: F401


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def build_tool_registry(session_id: str, services: LocalAgentServices) -> ToolRegistry:
    registry = ToolRegistry(trace_metadata=lambda: {
        "session_id": session_id,
        "workspace_root": str(services.workspaces.get(session_id).root),
        "cwd": str(services.workspaces.current_path(session_id)),
    })

    def add(name, description, properties, required, risk, handler, risk_resolver=None):
        registry.register(ToolDefinition(
            name=name,
            description=description,
            input_schema=_schema(properties, required),
            risk=risk,
            handler=handler,
            risk_resolver=risk_resolver,
        ))

    path_property = {"type": "string", "description": "相对当前工作区的路径"}
    add("list_directory", "列出工作区目录内容", {"path": path_property}, [], ToolRisk.READ,
        lambda a: services.files.list_directory(session_id, a.get("path", ".")))
    add("read_file", "读取 UTF-8 文本文件，可指定行范围", {
        "path": path_property,
        "start_line": {"type": "integer", "minimum": 1},
        "end_line": {"type": "integer", "minimum": 1},
    }, ["path"], ToolRisk.READ, lambda a: services.files.read_file(
        session_id, a["path"], a.get("start_line", 1), a.get("end_line")
    ))
    add("search_text", "在工作区搜索精确文本。path 传目录则递归搜索该目录下所有文件，传文件路径则只搜该文件（默认 . 为整个工作区）", {
        "query": {"type": "string"}, "path": path_property,
    }, ["query"], ToolRisk.READ,
        lambda a: services.files.search_text(session_id, a["query"], a.get("path", ".")))
    add("repo_map", "生成工作区源码文件和主要符号的轻量 Repo Map", {
        "path": path_property,
    }, [], ToolRisk.READ, lambda a: services.context.repo_map(session_id, a.get("path", ".")))
    add("create_directory", "在工作区创建目录", {"path": path_property}, ["path"], ToolRisk.WRITE_SAFE,
        lambda a: services.files.create_directory(session_id, a["path"]))
    add("edit_files", "原子应用一个或多个文件编辑。replace 的 search 必须唯一匹配", {
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": path_property,
                    "operation": {"type": "string", "enum": ["write", "replace"]},
                    "search": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "operation", "content"],
            },
        },
    }, ["edits"], ToolRisk.WRITE_SAFE, lambda a: services.edits.apply(session_id, a["edits"]))
    add("undo_last_change", "撤销当前会话最近一次文件修改", {}, [], ToolRisk.WRITE_SAFE,
        lambda a: services.edits.undo_last(session_id))
    add("run_command", "在工作区运行前台 PowerShell/bash 命令并返回真实退出码", {
        "command": {"type": "string"},
        "cwd": path_property,
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 600, "description": "秒，最大 600"},
    }, ["command"], ToolRisk.PROCESS, lambda a: services.commands.run(
        session_id, a["command"], a.get("cwd", "."), a.get("timeout", 60)
    ), classify_command_risk)
    add("clone_repository", "克隆远程 Git 仓库到工作区内目录。仅当用户明确要求克隆/下载该仓库时使用；了解/介绍仓库请用 web_fetch", {
        "url": {"type": "string"}, "destination": path_property,
    }, ["url", "destination"], ToolRisk.WRITE_SAFE,
        lambda a: services.projects.clone_repository(session_id, a["url"], a["destination"]))
    add("detect_project", "检测 Python/Node 项目、包管理器和可用脚本", {"path": path_property}, [], ToolRisk.READ,
        lambda a: services.projects.detect(session_id, a.get("path", ".")))
    add("ensure_venv", "为 Python 项目创建或复用工作区 .venv。Python 项目只能使用此工具创建/复用的项目内 .venv，禁止系统 Python 或其他目录的解释器", {"path": path_property}, [], ToolRisk.PROCESS,
        lambda a: services.projects.ensure_venv(session_id, a.get("path", ".")))

    def install(a):
        path = a.get("path", ".")
        detected = services.projects.detect(session_id, path)
        if not detected.success:
            return detected
        return services.projects.install_dependencies(session_id, detected.data, path)

    add("install_dependencies", "使用项目自己的环境安装 Python/Node 依赖", {"path": path_property}, [], ToolRisk.PROCESS, install)

    def verify(a):
        path = a.get("path", ".")
        detected = services.projects.detect(session_id, path)
        if not detected.success:
            return detected
        return services.verifier.run(session_id, detected.data, path)

    add("verify_project", "运行项目测试、编译或构建并保留真实失败", {"path": path_property}, [], ToolRisk.PROCESS, verify)
    add("start_process", "启动后台服务并返回进程 ID", {
        "command": {"type": "string"}, "cwd": path_property,
        "host": {"type": "string", "enum": ["127.0.0.1", "::1"]},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
    }, ["command"], ToolRisk.PROCESS,
        lambda a: services.processes.start(
            session_id, a["command"], a.get("cwd", "."), host=a.get("host"), port=a.get("port"),
        ), classify_command_risk)
    add("get_process", "获取后台进程状态和日志", {"process_id": {"type": "string"}}, ["process_id"], ToolRisk.READ,
        lambda a: services.processes.get(session_id, a["process_id"]))
    add("list_processes", "列出当前会话后台进程", {}, [], ToolRisk.READ,
        lambda a: services.processes.list(session_id))
    add("stop_process", "停止当前会话拥有的后台进程树", {"process_id": {"type": "string"}}, ["process_id"], ToolRisk.PROCESS,
        lambda a: services.processes.stop(session_id, a["process_id"]))
    add("check_port", "检查本机 TCP 端口是否已开放", {
        "host": {"type": "string"},
        "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 10, "description": "秒，最大 10"},
    }, ["host", "port"], ToolRisk.READ,
        lambda a: services.network.check_port(a["host"], a["port"], a.get("timeout", 1)))
    def http_request(a):
        try:
            status, body, headers = services.network.request(
                a["method"], a["url"], a.get("headers"), a.get("json"), a.get("timeout", 15),
            )
        except (ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
            return ToolResult.fail(str(exc), error_kind="http_error")
        return ToolResult(
            success=200 <= status < 400,
            data={"method": a["method"].upper(), "url": a["url"], "status": status, "headers": headers},
            output=body,
            error=None if 200 <= status < 400 else f"HTTP 请求失败: {status}",
            error_kind=None if 200 <= status < 400 else "http_status",
        )

    add("http_request", "向本机 HTTP 服务发送结构化请求（仅限 127.0.0.1/::1 回环地址，用于验收本机启动的服务）；访问外网/第三方 API 请用 web_fetch", {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "url": {"type": "string"},
        "headers": {"type": "object"},
        "json": {},
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 60, "description": "秒，最大 60"},
    }, ["method", "url"], ToolRisk.READ, http_request)

    add("web_search", "无 key 网页搜索：返回标题/链接/摘要列表（DuckDuckGo 匿名抓取，失败自动回退 Bing RSS）。需要最新信息、事实核查、榜单/新闻时优先用它；要看某条结果的正文再用 web_fetch 抓该 url", {
        "query": {"type": "string", "description": "搜索词，中英文皆可"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 15, "description": "结果条数，默认 8"},
    }, ["query"], ToolRisk.READ,
        lambda a: services.network.search(str(a.get("query", "")), int(a.get("limit", 8) or 8)))

    add("web_fetch", "只读抓取外网页面/API 文本（GET），用于获取仓库信息、文档等；不能访问本机/内网地址", {
        "url": {"type": "string"},
        "timeout": {"type": "number", "minimum": 1, "maximum": 30, "description": "秒"},
    }, ["url"], ToolRisk.READ,
        lambda a: services.network.web_fetch(a["url"], a.get("timeout", 20)))

    def use_skill(a):
        from .skills import load_skills
        name = str(a.get("name", "")).strip()
        skills = load_skills(services.workspaces.get(session_id).root)
        skill = skills.get(name)
        if skill is None:
            available = "、".join(sorted(skills)) or "（无）"
            return ToolResult.fail(
                f"技能不存在: {name}（可用技能：{available}）",
                error_kind="invalid_input",
            )
        return ToolResult.ok(
            data={"skill": name, "source": skill.source},
            output=f"[技能 {name} 已加载]\n{skill.body}",
        )

    add("use_skill", "加载一个技能到上下文（技能正文按需加载，名称见系统提示词技能索引）", {
        "name": {"type": "string"},
    }, ["name"], ToolRisk.READ, use_skill)

    # MCP 扩展工具：来自 .mcp.json 连接的外部 server。默认 EXTERNAL 风险
    # （confirm 档需确认，auto/open/full 自动放行）；未安装 mcp 包或未连接时
    # 静默跳过，不影响内置工具集。
    try:
        from agent.mcp_client import cached_mcp_tools

        # MCP 工具风险启发式：名字/描述明显只读的降级为 READ（少一类审批噪音），
        # 不确定的仍保守兜底 EXTERNAL——审计 B 期
        _MCP_READ_HINTS = (
            "search", "fetch", "get", "list", "read", "find", "query", "describe",
            "lookup", "view", "show", "info", "download", "crawl", "scrape",
        )

        def _mcp_risk(name: str, desc: str) -> ToolRisk:
            haystack = f"{name} {desc}".casefold()
            # 明显写动词的优先级最高，避免 "search and update" 被误判只读
            if any(w in haystack for w in (
                "create", "delete", "remove", "update", "edit", "write",
                "upload", "publish", "send", "execute", "run ", "commit", "merge", "push",
            )):
                return ToolRisk.EXTERNAL
            if any(w in haystack for w in _MCP_READ_HINTS):
                return ToolRisk.READ
            return ToolRisk.EXTERNAL

        for mcp_tool in cached_mcp_tools():
            tool_name = str(mcp_tool.get("name") or "")
            server = str(mcp_tool.get("server") or "mcp")
            if not tool_name or tool_name in registry._definitions:
                continue
            description = str(mcp_tool.get("description") or tool_name)
            schema = mcp_tool.get("input_schema") or {}

            async def mcp_handler(a, _tool_name=tool_name):
                from agent.mcp_client import mcp_tool_call

                payload = await mcp_tool_call(_tool_name, a)
                if payload.get("success"):
                    return ToolResult.ok(
                        data={"server": payload.get("server"), "tool": _tool_name},
                        output=str(payload.get("content") or ""),
                    )
                return ToolResult.fail(
                    str(payload.get("error") or f"MCP 工具 {_tool_name} 调用失败"),
                    error_kind="mcp_error",
                )

            registry.register(ToolDefinition(
                name=tool_name,
                description=f"[MCP:{server}] {description}",
                input_schema=(
                    schema
                    if isinstance(schema, dict) and schema.get("type") == "object"
                    else _schema({})
                ),
                risk=_mcp_risk(tool_name, description),
                handler=mcp_handler,
            ))
    except ImportError:
        pass
    except Exception:
        pass

    async def spawn_subagent(a):
        from .subagent import MAX_SUBAGENT_ROUNDS, MAX_SUBAGENT_TOOL_CALLS, run_subagent
        from .tracing import current_tool_call_context

        runtime = _active_runtime
        if runtime is None:
            return ToolResult.fail("子代理运行时不可用", error_kind="tool_error")
        task = str(a.get("task") or "").strip()
        if not task:
            return ToolResult.fail("委托任务不能为空", error_kind="invalid_input")
        task_id = current_tool_call_context().get("task_id", "")
        try:
            state = runtime._load_task(task_id) or {
                "task_id": task_id,
                "session_id": session_id,
                "user_message": task,
                "summary": {},
            }
        except Exception:
            state = {
                "task_id": task_id,
                "session_id": session_id,
                "user_message": task,
                "summary": {},
            }
        return await run_subagent(
            runtime,
            state,
            registry,
            task,
            [str(item) for item in (a.get("tools") or [])] or None,
            # 预算从 runtime 治理参数注入，子循环不再持有写死的常量
            max_rounds=getattr(runtime, "subagent_max_rounds", MAX_SUBAGENT_ROUNDS),
            max_tool_calls=getattr(runtime, "subagent_max_tool_calls", MAX_SUBAGENT_TOOL_CALLS),
        )

    add("spawn_subagent", "委托一个聚焦任务给子代理执行并返回其结论摘要。适合独立的只读调研/检索：给出明确目标与产出要求，子代理使用受限工具集并在预算内收敛", {
        "task": {"type": "string", "description": "委托任务：目标、需要的上下文、期望产出"},
        "tools": {"type": "array", "items": {"type": "string"}, "description": "工具白名单；缺省为只读工具集"},
    }, ["task"], ToolRisk.PROCESS, spawn_subagent)

    async def spawn_subagents(a):
        """并行扇出多个聚焦子代理，结论交回让主模型汇总（map-reduce 的 fan-out 侧）。"""
        from .subagent import (
            MAX_FANOUT_CONCURRENCY, MAX_SUBAGENT_ROUNDS, MAX_SUBAGENT_TOOL_CALLS,
            run_subagents,
        )
        from .tracing import current_tool_call_context

        runtime = _active_runtime
        if runtime is None:
            return ToolResult.fail("子代理运行时不可用", error_kind="tool_error")
        tasks = [t for t in (a.get("tasks") or [])]
        if not tasks:
            return ToolResult.fail("tasks 不能为空", error_kind="invalid_input")
        task_id = current_tool_call_context().get("task_id", "")
        try:
            state = runtime._load_task(task_id) or {
                "task_id": task_id,
                "session_id": session_id,
                "user_message": "并行子代理",
                "summary": {},
            }
        except Exception:
            state = {
                "task_id": task_id,
                "session_id": session_id,
                "user_message": "并行子代理",
                "summary": {},
            }
        return await run_subagents(
            runtime,
            state,
            registry,
            tasks,
            [str(item) for item in (a.get("tools") or [])] or None,
            concurrency=int(a.get("concurrency") or MAX_FANOUT_CONCURRENCY),
            max_rounds=getattr(runtime, "subagent_max_rounds", MAX_SUBAGENT_ROUNDS),
            max_tool_calls=getattr(runtime, "subagent_max_tool_calls", MAX_SUBAGENT_TOOL_CALLS),
        )

    add("spawn_subagents", "并行委托多个聚焦子代理（map-reduce 的 fan-out）：每个子代理独立调研并在预算内收敛，结论全部交回，由主模型综合成对主任务的答复。适合把一个复杂任务拆成互不依赖的子问题并行分析", {
        "tasks": {"type": "array", "items": {"type": "string"}, "description": "聚焦子任务列表，每个是一个独立可并行的调研目标"},
        "tools": {"type": "array", "items": {"type": "string"}, "description": "工具白名单；缺省为只读工具集"},
        "concurrency": {"type": "integer", "minimum": 1, "maximum": 4, "description": "并发上限，默认 4"},
    }, ["tasks"], ToolRisk.PROCESS, spawn_subagents)

    def http_request_batch(a):
        group_id = str(a.get("group_id") or f"batch-{uuid.uuid4().hex[:12]}")
        checks = []
        for index, request in enumerate(a.get("requests", [])[:32], start=1):
            method = str(request.get("method", "GET")).upper()
            url = str(request.get("url", ""))
            try:
                status, body, headers = services.network.request(
                    method,
                    url,
                    request.get("headers"),
                    request.get("json"),
                    request.get("timeout", 15),
                )
                expected_status = request.get("expected_status")
                success = (
                    status == int(expected_status)
                    if expected_status is not None
                    else 200 <= status < 400
                )
                checks.append({
                    "index": index,
                    "method": method,
                    "url": url,
                    "status": status,
                    "expected_status": expected_status,
                    "success": success,
                    "headers": headers,
                    "body": str(body)[:4000],
                    "error": None if success else f"HTTP 请求失败: {status}",
                })
            except (ValueError, urllib.error.URLError, TimeoutError, OSError) as exc:
                checks.append({
                    "index": index,
                    "method": method,
                    "url": url,
                    "status": None,
                    "expected_status": request.get("expected_status"),
                    "success": False,
                    "headers": {},
                    "body": "",
                    "error": str(exc),
                })
        passed = sum(bool(item["success"]) for item in checks)
        failed = len(checks) - passed
        data = {"group_id": group_id, "checks": checks, "total": len(checks), "passed": passed, "failed": failed}
        return ToolResult(
            success=failed == 0,
            data=data,
            output=json.dumps(data, ensure_ascii=False),
            error=None if failed == 0 else f"{failed} 个 HTTP 检查失败",
            error_kind=None if failed == 0 else "http_batch_failed",
        )

    add("http_request_batch", "一次顺序执行最多 32 个本机 HTTP 检查并返回每一步结果，适合端到端验收", {
        "group_id": {"type": "string", "description": "同一验收批次的稳定标识；复测时沿用返回的 group_id"},
        "requests": {
            "type": "array",
            "minItems": 1,
            "maxItems": 32,
            "items": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
                    "url": {"type": "string"},
                    "headers": {"type": "object"},
                    "json": {},
                    "timeout": {"type": "number", "minimum": 0.1, "maximum": 60, "description": "秒，最大 60"},
                    "expected_status": {"type": "integer", "minimum": 100, "maximum": 599},
                },
                "required": ["method", "url"],
                "additionalProperties": False,
            },
        },
    }, ["requests"], ToolRisk.READ, http_request_batch)
    add("wait_http", "等待本地 HTTP 服务开始响应", {
        "url": {"type": "string"},
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 60, "description": "秒，最大 60"},
        "expected_text": {"type": "string", "description": "响应正文必须包含的版本标记"},
        "process_id": {"type": "string", "description": "指定受管进程并核验端口归属"},
    }, ["url"], ToolRisk.READ,
        lambda a: services.network.wait_http(
            a["url"], a.get("timeout", 15), a.get("expected_text"),
            session_id=session_id, process_id=a.get("process_id"),
        ))
    return registry
