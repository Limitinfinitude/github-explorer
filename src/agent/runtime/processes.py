import platform
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field

from .commands import clean_environment, process_creation_flags, shell_args, terminate_process_tree
from .models import ToolResult
from .workspace import WorkspaceManager


@dataclass
class ManagedProcess:
    process_id: str
    session_id: str
    command: str
    cwd: str
    process: subprocess.Popen
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    status: str = "running"


class ProcessManager:
    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()

    def start(self, session_id: str, command: str, cwd: str = ".") -> ToolResult:
        work_dir = self.workspaces.resolve(session_id, cwd)
        if not work_dir.is_dir():
            return ToolResult.fail(f"进程目录不存在: {cwd}")

        process = subprocess.Popen(
            shell_args(command),
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=clean_environment(),
            creationflags=process_creation_flags(),
            start_new_session=platform.system() != "Windows",
        )
        process_id = uuid.uuid4().hex
        managed = ManagedProcess(
            process_id=process_id,
            session_id=session_id,
            command=command,
            cwd=str(work_dir),
            process=process,
        )
        with self._lock:
            self._processes[process_id] = managed
        threading.Thread(target=self._capture_output, args=(managed,), daemon=True).start()
        return ToolResult.ok(
            output=f"后台进程已启动: {process_id}",
            process_id=process_id,
            data={"pid": process.pid, "cwd": str(work_dir), "status": "running"},
        )

    def get(self, session_id: str, process_id: str) -> ToolResult:
        managed = self._owned_process(session_id, process_id)
        if isinstance(managed, ToolResult):
            return managed
        return ToolResult.ok(data=self._snapshot(managed), output="".join(managed.logs), process_id=process_id)

    def list(self, session_id: str) -> ToolResult:
        with self._lock:
            processes = [item for item in self._processes.values() if item.session_id == session_id]
        return ToolResult.ok(data={"processes": [self._snapshot(item) for item in processes]})

    def stop(self, session_id: str, process_id: str) -> ToolResult:
        managed = self._owned_process(session_id, process_id)
        if isinstance(managed, ToolResult):
            return managed
        terminate_process_tree(managed.process)
        try:
            managed.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            managed.process.kill()
            managed.process.wait(timeout=5)
        managed.status = "stopped"
        return ToolResult.ok(output=f"已停止后台进程: {process_id}", process_id=process_id)

    def _owned_process(self, session_id: str, process_id: str) -> ManagedProcess | ToolResult:
        with self._lock:
            managed = self._processes.get(process_id)
        if managed is None or managed.session_id != session_id:
            return ToolResult.fail(f"后台进程不存在: {process_id}")
        return managed

    def _snapshot(self, managed: ManagedProcess) -> dict:
        returncode = managed.process.poll()
        if returncode is not None and managed.status == "running":
            managed.status = "exited"
        return {
            "process_id": managed.process_id,
            "pid": managed.process.pid,
            "command": managed.command,
            "cwd": managed.cwd,
            "status": managed.status,
            "returncode": returncode,
            "logs": "".join(managed.logs),
        }

    @staticmethod
    def _capture_output(managed: ManagedProcess) -> None:
        if managed.process.stdout is None:
            return
        for line in managed.process.stdout:
            managed.logs.append(line)
