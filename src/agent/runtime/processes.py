from __future__ import annotations

import platform
import time
import hashlib
import socket
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from .commands import (
    clean_environment, command_python_executable, process_creation_flags,
    plan_shell_command, terminate_process_tree,
)
from .models import ToolResult
from .workspace import WorkspaceManager


@dataclass
class ManagedProcess:
    process_id: str
    session_id: str
    command: str
    original_command: str
    shell: str
    cwd: str
    process: subprocess.Popen
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=1000))
    status: str = "running"
    command_fingerprint: str = ""
    declared_host: str | None = None
    declared_port: int | None = None


class ProcessManager:
    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()

    def start(
        self,
        session_id: str,
        command: str,
        cwd: str = ".",
        *,
        host: str | None = None,
        port: int | None = None,
    ) -> ToolResult:
        work_dir = self.workspaces.resolve(session_id, cwd)
        if not work_dir.is_dir():
            return ToolResult.fail(f"进程目录不存在: {cwd}")
        if host is not None and host not in {"127.0.0.1", "::1"}:
            return ToolResult.fail(
                f"后台服务只允许监听本机回环地址: {host}",
                error_kind="invalid_host",
                data={"host": host, "allowed_hosts": ["127.0.0.1", "::1"]},
            )
        if port is not None:
            host = host or "127.0.0.1"
            if self._port_is_open(host, port):
                return ToolResult.fail(
                    f"端口已被占用: {host}:{port}",
                    error_kind="port_conflict",
                    data={"host": host, "port": port},
                )

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
            command=plan.command,
            original_command=plan.original_command,
            shell=plan.shell,
            cwd=str(work_dir),
            process=process,
            command_fingerprint=hashlib.sha256(
                f"{plan.shell}\0{plan.command}\0{work_dir}".encode("utf-8")
            ).hexdigest(),
            declared_host=host,
            declared_port=port,
        )
        with self._lock:
            self._processes[process_id] = managed
        threading.Thread(target=self._capture_output, args=(managed,), daemon=True).start()
        # 早期健康检查：服务类命令最常见的失败是"启动即退"（解释器路径
        # 错、缺环境变量、端口占用后崩溃）。等 2 秒把早期输出收进结果，
        # 让模型第一轮就看到进程死活与报错，而不是 wait_http 干等超时。
        import time as _time
        time.sleep(2.0)
        exited = managed.process.poll()
        early_logs = list(managed.logs)
        early_output = "\n".join(early_logs[-30:])
        if exited is not None:
            with self._lock:
                managed.status = "exited"
            return ToolResult.fail(
                f"进程启动后立即退出（退出码 {exited}）。早期输出：\n{early_output or '(无输出)'}",
                error_kind="process_exited",
                process_id=process_id,
                data={
                    "pid": process.pid,
                    "returncode": exited,
                    "cwd": str(work_dir),
                    "status": "exited",
                    "early_output": early_output,
                    "shell": plan.shell,
                    "original_command": plan.original_command,
                    "executed_command": plan.command,
                    "declared_host": host,
                    "declared_port": port,
                },
            )
        warning = ""
        lowered = early_output.lower()
        if "traceback" in lowered or "no module named" in lowered or "error" in lowered:
            warning = (
                "\n注意：进程仍在运行，但早期输出包含错误信息，"
                "建议先检查上面的输出再继续验收。"
            )
        return ToolResult.ok(
            output=(
                f"后台进程已启动: {process_id}（已确认 2 秒后仍在运行）"
                f"\n早期输出：\n{early_output or '(暂无输出)'}{warning}"
            ),
            process_id=process_id,
            data={
                "pid": process.pid,
                "launcher_pid": process.pid,
                "process_tree_pids": self._process_tree_pids(process.pid),
                "cwd": str(work_dir),
                "status": "running",
                "early_output": early_output,
                "python_executable": command_python_executable(plan.command, work_dir),
                "shell": plan.shell,
                "original_command": plan.original_command,
                "executed_command": plan.command,
                "command_fingerprint": managed.command_fingerprint,
                "declared_host": host,
                "declared_port": port,
            },
        )

    def listener_ownership(
        self,
        session_id: str,
        process_id: str,
        host: str,
        port: int,
    ) -> dict:
        managed = self._owned_process(session_id, process_id)
        if isinstance(managed, ToolResult):
            return {
                "owned": False,
                "listener_pids": [],
                "process_tree_pids": [],
                "error_kind": "process_not_found",
            }
        tree_pids = self._process_tree_pids(managed.process.pid)
        listener_pids: list[int] = []
        try:
            for connection in psutil.net_connections(kind="tcp"):
                if connection.status != psutil.CONN_LISTEN or not connection.laddr:
                    continue
                if connection.laddr.port != port:
                    continue
                if host == "127.0.0.1" and connection.laddr.ip not in {"127.0.0.1", "0.0.0.0"}:
                    continue
                if host == "::1" and connection.laddr.ip not in {"::1", "::"}:
                    continue
                if connection.pid is not None:
                    listener_pids.append(connection.pid)
        except (psutil.AccessDenied, OSError) as exc:
            return {
                "owned": False,
                "listener_pids": [],
                "process_tree_pids": tree_pids,
                "error_kind": "listener_inspection_failed",
                "detail": str(exc),
            }
        listener_pids = sorted(set(listener_pids))
        return {
            "owned": bool(set(listener_pids) & set(tree_pids)),
            "listener_pids": listener_pids,
            "process_tree_pids": tree_pids,
        }

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
            "launcher_pid": managed.process.pid,
            "process_tree_pids": self._process_tree_pids(managed.process.pid),
            "command": managed.command,
            "original_command": managed.original_command,
            "executed_command": managed.command,
            "shell": managed.shell,
            "cwd": managed.cwd,
            "python_executable": command_python_executable(managed.command, Path(managed.cwd)),
            "status": managed.status,
            "returncode": returncode,
            "logs": "".join(managed.logs),
            "command_fingerprint": managed.command_fingerprint,
            "declared_host": managed.declared_host,
            "declared_port": managed.declared_port,
        }

    @staticmethod
    def _process_tree_pids(launcher_pid: int) -> list[int]:
        pids = [launcher_pid]
        try:
            pids.extend(child.pid for child in psutil.Process(launcher_pid).children(recursive=True))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return sorted(set(pids))

    @staticmethod
    def _port_is_open(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return True
        except OSError:
            return False

    @staticmethod
    def _capture_output(managed: ManagedProcess) -> None:
        if managed.process.stdout is None:
            return
        for line in managed.process.stdout:
            managed.logs.append(line)
