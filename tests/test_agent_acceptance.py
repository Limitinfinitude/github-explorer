import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from agent.runtime.acceptance import WorkProductEvaluator
from agent.runtime.project_tools import command_executable
from agent.runtime.tooling import LocalAgentServices, build_tool_registry


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_local_agent_toolchain_creates_verifies_runs_and_stops_project(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", workspace)
    registry = build_tool_registry("session", services)

    assert registry.execute("create_directory", {"path": "demo"}).success
    edited = registry.execute("edit_files", {"edits": [
        {"path": "demo/requirements.txt", "operation": "write", "content": ""},
        {"path": "demo/app.py", "operation": "write", "content": "print('demo')\n"},
    ]})
    assert edited.success
    assert registry.execute("ensure_venv", {"path": "demo"}).success
    assert registry.execute("install_dependencies", {"path": "demo"}).success
    assert registry.execute("verify_project", {"path": "demo"}).success

    port = free_port()
    python = workspace / "demo" / ".venv" / "Scripts" / "python.exe"
    started = registry.execute("start_process", {
        "cwd": "demo",
        "command": f"{command_executable(python)} -m http.server {port} --bind 127.0.0.1",
    })
    assert started.success

    try:
        ready = registry.execute("wait_http", {
            "url": f"http://127.0.0.1:{port}/",
            "timeout": 10,
            "process_id": started.process_id,
        })
        assert ready.success
    finally:
        stopped = registry.execute("stop_process", {"process_id": started.process_id})

    assert stopped.success


def test_wait_http_rejects_listener_not_owned_by_managed_process(tmp_path: Path, monkeypatch):
    services = LocalAgentServices.create()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    services.workspaces.bind("session", workspace)
    registry = build_tool_registry("session", services)
    started = registry.execute("start_process", {
        "command": "python -c \"import time; time.sleep(30)\"",
    })
    monkeypatch.setattr(services.network, "_request", lambda url, timeout: (200, "ready"))
    monkeypatch.setattr(services.processes, "listener_ownership", lambda session_id, process_id, host, port: {
        "owned": False, "listener_pids": [99999], "process_tree_pids": [started.data["launcher_pid"]],
    })

    try:
        result = registry.execute("wait_http", {
            "url": "http://127.0.0.1:8123/",
            "timeout": 0.2,
            "process_id": started.process_id,
        })
    finally:
        registry.execute("stop_process", {"process_id": started.process_id})

    assert result.success is False
    assert result.error_kind == "process_mismatch"
    assert result.data["listener_pids"] == [99999]


def test_wait_http_rejects_a_stale_service_without_expected_content(tmp_path: Path):
    class StaleHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = "old page".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    server = HTTPServer(("127.0.0.1", 0), StaleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    services = LocalAgentServices.create()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    services.workspaces.bind("session", workspace)
    registry = build_tool_registry("session", services)

    try:
        result = registry.execute("wait_http", {
            "url": f"http://127.0.0.1:{server.server_port}/",
            "timeout": 0.3,
            "expected_text": "new page marker",
        })
    finally:
        server.shutdown()
        server.server_close()

    assert result.success is False
    assert "期望文本" in result.error


def test_file_evidence_accepts_windows_absolute_path_inside_workspace():
    evaluation = WorkProductEvaluator().evaluate(
        criteria=[{"id": 1, "text": "提供课程任务页面"}],
        response_text=(
            r"1. [完成] 页面已创建。"
            r"[证据:file:E:\Explorer验收\课程看板\templates\index.html]"
        ),
        summary={
            "workspace_root": r"E:\Explorer验收\课程看板",
            "changed_files": ["templates/index.html"],
            "verification": [],
            "processes": [],
        },
    )

    item = evaluation["requirement_coverage"]["items"][0]
    assert item["status"] == "passed"
    assert item["evidence"][0]["valid"] is True


def test_model_claimed_npm_test_is_invalid_when_package_script_is_missing(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")

    evaluation = WorkProductEvaluator().evaluate(
        criteria=[], response_text="", summary={
            "workspace_root": str(workspace),
            "verification": [{"kind": "unit", "command": "npm test", "success": True}],
        },
    )

    assert evaluation["technical_verification"]["status"] == "failed"
    assert evaluation["technical_verification"]["failed"] == 1
    assert evaluation["technical_verification"]["checks"][0]["evidence_status"] == "invalid"
