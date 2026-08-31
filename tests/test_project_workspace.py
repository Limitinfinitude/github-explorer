"""工作台项目识别与归档：简单任务不套项目仪式（detect_workspace_kind / kind / archived）。"""
from pathlib import Path

from agent.memory import Memory
from agent.runtime.project_projection import (
    detect_workspace_kind,
    project_session_id_for_workspace,
)
from agent.runtime.workspace import WorkspaceManager  # noqa: F401

import routes_agent
from test_agent_routes import make_client  # noqa: E402


def test_detect_workspace_kind_by_markers(tmp_path: Path):
    git_repo = tmp_path / "repo"
    git_repo.mkdir()
    (git_repo / ".git").mkdir()
    assert detect_workspace_kind(str(git_repo)) == "project"

    node_pkg = tmp_path / "web"
    node_pkg.mkdir()
    (node_pkg / "package.json").write_text("{}", encoding="utf-8")
    assert detect_workspace_kind(str(node_pkg)) == "project"

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert detect_workspace_kind(str(scratch)) == "scratch"

    assert detect_workspace_kind(str(tmp_path / "not-exist")) == "scratch"
    assert detect_workspace_kind("") == "scratch"


def _seed_task(memory: Memory, workspace: Path, task_id: str = "task-1"):
    memory.save_agent_task({
        "task_id": task_id,
        "session_id": "session-a",
        "workspace_root": str(workspace),
        "current_path": str(workspace),
        "user_message": "跑个任务",
        "status": "completed",
        "summary": {"changed_files": [], "verification": [], "processes": []},
    })


def test_overviews_carries_kind_session_and_archived(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "ws-kind.db")
    project_dir = tmp_path / "real-project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text("[tool]\n", encoding="utf-8")
    scratch_dir = tmp_path / "scratch-dir"
    scratch_dir.mkdir()
    _seed_task(local_memory, project_dir, "task-project")
    _seed_task(local_memory, scratch_dir, "task-scratch")
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    rows = {row["workspace_root"]: row for row in client.get("/api/projects/overviews").json()["projects"]}
    assert rows[str(project_dir)]["kind"] == "project"
    assert rows[str(scratch_dir)]["kind"] == "scratch"
    for row in rows.values():
        assert row["session_id"].startswith("project-session-")
        assert row["archived"] is False


def test_archive_round_trip(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "ws-archive.db")
    project_dir = tmp_path / "real-project"
    project_dir.mkdir()
    (project_dir / ".git").mkdir()
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    _seed_task(local_memory, project_dir, "task-project")
    _seed_task(local_memory, scratch_dir, "task-scratch")
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    rows = {row["workspace_root"]: row for row in client.get("/api/projects/overviews").json()["projects"]}
    project_id = rows[str(project_dir)]["project_id"]

    # 归档 → archived=true；重新拉取仍在列表但标记归档
    res = client.post(f"/api/projects/{project_id}/archive", json={"archived": True})
    assert res.status_code == 200 and res.json()["archived"] is True
    rows = {row["workspace_root"]: row for row in client.get("/api/projects/overviews").json()["projects"]}
    assert rows[str(project_dir)]["archived"] is True
    assert rows[str(scratch_dir)]["archived"] is False

    # 取消归档
    client.post(f"/api/projects/{project_id}/archive", json={"archived": False})
    rows = {row["workspace_root"]: row for row in client.get("/api/projects/overviews").json()["projects"]}
    assert rows[str(project_dir)]["archived"] is False


def test_remove_project_deletes_records_but_keeps_other(monkeypatch, tmp_path: Path):
    local_memory = Memory(tmp_path / "ws-remove.db")
    doomed = tmp_path / "doomed"
    doomed.mkdir()
    (doomed / ".git").mkdir()
    keep = tmp_path / "keep-project"
    keep.mkdir()
    _seed_task(local_memory, doomed, "task-doomed")
    _seed_task(local_memory, keep, "task-keep")
    # 项目会话里的聊天消息 + 工作区绑定也应一并清理
    local_memory.save_chat_message(
        project_session_id_for_workspace(str(doomed)),
        {"role": "user", "content": "旧消息", "time": "2026-08-31T00:00:00Z"},
    )
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    rows = {row["workspace_root"]: row for row in client.get("/api/projects/overviews").json()["projects"]}
    doomed_id = rows[str(doomed)]["project_id"]

    res = client.delete(f"/api/projects/{doomed_id}")
    assert res.status_code == 200
    removed = res.json()["removed"]
    assert removed["agent_tasks"] == 1

    # 被移除项目的记录消失，另一个项目完好
    assert local_memory.get_agent_task("task-doomed") is None
    assert local_memory.get_agent_task("task-keep") is not None
    rows = {row["workspace_root"]: row for row in client.get("/api/projects/overviews").json()["projects"]}
    assert str(doomed) not in rows
    assert str(keep) in rows
    # 项目会话聊天消息已清
    session_id = project_session_id_for_workspace(str(doomed))
    assert local_memory.get_chat_messages(session_id) == []

    # 再次移除 → 404
    assert client.delete(f"/api/projects/{doomed_id}").status_code == 404


def test_import_does_not_autostart_inspection(monkeypatch, tmp_path: Path):
    """导入只建立绑定：不自动启动体检任务——想做什么由用户开口。"""
    local_memory = Memory(tmp_path / "ws-import.db")
    target = tmp_path / "new-project"
    target.mkdir()
    client, _ = make_client(monkeypatch, tmp_path, local_memory)

    started = []
    class _SpySupervisor:
        def start(self, *args, **kwargs):
            started.append((args, kwargs))
            return "spy-task"
    monkeypatch.setattr(routes_agent, "get_agent_task_supervisor", lambda: _SpySupervisor())

    res = client.post("/api/projects/import", json={"workspace": str(target)})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ready"
    assert "task_id" not in body
    assert started == [], "导入不得自动启动任何任务"
    # 但绑定已建立
    assert local_memory.get_workspace_state(body["session_id"])["root"] == str(target.resolve())
