import pytest

from agent.memory import Memory


def test_agent_task_tool_run_and_changeset_are_persisted(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    state = {
        "task_id": "task-1",
        "session_id": "session-1",
        "user_message": "修改文件",
        "status": "running",
        "messages": [{"role": "user", "content": "修改文件"}],
        "summary": {"changed_files": []},
    }

    memory.save_agent_task(state)
    memory.record_agent_tool_run("task-1", "edit_files", {"path": "a.py"}, {"success": True})
    memory.record_agent_changeset("task-1", ["a.py"], "--- a.py")

    restored = memory.get_agent_task("task-1")
    activity = memory.get_agent_task_activity("task-1")

    assert restored["messages"] == state["messages"]
    assert activity["tool_runs"][0]["tool_name"] == "edit_files"
    assert activity["changesets"][0]["files"] == ["a.py"]


def test_agent_artifact_persists_full_content_and_lists_metadata(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    content = "完整工具输出" * 10_000

    artifact = memory.record_agent_artifact(
        task_id="task-1",
        call_id="call-1",
        tool_name="read_file",
        content=content,
        mime_type="application/json",
    )

    restored = memory.get_agent_artifact("task-1", artifact["artifact_id"])
    activity = memory.get_agent_task_activity("task-1")

    assert restored is not None
    assert restored["content"] == content
    assert restored["size"] == len(content.encode("utf-8"))
    assert activity["artifacts"] == [{
        key: value for key, value in restored.items() if key != "content"
    }]
    assert memory.get_agent_artifact("task-2", artifact["artifact_id"]) is None


def test_recent_workspaces_are_unique_and_most_recent_first(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.set_workspace("session-1", str(tmp_path / "alpha"))
    memory.set_workspace("session-2", str(tmp_path / "beta"))
    memory.set_workspace("session-3", str(tmp_path / "alpha"))

    assert memory.get_recent_workspaces() == [
        str(tmp_path / "alpha"),
        str(tmp_path / "beta"),
    ]


def test_workspace_state_keeps_root_and_current_path_and_default(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    root = tmp_path / "project"
    current = root / "src"
    root.mkdir()
    current.mkdir()

    memory.set_workspace("session-a", str(root), current_path=str(current))
    memory.set_preference("default_workspace_root", str(root))

    assert memory.get_workspace_state("session-a") == {
        "root": str(root),
        "current_path": str(current),
    }
    assert memory.get_preference("default_workspace_root") == str(root)


def test_session_requirement_ledger_deduplicates_and_keeps_unfinished_items(tmp_path):
    memory = Memory(tmp_path / "requirements.db")

    first = memory.merge_session_requirements(
        "session-1",
        ["支持中文搜索", "增加筛选统计"],
        source_task_id="task-1",
    )
    repeated = memory.merge_session_requirements(
        "session-1",
        ["支持中文搜索"],
        source_task_id="task-2",
    )
    memory.settle_session_requirements(
        "session-1",
        "task-1",
        [{"id": 1, "status": "passed", "evidence": [{"type": "check", "ref": "unit"}]}],
    )

    all_items = memory.list_session_requirements("session-1")
    pending = memory.list_session_requirements("session-1", status="pending")

    assert [item["position"] for item in first] == [1, 2]
    assert [item["position"] for item in repeated] == [1]
    assert len(all_items) == 2
    assert all_items[0]["status"] == "completed"
    assert all_items[0]["completed_task_id"] == "task-1"
    assert all_items[0]["evidence"] == [{"type": "check", "ref": "unit"}]
    assert [(item["position"], item["text"]) for item in pending] == [
        (2, "增加筛选统计"),
    ]


def test_agent_trace_summary_uses_persisted_task_activity(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    state = {
        "task_id": "task-1",
        "session_id": "session-1",
        "user_message": "修改并验证项目",
        "status": "completed",
        "summary": {
            "changed_files": ["a.py", "b.py"],
            "verification": [{"command": "pytest", "success": True}],
        },
    }
    memory.save_agent_task(state)
    memory.record_agent_tool_run("task-1", "read_file", {}, {"success": True})
    memory.record_agent_tool_run("task-1", "edit_files", {}, {"success": False, "error": "failed"})
    memory.record_agent_changeset("task-1", ["a.py", "b.py"], "diff")

    traces = memory.list_agent_traces()

    assert traces[0]["task_id"] == "task-1"
    assert traces[0]["tool_count"] == 2
    assert traces[0]["failed_tool_count"] == 1
    assert traces[0]["recovered_tool_count"] == 0
    assert traces[0]["changed_file_count"] == 2
    assert traces[0]["verification"] == "passed"


def test_agent_trace_title_preserves_unicode_and_flags_irrecoverable_legacy_damage(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    for task_id, message in (
        ("unicode-task", "修复中文标题与搜索功能"),
        ("legacy-task", "????????????????"),
    ):
        memory.save_agent_task({
            "task_id": task_id,
            "session_id": "session-1",
            "user_message": message,
            "status": "completed",
            "summary": {"changed_files": [], "verification": []},
        })

    traces = {trace["task_id"]: trace for trace in memory.list_agent_traces()}

    assert traces["unicode-task"]["message"] == "修复中文标题与搜索功能"
    assert traces["unicode-task"]["message_encoding_status"] == "intact"
    assert traces["legacy-task"]["message_encoding_status"] == "legacy_corrupted"


def test_agent_events_are_ordered_isolated_and_redacted(tmp_path):
    memory = Memory(tmp_path / "events.db")

    memory.record_agent_event({
        "task_id": "task-1",
        "session_id": "session-1",
        "type": "tool_call",
        "args": {"api_key": "secret-value", "path": "a.py"},
    })
    memory.record_agent_event({
        "task_id": "task-1",
        "session_id": "session-1",
        "type": "tool_result",
        "success": True,
    })
    memory.record_agent_event({
        "task_id": "task-2",
        "session_id": "session-2",
        "type": "task_started",
    })

    events = memory.get_agent_events("task-1")

    assert [event["sequence"] for event in events] == [1, 2]
    assert [event["type"] for event in events] == ["tool_call", "tool_result"]
    assert events[0]["payload"]["args"]["api_key"] == "[REDACTED]"
    assert events[0]["payload"]["args"]["path"] == "a.py"


def test_session_chat_history_projects_user_and_assistant_task_facts(tmp_path):
    memory = Memory(tmp_path / "events.db")
    for task_id, user, assistant in (
        ("task-1", "第一问", "第一答"),
        ("task-2", "第二问", "第二答"),
    ):
        memory.save_agent_task({
            "task_id": task_id, "session_id": "session-1", "status": "completed",
            "user_message": user, "final_text": assistant, "summary": {},
        })

    history = memory.get_agent_chat_history("session-1")

    assert [(item["role"], item["content"]) for item in history] == [
        ("user", "第一问"), ("assistant", "第一答"),
        ("user", "第二问"), ("assistant", "第二答"),
    ]


def test_previous_task_lookup_excludes_current_placeholder(tmp_path):
    memory = Memory(tmp_path / "previous-task.db")
    memory.save_agent_task({
        "task_id": "failed-task", "session_id": "session-1",
        "user_message": "部署项目", "status": "failed",
        "final_text": "模型读取超时，仓库已克隆。", "summary": {},
    })
    memory.save_agent_task({
        "task_id": "current-task", "session_id": "session-1",
        "user_message": "", "status": "pending", "summary": {},
    })

    previous = memory.get_previous_agent_task("session-1", exclude_task_id="current-task")

    assert previous["task_id"] == "failed-task"
    assert previous["status"] == "failed"


def test_project_memory_is_scoped_searchable_upserted_and_expiry_aware(tmp_path):
    memory = Memory(tmp_path / "project-memory.db")

    first_id = memory.remember_project_fact(
        workspace_root="C:/project-a",
        content="pytest verification passed for src/app.py api_key=secret-value",
        source_type="task",
        source_ref="task-1",
        confidence=0.9,
        verification_status="verified",
    )
    updated_id = memory.remember_project_fact(
        workspace_root="C:/project-a",
        content="pytest verification passed for src/main.py api_key=updated-secret",
        source_type="task",
        source_ref="task-1",
        confidence=1.0,
        verification_status="verified",
    )
    memory.remember_project_fact(
        workspace_root="C:/project-b",
        content="pytest verification passed for other.py",
        source_type="task",
        source_ref="task-2",
        confidence=1.0,
        verification_status="verified",
    )
    memory.remember_project_fact(
        workspace_root="C:/project-a",
        content="pytest obsolete fact",
        source_type="task",
        source_ref="task-expired",
        confidence=0.5,
        verification_status="unverified",
        expires_at="2000-01-01 00:00:00",
    )

    matches = memory.search_project_memories(
        "C:/project-a", "pytest", verified_only=True,
    )

    assert updated_id == first_id
    assert len(matches) == 1
    assert matches[0]["source_ref"] == "task-1"
    assert matches[0]["content"] == "pytest verification passed for src/main.py [REDACTED]"
    assert matches[0]["verification_status"] == "verified"


def test_agent_tool_call_ledger_persists_unique_call(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    call = {
        "task_id": "task-1",
        "session_id": "session-1",
        "call_id": "call-1",
        "batch_id": "batch-1",
        "tool_name": "read_file",
        "input": {"path": "README.md"},
    }

    memory.create_agent_tool_call(**call)
    memory.create_agent_tool_call(**call)

    calls = memory.get_agent_tool_calls("task-1")
    assert len(calls) == 1
    assert calls[0]["status"] == "parsed"
    assert calls[0]["input"] == {"path": "README.md"}


def test_agent_tool_call_ledger_enforces_transitions_and_terminal_identity(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.create_agent_tool_call(
        task_id="task-1",
        session_id="session-1",
        call_id="call-1",
        batch_id="batch-1",
        tool_name="read_file",
        input={"path": "README.md"},
    )

    memory.transition_agent_tool_call("task-1", "call-1", "running")
    settled = memory.transition_agent_tool_call(
        "task-1", "call-1", "succeeded", result={"success": True, "output": "ok"},
    )
    repeated = memory.transition_agent_tool_call(
        "task-1", "call-1", "succeeded", result={"success": True, "output": "ignored"},
    )

    assert settled["status"] == "succeeded"
    assert repeated["result"] == {"success": True, "output": "ok"}
    with pytest.raises(ValueError, match="终态"):
        memory.transition_agent_tool_call(
            "task-1", "call-1", "failed", result={"success": False, "error": "late"},
        )


def test_agent_tool_call_ledger_interrupts_only_open_calls(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    for call_id in ("done", "open", "approval"):
        memory.create_agent_tool_call(
            task_id="task-1",
            session_id="session-1",
            call_id=call_id,
            batch_id="batch-1",
            tool_name="read_file",
            input={"path": call_id},
        )
    memory.transition_agent_tool_call("task-1", "done", "running")
    memory.transition_agent_tool_call(
        "task-1", "done", "succeeded", result={"success": True},
    )
    memory.transition_agent_tool_call("task-1", "open", "running")
    memory.transition_agent_tool_call("task-1", "approval", "awaiting_approval")

    settled = memory.settle_open_agent_tool_calls("task-1", error="task interrupted")
    by_id = {call["call_id"]: call for call in memory.get_agent_tool_calls("task-1")}

    assert {call["call_id"] for call in settled} == {"open", "approval"}
    assert by_id["done"]["status"] == "succeeded"
    assert by_id["open"]["status"] == "interrupted"
    assert by_id["approval"]["status"] == "interrupted"
    assert by_id["open"]["result"]["error"] == "task interrupted"


def test_reconcile_interrupted_runtime_marks_running_tasks_calls_and_processes(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    state = {
        "task_id": "task-running",
        "session_id": "session-1",
        "user_message": "启动服务",
        "status": "running",
        "summary": {
            "changed_files": [],
            "verification": [],
            "processes": [{"process_id": "process-1", "status": "running", "pid": 12345}],
        },
    }
    memory.save_agent_task(state)
    memory.create_agent_tool_call(
        task_id="task-running", session_id="session-1", call_id="call-1",
        batch_id="batch-1", tool_name="start_process", input={"command": "python app.py"},
    )
    memory.transition_agent_tool_call("task-running", "call-1", "running")

    reconciled = memory.reconcile_interrupted_runtime()

    restored = memory.get_agent_task("task-running")
    call = memory.get_agent_tool_calls("task-running")[0]
    assert reconciled == ["task-running"]
    assert restored["status"] == "interrupted"
    assert restored["summary"]["processes"][0]["status"] == "orphaned"
    assert call["status"] == "interrupted"
    assert any(event["type"] == "runtime_reconciled" for event in memory.get_agent_events("task-running"))


def test_reconcile_marks_task_resumable_and_is_idempotent(tmp_path):
    memory = Memory(tmp_path / "resume-state.db")
    memory.save_agent_task({
        "task_id": "resume-task",
        "session_id": "session-1",
        "user_message": "继续任务",
        "status": "running",
        "summary": {},
    })

    assert memory.reconcile_interrupted_runtime() == ["resume-task"]
    assert memory.reconcile_interrupted_runtime() == []
    restored = memory.get_agent_task("resume-task")

    assert restored["status"] == "interrupted"
    assert restored["run"]["resume_available"] is True
    assert restored["run"]["resume_count"] == 0


def test_agent_tool_call_recovery_keeps_failure_terminal_and_persists_link(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    for call_id in ("failed-call", "retry-call"):
        memory.create_agent_tool_call(
            task_id="task-1",
            session_id="session-1",
            call_id=call_id,
            batch_id="batch-1",
            tool_name="run_command",
            input={"command": "pytest -q"},
            recovery_key="command:C:/project:test",
        )
        memory.transition_agent_tool_call("task-1", call_id, "running")
    memory.transition_agent_tool_call(
        "task-1", "failed-call", "failed",
        result={"success": False, "error": "tests failed"},
        error_kind="tool_error",
    )
    memory.transition_agent_tool_call(
        "task-1", "retry-call", "succeeded", result={"success": True},
    )

    memory.mark_agent_tool_call_recovered("task-1", "failed-call", "retry-call")

    failed = next(call for call in memory.get_agent_tool_calls("task-1") if call["call_id"] == "failed-call")
    assert failed["status"] == "failed"
    assert failed["recovery_key"] == "command:C:/project:test"
    assert failed["recovered_by_call_id"] == "retry-call"
    assert failed["recovered_at"] is not None


def test_agent_trace_counts_only_unrecovered_failures(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    state = {
        "task_id": "task-1",
        "session_id": "session-1",
        "user_message": "修复测试",
        "status": "completed",
        "summary": {"changed_files": [], "verification": []},
    }
    memory.save_agent_task(state)
    for call_id, status in (("recovered-failure", "failed"), ("open-failure", "failed"), ("retry", "succeeded")):
        memory.create_agent_tool_call(
            task_id="task-1", session_id="session-1", call_id=call_id,
            batch_id="batch-1", tool_name="run_command",
            input={"command": "pytest -q"}, recovery_key="command:C:/project:test",
        )
        memory.transition_agent_tool_call("task-1", call_id, "running")
        memory.transition_agent_tool_call(
            "task-1", call_id, status,
            result={"success": status == "succeeded", "error": None if status == "succeeded" else "failed"},
            error_kind=None if status == "succeeded" else "tool_error",
        )
    memory.mark_agent_tool_call_recovered("task-1", "recovered-failure", "retry")

    trace = memory.list_agent_traces()[0]

    assert trace["failed_tool_count"] == 1
    assert trace["recovered_tool_count"] == 1


def test_agent_trace_exposes_terminal_reason_and_quality_evidence(tmp_path):
    memory = Memory(tmp_path / "trace-quality.db")
    memory.save_agent_task({
        "task_id": "budget-task", "session_id": "session-1",
        "user_message": "启动服务", "status": "incomplete",
        "summary": {
            "changed_files": ["app.py"],
            "verification": [{"success": True}],
            "acceptance": [{"status": "passed", "evidence": [{"valid": True}]}],
            "stage_budgets": {"run": {"used": 2, "limit": 2, "status": "exhausted"}},
        },
    })
    memory.record_agent_event({
        "task_id": "budget-task", "session_id": "session-1",
        "type": "stage_budget_exhausted", "stage": "run",
    })

    trace = memory.list_agent_traces()[0]

    assert trace["terminal_reason"] == "stage_budget_exhausted"
    assert trace["completion_evidence"] == "verified"
    assert trace["acceptance_passed"] is True
    assert trace["budget_exhausted_stages"] == ["run"]


def test_terminal_task_writes_versioned_metrics_snapshot(tmp_path):
    memory = Memory(tmp_path / "metrics-snapshot.db")
    memory.save_agent_task({
        "task_id": "snapshot-task",
        "session_id": "session-a",
        "user_message": "生成报告",
        "status": "completed",
        "summary": {"changed_files": ["report.md"], "verification": [{"success": True}]},
    })
    memory.record_agent_event({
        "task_id": "snapshot-task",
        "session_id": "session-a",
        "type": "task_completed",
    })

    snapshot = memory.get_agent_metrics_snapshot("snapshot-task")

    assert snapshot is not None
    assert snapshot["metrics_version"]
    assert snapshot["metrics"]["completion_evidence"] == "verified"


def test_trace_reads_matching_snapshot_without_loading_full_activity(tmp_path, monkeypatch):
    memory = Memory(tmp_path / "metrics-read.db")
    memory.save_agent_task({
        "task_id": "snapshot-read",
        "session_id": "session-a",
        "user_message": "读取快照",
        "status": "completed",
        "summary": {"changed_files": ["app.py"], "verification": [{"success": True}]},
    })
    memory.record_agent_event({
        "task_id": "snapshot-read",
        "session_id": "session-a",
        "type": "task_completed",
    })

    def fail_if_full_activity_loaded(task_id):
        raise AssertionError(f"full activity loaded for {task_id}")

    monkeypatch.setattr(memory, "get_agent_task_activity", fail_if_full_activity_loaded)

    traces = memory.list_agent_traces()

    assert traces[0]["task_id"] == "snapshot-read"
    assert traces[0]["completion_evidence"] == "verified"


def test_legacy_task_without_snapshot_is_recomputed_and_backfilled(tmp_path):
    memory = Memory(tmp_path / "metrics-legacy.db")
    memory.save_agent_task({
        "task_id": "legacy-metrics",
        "session_id": "session-a",
        "user_message": "兼容旧任务",
        "status": "completed",
        "summary": {"changed_files": ["app.py"], "verification": [{"success": True}]},
    })
    memory.conn.execute("DELETE FROM agent_task_metrics WHERE task_id = ?", ("legacy-metrics",))
    memory.conn.commit()

    trace = memory.list_agent_traces()[0]

    assert trace["completion_evidence"] == "verified"
    assert memory.get_agent_metrics_snapshot("legacy-metrics") is not None


def test_agent_task_activity_marks_recovered_tool_run(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.create_agent_tool_call(
        task_id="task-1", session_id="session-1", call_id="failed-call",
        batch_id="batch-1", tool_name="run_command", input={"command": "pytest -q"},
        recovery_key="command:C:/project:test",
    )
    memory.transition_agent_tool_call("task-1", "failed-call", "running")
    memory.transition_agent_tool_call(
        "task-1", "failed-call", "failed", result={"success": False}, error_kind="tool_error",
    )
    memory.create_agent_tool_call(
        task_id="task-1", session_id="session-1", call_id="retry-call",
        batch_id="batch-2", tool_name="run_command", input={"command": "pytest -q"},
        recovery_key="command:C:/project:test",
    )
    memory.transition_agent_tool_call("task-1", "retry-call", "running")
    memory.transition_agent_tool_call(
        "task-1", "retry-call", "succeeded", result={"success": True},
    )
    memory.mark_agent_tool_call_recovered("task-1", "failed-call", "retry-call")
    memory.record_agent_tool_run(
        "task-1", "run_command", {"command": "pytest -q"},
        {"success": False, "call_id": "failed-call"},
    )

    activity = memory.get_agent_task_activity("task-1")

    assert activity["tool_runs"][0]["recovered_by_call_id"] == "retry-call"


def test_session_requirement_context_keeps_pending_and_recent_evidence_only(tmp_path):
    memory = Memory(tmp_path / "requirements-context.db")
    memory.merge_session_requirements(
        "session", [f"待办 {index}" for index in range(1, 5)], source_task_id="task-1",
    )
    memory.settle_session_requirements(
        "session", "task-2", [{"id": 1, "status": "passed", "evidence": [{"type": "check", "ref": "unit"}]}],
    )

    context = memory.get_session_requirement_context(
        "session", pending_limit=2, recent_completed_limit=1,
    )

    assert [item["position"] for item in context["pending"]] == [2, 3]
    assert context["pending_truncated"] is True
    assert context["recent_completed"][0]["position"] == 1
    assert context["recent_completed"][0]["evidence"] == [{"type": "check", "ref": "unit"}]
