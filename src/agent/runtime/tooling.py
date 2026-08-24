import json
import re
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


_DESTRUCTIVE_COMMANDS = re.compile(
    r"\b(Remove-Item|rm|del|rmdir|mkfs|diskpart)\b|"
    r"^\s*format(?:\.com)?(?:\s|$)|git\s+(reset\s+--hard|clean\s+-[a-z]*f)",
    re.IGNORECASE,
)
_PRIVILEGED_COMMANDS = re.compile(
    r"\b(sudo|runas)\b|Start-Process.+-Verb\s+RunAs|\b(winget|choco)\s+install\b",
    re.IGNORECASE,
)
_EXTERNAL_COMMANDS = re.compile(
    r"\bgit\s+push\b|\bgh\s+(repo\s+create|pr\s+create|issue\s+create|release\s+create)\b|\bnpm\s+publish\b",
    re.IGNORECASE,
)

# 边界拦截（ToolRisk.BOUNDARY）：评测完整性 + 全局环境污染。
# - 受限路径引用：判分脚本目录（checks/）与评测结果文件（results-*.jsonl）。
#   语义对齐 SWE-bench「测试对 agent 物理隐藏」——agent 在非完全访问档下不可触碰；
# - 全局工具链/系统级写入：setx（注册表持久化环境变量）、reg add、npm/pnpm/yarn -g、
#   go install（写全局 GOBIN）。这些会跨任务污染本机环境。
_BOUNDARY_REFERENCE_RE = re.compile(
    r"(?:^|[\\/])(?:checks[\\/]|results-[A-Za-z0-9_.-]*\.jsonl)",
    re.IGNORECASE,
)
_GLOBAL_WRITE_RE = re.compile(
    r"\bsetx\b|\breg\s+add\b|\b(?:npm|pnpm)\s+(?:install|i|add)\s+(?:-g\b|--global\b)|"
    r"\byarn\s+global\s+add\b|\bgo\s+install\b",
    re.IGNORECASE,
)


def boundary_violation(command: str) -> str | None:
    """检测命令是否引用工作区外受限路径或做全局工具链写入。返回违规原因或 None。"""
    if _BOUNDARY_REFERENCE_RE.search(command):
        return "命令引用受限路径（判分脚本/评测结果目录）"
    if _GLOBAL_WRITE_RE.search(command):
        return "命令执行全局工具链/系统级写入（如 setx、npm install -g、go install）"
    return None


def classify_command_risk(args: dict) -> ToolRisk:
    command = str(args.get("command", ""))
    if boundary_violation(command):
        return ToolRisk.BOUNDARY
    if _DESTRUCTIVE_COMMANDS.search(command):
        return ToolRisk.DESTRUCTIVE
    if _PRIVILEGED_COMMANDS.search(command):
        return ToolRisk.PRIVILEGED
    if _EXTERNAL_COMMANDS.search(command):
        return ToolRisk.EXTERNAL
    return ToolRisk.PROCESS


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
