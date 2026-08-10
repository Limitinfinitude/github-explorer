import os
import platform
import re
import signal
import subprocess
import sys
from pathlib import Path

from .models import ToolResult
from .workspace import WorkspaceManager


def shell_args(command: str) -> list[str]:
    if platform.system() == "Windows":
        wrapped = (
            "& { " + command + "; "
            "if (-not $?) { "
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }; exit 1 }; "
            "if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } "
            "}"
        )
        return ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped]
    return ["bash", "-c", command]


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
        return candidate if candidate.is_file() else Path(sys.executable)

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

        process = subprocess.Popen(
            shell_args(command),
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
                data={"returncode": None, "cwd": str(work_dir)},
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
