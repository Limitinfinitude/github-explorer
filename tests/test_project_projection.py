import pytest

from agent.runtime.project_projection import (
    _failure_patterns, _first_error_line,
    build_project_evidence, build_project_overview, build_project_report_markdown,
    build_project_summary,
    project_action_prompt, project_id_for_workspace, project_session_id_for_workspace,
)


def test_project_overview_preserves_failed_status_and_developer_counts():
    result = build_project_overview(
        project_id="task-1",
        workspace={"root": "C:/workspace", "current_path": "C:/workspace/app"},
        task={
            "task_id": "task-1",
            "session_id": "session-1",
            "user_message": "启动服务并验证端口",
            "status": "incomplete",
            "summary": {
                "verification": [{"success": False}],
                "processes": [{"process_id": "p1", "status": "exited"}],
            },
        },
        activity={
            "events": [{"type": "task_failed"}],
            "tool_runs": [{"tool_name": "start_process"}],
            "changesets": [{"files": ["app.py"], "diff": ""}],
            "artifacts": [],
        },
    )

    assert result["stage"] == "run"
    assert result["stage_status"] == "incomplete"
    assert result["summary"]["failed"] is True
    assert result["next_action"] == "查看失败原因并重试"
    assert result["evidence_counts"] == {"events": 1, "tool_runs": 1, "changesets": 1, "files": 1, "artifacts": 0}


def test_project_overview_projects_stage_budgets_and_quality_metrics():
    result = build_project_overview(
        project_id="task-1",
        workspace={"root": "C:/workspace"},
        task={
            "task_id": "task-1",
            "status": "completed",
            "summary": {
                "stage_budgets": {"inspect": {"used": 2, "limit": 24, "status": "active"}},
                "verification": [{"success": True}],
            },
        },
        activity={"events": [], "tool_runs": [], "changesets": [], "artifacts": []},
    )

    assert result["stage_budgets"]["inspect"]["used"] == 2
    assert result["quality_metrics"]["false_completion"] is False


def test_project_overview_falls_back_to_workspace_name_for_damaged_message():
    result = build_project_overview(
        project_id="task-1",
        workspace={"root": r"E:\projects\demo"},
        task={"task_id": "task-1", "user_message": "??", "status": "completed"},
        activity={},
    )

    assert result["summary"]["message"] == "demo"


def test_project_evidence_returns_empty_read_only_layer_for_unknown_task():
    result = build_project_evidence(project_id="missing", workspace=None, task=None, activity={})

    assert result["project_id"] == "missing"
    assert result["events"] == []
    assert result["tool_runs"] == []
    assert "tools" in result["developer_layers"]


def test_project_evidence_redacts_credentials_in_developer_payloads():
    result = build_project_evidence(
        project_id="task-secret",
        workspace={"root": "C:/workspace"},
        task={"task_id": "task-secret", "model_config": {"api_key": "secret-key"}},
        activity={
            "events": [{
                "type": "model_request_completed",
                "payload": {
                    "authorization": "Bearer secret",
                    "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                },
            }],
            "tool_runs": [{"tool_name": "http_request", "args": {"token": "secret-token"}, "result": {"ok": True}}],
            "changesets": [],
            "artifacts": [],
        },
    )

    assert result["task"]["model_config"]["api_key"] == "[REDACTED]"
    assert result["events"][0]["payload"]["authorization"] == "[REDACTED]"
    assert result["events"][0]["payload"]["usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    }
    assert result["tool_runs"][0]["args"]["token"] == "[REDACTED]"


def test_project_overview_uses_only_the_current_task_trace():
    result = build_project_overview(
        project_id="task-current",
        workspace=None,
        task={"task_id": "task-current", "status": "completed"},
        activity={},
        traces=[
            {"task_id": "task-newer", "status": "failed"},
            {"task_id": "task-current", "status": "completed"},
        ],
    )

    assert result["trace"]["task_id"] == "task-current"


def test_project_identity_is_stable_for_equivalent_windows_workspace_paths():
    first = project_id_for_workspace(r"E:\\Projects\\Demo")
    second = project_id_for_workspace("e:/projects/demo/")

    assert first == second
    assert first.startswith("project-")


def test_project_session_is_stable_and_separate_from_regular_chat_sessions():
    first = project_session_id_for_workspace("E:/Projects/App")
    second = project_session_id_for_workspace("e:\\projects\\app\\")

    assert first == second
    assert first.startswith("project-session-")


def test_project_actions_are_validated_and_have_execution_prompts():
    for action in ("inspect", "prepare", "start", "guide", "verify"):
        assert "当前项目" in project_action_prompt(action)

    with pytest.raises(ValueError, match="不支持的项目动作"):
        project_action_prompt("delete")


def test_project_stage_and_next_action_are_driven_by_execution_evidence():
    running = build_project_overview(
        project_id="project-a",
        workspace={"root": "E:/project"},
        task={
            "task_id": "task-a", "status": "running", "user_message": "继续",
            "summary": {"changed_files": [], "verification": [], "processes": [{"status": "running"}]},
        },
        activity={"events": [], "tool_runs": [], "changesets": []},
    )
    changed = build_project_overview(
        project_id="project-a",
        workspace={"root": "E:/project"},
        task={
            "task_id": "task-b", "status": "completed", "user_message": "继续",
            "summary": {"changed_files": ["app.py"], "verification": [], "processes": []},
        },
        activity={"events": [], "tool_runs": [], "changesets": [{"files": ["app.py"]}]},
    )

    assert running["stage"] == "run"
    assert "项目对话" in running["next_action"]
    assert changed["stage"] == "experiment"
    assert changed["next_action"] == "运行验证"


def test_project_summary_groups_tasks_under_workspace_identity():
    tasks = [
        {"task_id": "old", "workspace_root": "E:/projects/demo", "updated_at": "2026-08-14 10:00:00"},
        {"task_id": "new", "workspace_root": r"E:\\projects\\demo", "updated_at": "2026-08-14 11:00:00"},
    ]

    summary = build_project_summary(tasks)

    assert len(summary) == 1
    assert summary[0]["latest_task_id"] == "new"
    assert summary[0]["task_count"] == 2


def test_project_evidence_aggregates_history_into_filterable_entries():
    result = build_project_evidence(
        project_id="project-demo",
        workspace={"root": "C:/workspace"},
        task={"task_id": "latest", "status": "completed"},
        activity={},
        history=[
            {
                "task": {
                    "task_id": "older",
                    "session_id": "session-1",
                    "user_message": "运行测试",
                    "status": "failed",
                    "created_at": "2026-08-14 10:00:00",
                    "summary": {
                        "verification": [{
                            "command": "pytest -q",
                            "cwd": "C:/workspace",
                            "returncode": 1,
                            "success": False,
                        }],
                        "processes": [{
                            "process_id": "process-1",
                            "pid": 321,
                            "command": "python app.py",
                            "cwd": "C:/workspace",
                            "status": "exited",
                            "returncode": 1,
                            "url": "http://127.0.0.1:5000",
                        }],
                    },
                },
                "activity": {
                    "events": [],
                    "tool_runs": [{
                        "tool_name": "run_command",
                        "args": {"command": "pytest -q", "cwd": "C:/workspace"},
                        "result": {"success": False, "returncode": 1, "api_key": "secret"},
                        "recovered_by_call_id": "retry-1",
                        "created_at": "2026-08-14 10:01:00",
                    }],
                    "changesets": [{
                        "files": ["app.py"],
                        "diff": "+print('ready')",
                        "created_at": "2026-08-14 10:02:00",
                    }],
                    "artifacts": [],
                },
                "trace": {"task_id": "older", "session_id": "session-1"},
            },
            {
                "task": {
                    "task_id": "latest",
                    "session_id": "session-2",
                    "user_message": "修复后重试",
                    "status": "completed",
                    "created_at": "2026-08-14 11:00:00",
                    "summary": {"verification": [], "processes": []},
                },
                "activity": {"events": [], "tool_runs": [], "changesets": [], "artifacts": []},
                "trace": {"task_id": "latest", "session_id": "session-2"},
            },
        ],
    )

    assert [item["task_id"] for item in result["task_history"]] == ["latest", "older"]
    categories = {entry["category"] for entry in result["entries"]}
    assert {"tools", "files", "verification", "processes", "observability"} <= categories
    tool = next(entry for entry in result["entries"] if entry["category"] == "tools")
    assert tool["status"] == "recovered"
    assert tool["details"]["args"]["command"] == "pytest -q"
    assert tool["details"]["result"]["api_key"] == "[REDACTED]"
    process = next(entry for entry in result["entries"] if entry["category"] == "processes")
    assert process["details"]["pid"] == 321
    assert process["details"]["url"] == "http://127.0.0.1:5000"
    observability = next(entry for entry in result["entries"] if entry["category"] == "observability")
    assert observability["title"] == "本地 Trace"


def test_failure_patterns_aggregates_only_unrecovered_failures():
    patterns = _failure_patterns([
        {"tool_name": "run_command", "result": {"success": False, "error": "ModuleNotFoundError: flask"}, "created_at": "2026-08-14 10:00:00"},
        {"tool_name": "run_command", "result": {"success": False, "error": "ModuleNotFoundError: flask"}, "created_at": "2026-08-14 10:05:00"},
        {"tool_name": "run_command", "result": {"success": False, "error": "port 5000 already in use"}, "created_at": "2026-08-14 11:00:00"},
        {"tool_name": "run_command", "result": {"success": False, "error": "transient"}, "recovered_by_call_id": "retry-1", "created_at": "2026-08-14 12:00:00"},
        {"tool_name": "read_file", "result": {"success": True}, "created_at": "2026-08-14 13:00:00"},
    ])

    assert patterns == [
        {"tool_name": "run_command", "error": "ModuleNotFoundError: flask", "count": 2, "last_at": "2026-08-14 10:05:00"},
        {"tool_name": "run_command", "error": "port 5000 already in use", "count": 1, "last_at": "2026-08-14 11:00:00"},
    ]


def test_first_error_line_skips_empty_fields_and_falls_back_to_output():
    assert _first_error_line({"success": False, "error": "boom", "output": "ignored"}) == "boom"
    assert _first_error_line({"success": False, "error": "", "stderr": "  \nTraceback (most recent):\n  line 1"}) == "Traceback (most recent):"
    assert _first_error_line({"success": False, "output": "\n\nnpm ERR! code ENOENT"}) == "npm ERR! code ENOENT"
    assert _first_error_line({"success": False}) == "未知错误"


def test_project_overview_includes_failure_patterns_field():
    result = build_project_overview(
        project_id="project-a",
        workspace={"root": "C:/workspace"},
        task={"task_id": "task-a", "status": "incomplete"},
        activity={
            "events": [],
            "tool_runs": [
                {"tool_name": "run_command", "result": {"success": False, "error": "pip install failed"}, "created_at": "2026-08-14 10:00:00"},
                {"tool_name": "run_command", "result": {"success": True}, "created_at": "2026-08-14 10:01:00"},
            ],
            "changesets": [],
            "artifacts": [],
        },
    )

    assert result["failure_patterns"] == [
        {"tool_name": "run_command", "error": "pip install failed", "count": 1, "last_at": "2026-08-14 10:00:00"},
    ]


def test_project_report_markdown_renders_sections_from_facts():
    overview = build_project_overview(
        project_id="project-a",
        workspace={"root": "E:/projects/demo"},
        task={
            "task_id": "task-a", "status": "completed", "user_message": "跑通演示项目",
            "summary": {
                "verification": [{"success": True}],
                "processes": [{"process_id": "p1", "status": "running"}],
            },
        },
        activity={"events": [], "tool_runs": [], "changesets": [], "artifacts": []},
    )
    evidence = build_project_evidence(
        project_id="project-a",
        workspace={"root": "E:/projects/demo"},
        task={"task_id": "task-a", "status": "completed"},
        activity={},
    )
    markdown = build_project_report_markdown(
        overview=overview,
        evidence=evidence,
        memories=[{"content": "演示项目入口在 app.py", "source_type": "task", "verification_status": "verified", "confidence": 0.9}],
    )

    assert "# 项目报告 — demo" in markdown
    assert "## 失败模式" in markdown
    assert "未发现未恢复的工具失败" in markdown
    assert "## 项目记忆" in markdown
    assert "演示项目入口在 app.py" in markdown
    assert "## 任务历史" in markdown
    assert "跑通演示项目" in markdown
