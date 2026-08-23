from pathlib import Path

from agent.runtime.tooling import LocalAgentServices, build_tool_registry, classify_command_risk


def test_core_registry_exposes_local_operation_tools(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", root)

    registry = build_tool_registry("session", services)
    names = {schema["name"] for schema in registry.schemas()}

    assert {
        "list_directory", "read_file", "search_text", "create_directory",
        "edit_files", "undo_last_change", "run_command", "clone_repository",
        "detect_project", "ensure_venv", "install_dependencies", "verify_project",
        "start_process", "get_process", "list_processes", "stop_process",
        "http_request", "http_request_batch",
    } <= names


def test_structured_http_request_passes_method_headers_and_json(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", root)
    registry = build_tool_registry("session", services)
    captured = {}

    def fake_request(method, url, headers, json_body, timeout):
        captured.update({
            "method": method, "url": url, "headers": headers,
            "json": json_body, "timeout": timeout,
        })
        return 201, '{"ok":true}', {"content-type": "application/json"}

    monkeypatch.setattr(services.network, "request", fake_request)
    result = registry.execute("http_request", {
        "method": "POST",
        "url": "http://127.0.0.1:8000/items",
        "headers": {"x-test": "yes"},
        "json": {"name": "demo"},
    })

    assert result.success is True
    assert captured["method"] == "POST"
    assert captured["json"] == {"name": "demo"}
    assert result.data["status"] == 201


def test_batch_http_request_runs_multiple_checks_and_preserves_each_result(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", root)
    registry = build_tool_registry("session", services)
    calls = []

    def fake_request(method, url, headers, json_body, timeout):
        calls.append((method, url, json_body))
        if url.endswith("/missing"):
            return 404, '{"detail":"missing"}', {"content-type": "application/json"}
        return 200, '{"ok":true}', {"content-type": "application/json"}

    monkeypatch.setattr(services.network, "request", fake_request)
    result = registry.execute("http_request_batch", {
        "requests": [
            {"method": "GET", "url": "http://127.0.0.1:8000/health"},
            {"method": "POST", "url": "http://127.0.0.1:8000/items", "json": {"name": "demo"}},
            {"method": "GET", "url": "http://127.0.0.1:8000/missing", "expected_status": 404},
        ],
    })

    assert result.success is True
    assert calls == [
        ("GET", "http://127.0.0.1:8000/health", None),
        ("POST", "http://127.0.0.1:8000/items", {"name": "demo"}),
        ("GET", "http://127.0.0.1:8000/missing", None),
    ]
    assert result.data["total"] == 3
    assert result.data["passed"] == 3
    assert result.data["failed"] == 0
    assert [item["status"] for item in result.data["checks"]] == [200, 200, 404]


def test_batch_http_request_keeps_explicit_group_id_for_recovery_lineage(tmp_path: Path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", root)
    registry = build_tool_registry("session", services)
    monkeypatch.setattr(
        services.network,
        "request",
        lambda method, url, headers, json_body, timeout: (200, "ok", {}),
    )

    result = registry.execute("http_request_batch", {
        "group_id": "acceptance-books-v2",
        "requests": [{"method": "GET", "url": "http://127.0.0.1:8000/health"}],
    })

    assert result.success is True
    assert result.data["group_id"] == "acceptance-books-v2"


def test_core_registry_runs_workspace_file_tool(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", root)
    registry = build_tool_registry("session", services)

    result = registry.execute("create_directory", {"path": "src"})

    assert result.success is True
    assert (root / "src").is_dir()


def test_dangerous_command_is_upgraded_to_confirmation(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    services = LocalAgentServices.create()
    services.workspaces.bind("session", root)
    registry = build_tool_registry("session", services)

    result = registry.execute("run_command", {"command": "Remove-Item -Recurse src"})

    assert result.requires_confirmation is True
    assert classify_command_risk({"command": "git push origin main"}).value == "external"


def test_powershell_format_table_is_not_treated_as_disk_format():
    assert classify_command_risk({
        "command": "Get-ChildItem | Format-Table -AutoSize",
    }).value == "process"


def test_disk_format_command_still_requires_confirmation():
    assert classify_command_risk({"command": "format D:"}).value == "destructive"
