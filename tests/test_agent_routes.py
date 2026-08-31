import json
import importlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes_agent
from agent.memory import Memory
from agent.runtime.models import ToolResult
from agent.runtime.tooling import LocalAgentServices

memory_module = importlib.import_module("agent.memory")


def make_client(monkeypatch, tmp_path: Path, test_memory: Memory | None = None):
    services = LocalAgentServices.create()
    test_memory = test_memory or Memory(tmp_path / "routes-memory.db")
    # routes_agent 已改为模块级显式绑定 memory（组合根单点），
    # 测试替换实例需要同时替换消费方模块上的绑定。
    monkeypatch.setattr(memory_module, "memory", test_memory)
    monkeypatch.setattr(routes_agent, "memory", test_memory, raising=False)
    monkeypatch.setattr(routes_agent, "get_local_agent_services", lambda: services, raising=False)
    monkeypatch.setattr(routes_agent, "_agent_task_supervisor", None, raising=False)
    app = FastAPI()
    app.include_router(routes_agent.router_agent)
    return TestClient(app), services


def test_task_start_and_event_replay_routes_are_separate(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "task-routes.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_memory.set_workspace("session-a", str(workspace))
    client, _ = make_client(monkeypatch, tmp_path, local_memory)
    captured = {}

    class FakeSupervisor:
        def start(self, session_id, message, *, history=None, model_context=None, task_id=None, approval_mode="confirm", plan_mode=False):
            captured.update(session_id=session_id, message=message, history=history)
            local_memory.save_agent_task({
                "task_id": "task-started", "session_id": session_id,
                "user_message": message, "status": "pending", "summary": {},
            })
            return "task-started"

        async def subscribe(self, task_id, after_sequence=0):
            captured.update(task_id=task_id, after_sequence=after_sequence)
            yield {"type": "token", "content": "完成", "sequence": 2}
            yield {"type": "done", "content": "完成", "status": "completed", "sequence": 3}

    monkeypatch.setattr(routes_agent, "get_agent_task_supervisor", lambda: FakeSupervisor(), raising=False)

    started = client.post("/api/agent/tasks/start", json={
        "message": "检查项目",
        "session_id": "session-a",
        "workspace": str(workspace),
        "agent_mode": True,
    })
    replay = client.get("/api/agent/tasks/task-started/events?after_sequence=1")
    events = [json.loads(line[6:]) for line in replay.text.splitlines() if line.startswith("data: ")]

    assert started.status_code == 202
    assert started.json() == {
        "task_id": "task-started",
        "session_id": "session-a",
        "workspace": str(workspace.resolve()),
        "status": "pending",
    }
    assert [event["type"] for event in events] == ["token", "done"]
    assert captured["after_sequence"] == 1


def test_task_start_rejects_corrupted_input_with_actionable_error(monkeypatch, tmp_path: Path):
    client, _ = make_client(monkeypatch, tmp_path)

    response = client.post("/api/agent/tasks/start", json={
        "message": "????",
        "session_id": "session-a",
    })

    assert response.status_code == 400
    assert "编码" in response.json()["detail"]


def test_encoding_health_endpoint_reports_utf8_round_trip(monkeypatch, tmp_path: Path):
    client, _ = make_client(monkeypatch, tmp_path)

    response = client.get("/api/agent/health/encoding")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["encoding"] == "utf-8"
    assert "charset=utf-8" in response.headers["content-type"]


def test_evaluation_report_aggregates_task_tools_and_artifacts(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "report.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_memory.save_agent_task({
        "task_id": "report-task", "session_id": "session-a", "user_message": "build",
        "status": "completed", "workspace_root": str(workspace),
        "summary": {"changed_files": ["app.py"], "verification": [{"success": True}]},
    })
    local_memory.record_agent_tool_run(
        "report-task", "run_command", {"command": "pytest"}, {"success": True},
    )
    local_memory.record_agent_artifact(
        task_id="report-task", call_id="call-1", tool_name="run_command",
        content="log", mime_type="text/plain",
    )
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/evaluation-report")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["task_count"] == 1
    assert body["tasks"][0]["task_id"] == "report-task"
    assert body["tasks"][0]["tool_count"] == 1
    assert body["summary"]["artifact_count"] == 1


def test_resume_interrupted_task_route_starts_same_task(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "resume-route.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_memory.set_workspace("session-a", str(workspace))
    local_memory.save_agent_task({
        "task_id": "interrupted-task",
        "session_id": "session-a",
        "user_message": "resume",
        "status": "interrupted",
        "resume_available": True,
        "summary": {},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)
    captured = {}

    class FakeSupervisor:
        def resume_interrupted(self, session_id, task_id, *, model_context=None):
            captured.update(session_id=session_id, task_id=task_id, model_context=model_context)
            return task_id

    monkeypatch.setattr(routes_agent, "get_agent_task_supervisor", lambda: FakeSupervisor())

    response = client.post(
        "/api/agent/tasks/interrupted-task/resume",
        json={"session_id": "session-a"},
    )

    assert response.status_code == 202
    assert response.json()["task_id"] == "interrupted-task"
    assert captured["session_id"] == "session-a"
    assert captured["task_id"] == "interrupted-task"


def test_active_task_route_returns_latest_nonterminal_task(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "active-task.db")
    local_memory.save_agent_task({
        "task_id": "running-task",
        "session_id": "session-a",
        "user_message": "正在执行",
        "status": "running",
        "summary": {},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/sessions/session-a/active-task")

    assert response.status_code == 200
    assert response.json()["task"]["task_id"] == "running-task"


def test_bind_and_get_session_workspace(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    client, _ = make_client(monkeypatch, tmp_path, local_memory)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    bound = client.post("/api/agent/workspace", json={
        "session_id": "session-a",
        "path": str(workspace),
    })
    fetched = client.get("/api/agent/workspace/session-a")

    assert bound.status_code == 200
    assert bound.json()["workspace"] == str(workspace.resolve())
    assert fetched.json()["workspace"] == str(workspace.resolve())
    assert fetched.json()["profile"]["name"] == "workspace"
    assert fetched.json()["recent"] == [str(workspace.resolve())]


def test_new_session_uses_default_workspace_without_overwriting_existing_session(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    default = tmp_path / "default"
    existing = tmp_path / "existing"
    default.mkdir()
    existing.mkdir()
    local_memory.set_preference("default_workspace_root", str(default))
    local_memory.set_workspace("session-existing", str(existing))
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    fresh = client.get("/api/agent/workspace/session-new")
    restored = client.get("/api/agent/workspace/session-existing")

    assert fresh.json()["root"] == str(default.resolve())
    assert fresh.json()["source"] == "default"
    assert restored.json()["root"] == str(existing.resolve())
    assert restored.json()["source"] == "session"


def test_chat_stream_rejects_stale_workspace_instead_of_rebinding_session(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    selected = tmp_path / "selected"
    stale = tmp_path / "stale"
    selected.mkdir()
    stale.mkdir()
    local_memory.set_workspace("session-a", str(selected))
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.post("/api/agent/chat/stream", json={
        "message": "读取项目",
        "session_id": "session-a",
        "workspace": str(stale),
        "agent_mode": True,
    })
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert events[0]["type"] == "error"
    assert "工作区已变化" in events[0]["content"]
    assert local_memory.get_workspace_state("session-a")["root"] == str(selected.resolve())


def test_default_workspace_endpoint_validates_and_persists_path(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    client, _ = make_client(monkeypatch, tmp_path, local_memory)
    default = tmp_path / "default"
    default.mkdir()

    missing = client.put("/api/agent/workspace/default", json={"path": str(tmp_path / "missing")})
    saved = client.put("/api/agent/workspace/default", json={"path": str(default)})
    fetched = client.get("/api/agent/workspace/default")

    assert missing.status_code == 400
    assert saved.status_code == 200
    assert fetched.json() == {
        "path": str(default.resolve()),
        "source": "configured",
    }


def test_trace_and_observability_endpoints(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    local_memory.save_agent_task({
        "task_id": "task-route",
        "session_id": "session-a",
        "user_message": "运行测试",
        "status": "completed",
        "summary": {"changed_files": [], "verification": []},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    traces = client.get("/api/agent/traces")
    status = client.get("/api/agent/observability")

    assert traces.status_code == 200
    assert traces.json()["traces"][0]["task_id"] == "task-route"
    assert status.json()["local"]["enabled"] is True
    assert status.json()["local"]["coverage"] == [
        "model", "tool", "approval", "file", "verification", "process", "terminal",
    ]
    assert status.json()["local"]["summary"]["task_count"] == 1
    assert status.json()["local"]["summary"]["status_counts"] == {"completed": 1}


def test_trace_route_preserves_unicode_title(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    local_memory.save_agent_task({
        "task_id": "unicode-route",
        "session_id": "session-a",
        "user_message": "中文标题：实现搜索与筛选",
        "status": "completed",
        "summary": {"changed_files": [], "verification": []},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/traces")

    assert response.status_code == 200
    assert response.json()["traces"][0]["message"] == "中文标题：实现搜索与筛选"


def test_trace_route_filters_by_status_reason_workspace_and_time(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "trace-filters.db")
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    local_memory.save_agent_task({
        "task_id": "trace-failed",
        "session_id": "session-a",
        "workspace_root": str(workspace_a),
        "user_message": "失败任务",
        "status": "failed",
        "summary": {},
    })
    local_memory.save_agent_task({
        "task_id": "trace-completed",
        "session_id": "session-b",
        "workspace_root": str(workspace_b),
        "user_message": "完成任务",
        "status": "completed",
        "summary": {"verification": [{"success": True}]},
    })
    local_memory.conn.execute(
        "UPDATE agent_tasks SET created_at = ?, updated_at = ? WHERE task_id = ?",
        ("2026-08-14 10:00:00", "2026-08-14 10:05:00", "trace-failed"),
    )
    local_memory.conn.execute(
        "UPDATE agent_tasks SET created_at = ?, updated_at = ? WHERE task_id = ?",
        ("2026-08-15 10:00:00", "2026-08-15 10:05:00", "trace-completed"),
    )
    local_memory.conn.commit()
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/traces", params={
        "status": "failed",
        "workspace": str(workspace_a),
        "from": "2026-08-14 00:00:00",
        "to": "2026-08-14 23:59:59",
    })

    assert response.status_code == 200
    assert [trace["task_id"] for trace in response.json()["traces"]] == ["trace-failed"]


def test_task_detail_includes_ordered_event_stream(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    local_memory.save_agent_task({
        "task_id": "task-events",
        "session_id": "session-a",
        "user_message": "读取项目",
        "status": "completed",
        "summary": {"changed_files": [], "verification": []},
    })
    local_memory.record_agent_event({
        "task_id": "task-events",
        "session_id": "session-a",
        "type": "task_started",
    })
    local_memory.record_agent_event({
        "task_id": "task-events",
        "session_id": "session-a",
        "type": "task_completed",
        "content": "完成",
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/tasks/task-events")

    assert response.status_code == 200
    assert [event["type"] for event in response.json()["activity"]["events"]] == [
        "task_started", "task_completed",
    ]
    assert "tool_runs" in response.json()["activity"]
    assert "changesets" in response.json()["activity"]


def test_history_endpoint_prefers_agent_task_projection(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    local_memory.add_message("session-a", "assistant", "legacy answer")
    local_memory.save_agent_task({
        "task_id": "task-1", "session_id": "session-a", "status": "completed",
        "user_message": "event question", "final_text": "event answer", "summary": {},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/history/session-a")

    assert [item["content"] for item in response.json()["history"]] == ["event question", "event answer"]


def test_task_artifact_endpoint_is_task_scoped(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    artifact = local_memory.record_agent_artifact(
        task_id="task-artifact",
        call_id="call-1",
        tool_name="read_file",
        content='{"output":"full"}',
        mime_type="application/json",
    )
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get(
        f"/api/agent/tasks/task-artifact/artifacts/{artifact['artifact_id']}"
    )
    wrong_task = client.get(
        f"/api/agent/tasks/other-task/artifacts/{artifact['artifact_id']}"
    )

    assert response.status_code == 200
    assert response.json()["content"] == '{"output":"full"}'
    assert wrong_task.status_code == 404


def test_project_memory_search_endpoint_is_workspace_scoped(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "memory.db")
    local_memory.remember_project_fact(
        workspace_root="C:/project-a",
        content="pytest verified in project A",
        source_type="task",
        source_ref="task-a",
        confidence=1.0,
        verification_status="verified",
    )
    local_memory.remember_project_fact(
        workspace_root="C:/project-b",
        content="pytest verified in project B",
        source_type="task",
        source_ref="task-b",
        confidence=1.0,
        verification_status="verified",
    )
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.get("/api/agent/memory/search", params={
        "workspace": "C:/project-a",
        "q": "pytest",
    })

    assert response.status_code == 200
    assert [item["source_ref"] for item in response.json()["memories"]] == ["task-a"]


def test_agent_mode_stream_uses_local_runtime_without_intent_step(monkeypatch, tmp_path: Path):
    client, services = make_client(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    captured = {}

    class FakeRuntime:
        async def run(self, session_id, user_message, history=None, task_id=None, model_context=None, plan_mode=False):
            captured["model_context"] = model_context
            yield {"type": "plan", "steps": ["execute"], "session_id": session_id, "task_id": "task"}
            yield {"type": "done", "content": "ok", "status": "completed", "session_id": session_id, "task_id": "task"}

    monkeypatch.setattr(routes_agent, "get_local_agent_runtime", lambda: FakeRuntime(), raising=False)
    services.workspaces.bind("session-a", workspace)

    response = client.post("/api/agent/chat/stream", json={
        "message": "创建目录",
        "session_id": "session-a",
        "workspace": str(workspace),
        "agent_mode": True,
    })
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert [event["type"] for event in events] == ["workspace", "plan", "done"]
    assert all("意图" not in json.dumps(event, ensure_ascii=False) for event in events)
    assert set(captured["model_context"]) == {"id", "protocol", "base_url"}
    assert "api_key" not in captured["model_context"]


def test_agent_mode_stream_loads_persisted_agent_task_history(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "history-memory.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_memory.set_workspace("session-a", str(workspace))
    local_memory.save_agent_task({
        "task_id": "failed-task",
        "session_id": "session-a",
        "status": "failed",
        "user_message": "部署项目",
        "final_text": "模型请求读取超时；已克隆 project-a",
        "summary": {"changed_files": ["project-a"]},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)
    captured = {}

    class FakeRuntime:
        def register_task(self, session_id, task_id):
            return None

        async def run(self, session_id, user_message, history=None, task_id=None, model_context=None, plan_mode=False):
            captured["history"] = history
            yield {
                "type": "done", "content": "ok", "status": "completed",
                "session_id": session_id, "task_id": task_id,
            }

    monkeypatch.setattr(routes_agent, "get_local_agent_runtime", lambda: FakeRuntime(), raising=False)

    response = client.post("/api/agent/chat/stream", json={
        "message": "失败了吗？原因是什么",
        "session_id": "session-a",
        "workspace": str(workspace),
        "agent_mode": True,
    })

    assert response.status_code == 200
    assert [(item["role"], item["content"]) for item in captured["history"]] == [
        ("user", "部署项目"),
        ("assistant", "模型请求读取超时；已克隆 project-a"),
    ]


def test_approval_stream_does_not_duplicate_completed_resume_in_legacy_conversation(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "approval-memory.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_memory.set_workspace("session-a", str(workspace))
    local_memory.save_agent_task({
        "task_id": "approval-task",
        "session_id": "session-a",
        "user_message": "删除旧文件",
        "status": "waiting_approval",
        "summary": {"changed_files": [], "verification": [], "processes": []},
    })

    class FakeRuntime:
        async def resume(self, session_id, task_id, approved):
            assert (session_id, task_id, approved) == ("session-a", "approval-task", True)
            yield {
                "type": "tool_result",
                "session_id": session_id,
                "task_id": task_id,
                "name": "delete_path",
                "success": True,
            }
            yield {
                "type": "done",
                "session_id": session_id,
                "task_id": task_id,
                "status": "completed",
                "content": "旧文件已删除。",
            }

    client, _ = make_client(monkeypatch, tmp_path, local_memory)
    monkeypatch.setattr(routes_agent, "get_local_agent_runtime", lambda: FakeRuntime())

    response = client.post("/api/agent/approval/stream", json={
        "session_id": "session-a",
        "task_id": "approval-task",
        "approved": True,
    })
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]

    assert response.status_code == 200
    assert [event["type"] for event in events] == ["tool_result", "done"]
    assert events[-1]["status"] == "completed"
    history = local_memory.get_history("session-a")
    assert history == []


def test_process_list_endpoint_is_session_scoped(monkeypatch, tmp_path: Path):
    client, services = make_client(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    services.workspaces.bind("session-a", workspace)

    response = client.get("/api/agent/processes/session-a")

    assert response.status_code == 200
    assert response.json() == {"processes": []}


def test_process_detail_endpoint_returns_snapshot(monkeypatch, tmp_path: Path):
    client, services = make_client(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    services.workspaces.bind("session-a", workspace)

    response = client.get("/api/agent/processes/session-a/missing")

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert "missing" in response.json()["error"]


def test_cancel_endpoint_passes_session_and_task_to_runtime(monkeypatch, tmp_path: Path):
    client, _ = make_client(monkeypatch, tmp_path)
    captured = {}

    class FakeRuntime:
        def cancel(self, session_id, task_id):
            captured.update(session_id=session_id, task_id=task_id)
            return ToolResult.ok(output="cancelled", data={"status": "cancelled"})

    monkeypatch.setattr(routes_agent, "get_local_agent_runtime", lambda: FakeRuntime(), raising=False)

    response = client.post("/api/agent/tasks/task-1/cancel", json={"session_id": "session-a"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured == {"session_id": "session-a", "task_id": "task-1"}


def test_project_routes_group_tasks_by_stable_workspace_identity(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "project-memory.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    local_memory.set_workspace("session-a", str(workspace))
    for task_id, message in (("task-old", "项目体检"), ("task-new", "启动服务")):
        local_memory.save_agent_task({
            "task_id": task_id,
            "session_id": "session-a",
            "workspace_root": str(workspace),
            "current_path": str(workspace),
            "user_message": message,
            "status": "completed",
            "summary": {"changed_files": [], "verification": [], "processes": []},
        })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    projects = client.get("/api/projects").json()["projects"]
    assert len(projects) == 1
    assert projects[0]["task_count"] == 2
    assert projects[0]["latest_task_id"] == "task-new"

    overview = client.get(f"/api/projects/{projects[0]['project_id']}/overview").json()
    evidence = client.get(f"/api/projects/{projects[0]['project_id']}/evidence").json()
    assert overview["project_id"] == projects[0]["project_id"]
    assert overview["summary"]["task_id"] == "task-new"
    assert evidence["task"]["task_id"] == "task-new"
    assert [item["task_id"] for item in evidence["task_history"]] == ["task-new", "task-old"]


def test_project_action_starts_shared_runtime_in_stable_project_session(monkeypatch, tmp_path: Path):
    from agent.runtime.project_projection import project_id_for_workspace, project_session_id_for_workspace

    local_memory = Memory(tmp_path / "project-action.db")
    workspace = tmp_path / "project"
    workspace.mkdir()
    local_memory.save_agent_task({
        "task_id": "source-task", "session_id": "ordinary-chat",
        "workspace_root": str(workspace), "current_path": str(workspace),
        "user_message": "导入项目", "status": "completed", "summary": {},
    })
    client, services = make_client(monkeypatch, tmp_path, local_memory)
    captured = {}

    class FakeSupervisor:
        def start(self, session_id, message, *, history=None, model_context=None, task_id=None, plan_mode=False):
            captured.update(session_id=session_id, message=message)
            return "project-task"

    monkeypatch.setattr(routes_agent, "get_agent_task_supervisor", lambda: FakeSupervisor())
    project_id = project_id_for_workspace(str(workspace))

    response = client.post(f"/api/projects/{project_id}/actions/inspect")

    expected_session = project_session_id_for_workspace(str(workspace))
    assert response.status_code == 202
    assert response.json()["session_id"] == expected_session
    assert response.json()["task_id"] == "project-task"
    assert local_memory.get_workspace_state(expected_session)["root"] == str(workspace.resolve())
    assert services.workspaces.get(expected_session).root == workspace.resolve()
    assert captured["session_id"] == expected_session
    assert "项目体检" in captured["message"]


def test_project_action_rejects_unknown_action(monkeypatch, tmp_path: Path):
    from agent.runtime.project_projection import project_id_for_workspace

    local_memory = Memory(tmp_path / "project-action-invalid.db")
    workspace = tmp_path / "project"
    workspace.mkdir()
    local_memory.save_agent_task({
        "task_id": "source-task", "session_id": "ordinary-chat",
        "workspace_root": str(workspace), "user_message": "导入", "status": "completed", "summary": {},
    })
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    response = client.post(
        f"/api/projects/{project_id_for_workspace(str(workspace))}/actions/delete"
    )

    assert response.status_code == 400
