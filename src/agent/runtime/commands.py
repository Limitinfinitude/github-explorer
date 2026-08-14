import os
import platform
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .models import ToolResult
from .workspace import WorkspaceManager


@dataclass(frozen=True)
class ShellCommandPlan:
    original_command: str
    command: str
    shell: str
    args: list[str]
    error: str | None = None
    suggestion: str | None = None


_BARE_CURL_RE = re.compile(r"(?<![\w.])curl(?=\s)", re.IGNORECASE)
_BASH_COMMAND_RE = re.compile(
    r"^\s*(?:bash|sh)(?:\.exe)?\s+-c\b|"
    r"(^|[;&|]\s*)(test\s+-[def]|for\s+\w+\s+in\b)|"
    r"\bdo\s+.*\bdone\b|\bfind\s+[^\r\n;]+\s+-maxdepth\b|/(?:c|d|e)/Program Files",
    re.IGNORECASE,
)
_CMD_COMMAND_RE = re.compile(
    r"^\s*(?:if\s+(?:not\s+)?exist\b|set\s+[A-Za-z_][\w]*=)|&&|\|\|",
    re.IGNORECASE,
)
_CURL_INLINE_JSON_RE = re.compile(
    r"\bcurl(?:\.exe)?\b[^\r\n]*(?:-d|--data|--data-raw)\s+"
    r"(?:'\s*\{|\"\s*\{|\"\{\\\")",
    re.IGNORECASE,
)


def plan_shell_command(command: str) -> ShellCommandPlan:
    if platform.system() != "Windows":
        return ShellCommandPlan(command, command, "bash", ["bash", "-c", command])

    if _BASH_COMMAND_RE.search(command):
        return ShellCommandPlan(
            command,
            command,
            "unsupported",
            [],
            error="检测到 Bash 语法，但当前 Windows 执行环境只支持 PowerShell 或 CMD。",
            suggestion=(
                "请改用 PowerShell，例如用 Test-Path 检查路径、Get-ChildItem 替代 ls/find，"
                "并用 foreach 替代 Bash for/do/done。"
            ),
        )
    if _CURL_INLINE_JSON_RE.search(command):
        return ShellCommandPlan(
            command,
            command,
            "unsupported",
            [],
            error="检测到 Windows 下不可靠的 curl 内联 JSON 载荷。",
            suggestion="请先将 JSON 写入文件，再使用 curl.exe --data-binary \"@body.json\"。",
        )

    normalized = _BARE_CURL_RE.sub("curl.exe", command)
    if _CMD_COMMAND_RE.search(normalized):
        return ShellCommandPlan(
            command,
            normalized,
            "cmd",
            ["cmd.exe", "/d", "/s", "/c", normalized],
        )

    wrapped = (
            "& { " + normalized + "; "
            "if (-not $?) { "
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }; exit 1 }; "
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } "
            "}"
        )
    return ShellCommandPlan(
        command,
        normalized,
        "powershell",
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
    )


def shell_args(command: str) -> list[str]:
    plan = plan_shell_command(command)
    if plan.error:
        raise ValueError(plan.error)
    return plan.args


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("CONDA") and key not in {"SSL_CERT_FILE", "SSL_CERT_DIR"}
    }


def process_creation_flags() -> int:
    if platform.system() != "Windows":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


_PYTHON_COMMAND_RE = re.compile(
    r'^\s*(?:&\s*)?(?:"([^"]*python(?:\.exe)?)"|\'([^\']*python(?:\.exe)?)\'|([^\s;&|]*python(?:\.exe)?))(?=\s|$)',
    re.IGNORECASE,
)


def command_python_executable(command: str, cwd: Path) -> str | None:
    match = _PYTHON_COMMAND_RE.match(command)
    if not match:
        return None
    value = next((part for part in match.groups() if part), "")
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return str(candidate.resolve())


def terminate_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return


class CommandRunner:
    _DIRECTORY_CHANGE_RE = re.compile(
        r"^\s*(?:cd|Set-Location)\s+(?:\"([^\"]+)\"|'([^']+)'|([^;&|]+?))\s*$",
        re.IGNORECASE,
    )

    def __init__(self, workspaces: WorkspaceManager, on_current_path_change=None) -> None:
        self.workspaces = workspaces
        self.on_current_path_change = on_current_path_change

    def project_python(self, session_id: str, path: str = ".") -> Path:
        root = self.workspaces.resolve(session_id, path)
        relative = Path("Scripts/python.exe") if platform.system() == "Windows" else Path("bin/python")
        candidate = root / ".venv" / relative
        if not candidate.is_file():
            raise FileNotFoundError(
                f"项目虚拟环境不存在: {candidate}；请先对 {root} 执行 ensure_venv"
            )
        return candidate

    def run(
        self,
        session_id: str,
        command: str,
        cwd: str = ".",
        timeout: float = 60,
    ) -> ToolResult:
        work_dir = self.workspaces.resolve(session_id, cwd)
        if not work_dir.is_dir():
            return ToolResult.fail(f"命令目录不存在: {cwd}")

        plan = plan_shell_command(command)
        if plan.error:
            return ToolResult.fail(
                plan.error,
                data={
                    "returncode": None,
                    "cwd": str(work_dir),
                    "shell": plan.shell,
                    "original_command": plan.original_command,
                    "executed_command": None,
                    "suggestion": plan.suggestion,
                },
            )

        process = subprocess.Popen(
            plan.args,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=clean_environment(),
            creationflags=process_creation_flags(),
            start_new_session=platform.system() != "Windows",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate_process_tree(process)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            return ToolResult.fail(
                f"命令执行超时（{timeout} 秒）",
                data={
                    "returncode": None,
                    "cwd": str(work_dir),
                    "python_executable": command_python_executable(plan.command, work_dir),
                    "shell": plan.shell,
                    "original_command": plan.original_command,
                    "executed_command": plan.command,
                },
            )

        output = stdout.rstrip()
        if stderr.strip():
            output = f"{output}\n{stderr.rstrip()}".strip()
        result = ToolResult(
            success=process.returncode == 0,
            data={
                "returncode": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "cwd": str(work_dir),
                "python_executable": command_python_executable(plan.command, work_dir),
                "shell": plan.shell,
                "original_command": plan.original_command,
                "executed_command": plan.command,
            },
            output=output,
            error=None if process.returncode == 0 else f"命令退出码: {process.returncode}",
        )
        match = self._DIRECTORY_CHANGE_RE.fullmatch(command)
        if result.success and match:
            target = next(value for value in match.groups() if value is not None).strip()
            current_path = self.workspaces.set_current_path(session_id, work_dir / target)
            result.data["cwd"] = str(current_path)
            if self.on_current_path_change is not None:
                self.on_current_path_change(session_id, current_path)
        return result
