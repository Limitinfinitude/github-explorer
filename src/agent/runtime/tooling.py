import re
import urllib.error
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


def classify_command_risk(args: dict) -> ToolRisk:
    command = str(args.get("command", ""))
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
    add("search_text", "在工作区文本文件中搜索精确文本", {
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
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 600},
    }, ["command"], ToolRisk.PROCESS, lambda a: services.commands.run(
        session_id, a["command"], a.get("cwd", "."), a.get("timeout", 60)
    ), classify_command_risk)
    add("clone_repository", "克隆远程 Git 仓库到工作区内目录", {
        "url": {"type": "string"}, "destination": path_property,
    }, ["url", "destination"], ToolRisk.WRITE_SAFE,
        lambda a: services.projects.clone_repository(session_id, a["url"], a["destination"]))
    add("detect_project", "检测 Python/Node 项目、包管理器和可用脚本", {"path": path_property}, [], ToolRisk.READ,
        lambda a: services.projects.detect(session_id, a.get("path", ".")))
    add("ensure_venv", "为 Python 项目创建或复用工作区 .venv", {"path": path_property}, [], ToolRisk.PROCESS,
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
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 10},
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

    add("http_request", "向本机 HTTP 服务发送结构化请求", {
        "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]},
        "url": {"type": "string"},
        "headers": {"type": "object"},
        "json": {},
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 60},
    }, ["method", "url"], ToolRisk.READ, http_request)
    add("wait_http", "等待本地 HTTP 服务开始响应", {
        "url": {"type": "string"},
        "timeout": {"type": "number", "minimum": 0.1, "maximum": 60},
        "expected_text": {"type": "string", "description": "响应正文必须包含的版本标记"},
        "process_id": {"type": "string", "description": "指定受管进程并核验端口归属"},
    }, ["url"], ToolRisk.READ,
        lambda a: services.network.wait_http(
            a["url"], a.get("timeout", 15), a.get("expected_text"),
            session_id=session_id, process_id=a.get("process_id"),
        ))
    return registry
