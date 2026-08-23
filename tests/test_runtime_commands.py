import sys
import time
from pathlib import Path

from agent.runtime.commands import CommandRunner, plan_shell_command
from agent.runtime.processes import ProcessManager
from agent.runtime.workspace import WorkspaceManager


def make_runtime(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    manager = WorkspaceManager()
    manager.bind("session", root)
    return root, CommandRunner(manager), ProcessManager(manager)


def python_command(code: str) -> str:
    escaped = code.replace('"', '\\"')
    return f'& "{sys.executable}" -c "{escaped}"'


def test_command_preserves_nonzero_exit_code(tmp_path: Path):
    _, runner, _ = make_runtime(tmp_path)

    result = runner.run("session", python_command("import sys; print('out'); sys.exit(3)"))

    assert result.success is False
    assert result.data["returncode"] == 3
    assert "out" in result.output
    assert result.data["python_executable"] == str(Path(sys.executable).resolve())


def test_command_timeout_is_failure(tmp_path: Path):
    _, runner, _ = make_runtime(tmp_path)

    result = runner.run("session", python_command("import time; time.sleep(2)"), timeout=0.1)

    assert result.success is False
    assert "超时" in result.error
    assert result.data["shell"] == "powershell" if sys.platform == "win32" else "bash"
    assert result.data["original_command"]
    assert result.data["executed_command"]


def test_windows_command_plan_routes_cmd_syntax(monkeypatch):
    monkeypatch.setattr("agent.runtime.commands.platform.system", lambda: "Windows")

    plan = plan_shell_command("if exist .venv (echo VENV_EXISTS) else (echo NO_VENV)")

    assert plan.error is None
    assert plan.shell == "cmd"
    assert plan.args[:4] == ["cmd.exe", "/d", "/s", "/c"]


def test_windows_command_plan_normalizes_bare_curl(monkeypatch):
    monkeypatch.setattr("agent.runtime.commands.platform.system", lambda: "Windows")

    plan = plan_shell_command('curl -s -w "HTTP %{http_code}" http://127.0.0.1:5000/')

    assert plan.error is None
    assert plan.shell == "powershell"
    assert plan.command.startswith("curl.exe ")


def test_windows_command_plan_rejects_bash_syntax(monkeypatch):
    monkeypatch.setattr("agent.runtime.commands.platform.system", lambda: "Windows")

    plan = plan_shell_command('test -d node_modules && echo "exists"')

    assert plan.args == []
    assert "Bash" in plan.error
    assert "Test-Path" in plan.suggestion


def test_windows_command_plan_rejects_explicit_bash_c(monkeypatch):
    monkeypatch.setattr("agent.runtime.commands.platform.system", lambda: "Windows")

    plan = plan_shell_command("bash -c 'pwd && ls -la'")

    assert plan.args == []
    assert "Bash" in plan.error
    assert "PowerShell" in plan.error


def test_windows_command_plan_rejects_unix_ls_chain(monkeypatch):
    monkeypatch.setattr("agent.runtime.commands.platform.system", lambda: "Windows")

    plan = plan_shell_command('ls -la && find . -maxdepth 2 -name "pyvenv.cfg" 2>/dev/null')

    assert plan.args == []
    assert "Bash" in plan.error
    assert "Get-ChildItem" in plan.suggestion


def test_windows_command_plan_rejects_inline_curl_json(monkeypatch):
    monkeypatch.setattr("agent.runtime.commands.platform.system", lambda: "Windows")

    plan = plan_shell_command(
        "curl.exe -X POST -H \"Content-Type: application/json\" "
        "-d '{\"content\":\"hello\"}' http://127.0.0.1:8000/notes"
    )

    assert plan.args == []
    assert "JSON" in plan.error
    assert "--data-binary" in plan.suggestion


def test_command_runner_executes_cmd_chain_and_records_shell(tmp_path: Path, monkeypatch):
    if sys.platform != "win32":
        return
    _, runner, _ = make_runtime(tmp_path)

    result = runner.run("session", "if exist . (echo WORKSPACE_EXISTS) else (exit /b 7)")

    assert result.success is True
    assert "WORKSPACE_EXISTS" in result.output
    assert result.data["shell"] == "cmd"
    assert result.data["original_command"].startswith("if exist")
    assert result.data["executed_command"] == result.data["original_command"]


def test_command_runner_rejects_unsupported_shell_before_execution(tmp_path: Path):
    _, runner, _ = make_runtime(tmp_path)

    result = runner.run("session", "for p in one two; do echo $p; done")

    assert result.success is False
    assert result.data["shell"] == "unsupported"
    assert result.data["returncode"] is None
    assert "Bash" in result.error


def test_project_python_prefers_workspace_venv(tmp_path: Path):
    root, runner, _ = make_runtime(tmp_path)
    expected = root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    expected.parent.mkdir(parents=True)
    expected.touch()

    assert runner.project_python("session") == expected


def test_run_uses_current_path_and_persists_explicit_cd(tmp_path: Path):
    root = tmp_path / "project"
    child = root / "child"
    child.mkdir(parents=True)
    manager = WorkspaceManager()
    manager.bind("session", root)
    seen = []
    runner = CommandRunner(manager, on_current_path_change=lambda sid, path: seen.append((sid, path)))

    result = runner.run("session", "cd child")

    assert result.success is True
    assert manager.current_path("session") == child.resolve()
    assert seen == [("session", child.resolve())]


def test_run_does_not_change_current_path_for_mkdir_or_compound_cd(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    manager = WorkspaceManager()
    manager.bind("session", root)
    runner = CommandRunner(manager)

    assert runner.run("session", "mkdir child").success is True
    assert manager.current_path("session") == root.resolve()
    assert runner.run("session", "cd child; Get-Location").success is True
    assert manager.current_path("session") == root.resolve()


def test_background_process_can_stream_logs_and_stop(tmp_path: Path):
    _, _, processes = make_runtime(tmp_path)
    started = processes.start(
        "session",
        python_command("import time; print('ready', flush=True); time.sleep(30)"),
    )
    assert started.success is True
    assert started.data["python_executable"] == str(Path(sys.executable).resolve())
    process_id = started.process_id

    try:
        deadline = time.time() + 5
        logs = ""
        while time.time() < deadline:
            status = processes.get("session", process_id)
            logs = status.data["logs"]
            if "ready" in logs:
                break
            time.sleep(0.05)

        assert "ready" in logs
        assert status.data["status"] == "running"
    finally:
        stopped = processes.stop("session", process_id)

    assert stopped.success is True
    assert processes.get("session", process_id).data["status"] == "stopped"


def test_background_process_snapshot_preserves_shell_evidence(tmp_path: Path):
    _, _, processes = make_runtime(tmp_path)
    started = processes.start(
        "session",
        python_command("import time; print('ready', flush=True); time.sleep(30)"),
    )

    try:
        snapshot = processes.get("session", started.process_id)
        assert snapshot.data["shell"] == "powershell" if sys.platform == "win32" else "bash"
        assert snapshot.data["original_command"] == started.data["original_command"]
        assert snapshot.data["executed_command"] == started.data["executed_command"]
    finally:
        processes.stop("session", started.process_id)


def test_background_process_snapshot_has_stable_identity_and_process_tree(tmp_path: Path):
    _, _, processes = make_runtime(tmp_path)
    started = processes.start(
        "session",
        python_command("import time; time.sleep(30)"),
        host="127.0.0.1",
        port=54321,
    )

    try:
        snapshot = processes.get("session", started.process_id).data
        assert snapshot["launcher_pid"] == started.data["launcher_pid"]
        assert snapshot["launcher_pid"] in snapshot["process_tree_pids"]
        assert len(snapshot["command_fingerprint"]) == 64
        assert snapshot["declared_host"] == "127.0.0.1"
        assert snapshot["declared_port"] == 54321
    finally:
        processes.stop("session", started.process_id)


def test_start_process_rejects_non_loopback_and_port_conflict(tmp_path: Path, monkeypatch):
    _, _, processes = make_runtime(tmp_path)

    external = processes.start("session", python_command("print('no')"), host="0.0.0.0", port=8123)
    monkeypatch.setattr(processes, "_port_is_open", lambda host, port: True)
    conflict = processes.start("session", python_command("print('no')"), host="127.0.0.1", port=8123)

    assert external.error_kind == "invalid_host"
    assert conflict.error_kind == "port_conflict"
    assert conflict.data["port"] == 8123


def test_background_process_rejects_unsupported_shell_before_start(tmp_path: Path):
    _, _, processes = make_runtime(tmp_path)

    result = processes.start("session", "for p in one two; do echo $p; done")

    assert result.success is False
    assert result.process_id is None
    assert result.data["shell"] == "unsupported"
    assert result.data["returncode"] is None
