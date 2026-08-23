from pathlib import Path

import pytest

from agent.runtime.models import ToolResult, ToolRisk
from agent.runtime.workspace import WorkspaceBoundaryError, WorkspaceManager


def test_describe_workspace_detects_project_markers(tmp_path):
    root = tmp_path / "sample-project"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    (root / "package.json").write_text("{}\n", encoding="utf-8")
    (root / ".venv").mkdir()

    profile = WorkspaceManager.describe(root)

    assert profile == {
        "name": "sample-project",
        "path": str(root.resolve()),
        "git": True,
        "branch": "main",
        "python": True,
        "node": True,
        "venv": True,
    }


def test_bind_and_resolve_paths_inside_workspace(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "src").mkdir()

    manager = WorkspaceManager()
    workspace = manager.bind("session-a", root)

    assert workspace.root == root.resolve()
    assert manager.resolve("session-a", "src") == (root / "src").resolve()


def test_sessions_keep_independent_workspaces(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    manager = WorkspaceManager()
    manager.bind("session-a", first)
    manager.bind("session-b", second)

    assert manager.resolve("session-a", ".") == first.resolve()
    assert manager.resolve("session-b", ".") == second.resolve()


def test_current_path_is_session_scoped_and_stays_inside_root(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "src").mkdir(parents=True)
    (second / "docs").mkdir(parents=True)

    manager = WorkspaceManager()
    manager.bind("session-a", first)
    manager.bind("session-b", second)
    manager.set_current_path("session-a", "src")

    assert manager.current_path("session-a") == (first / "src").resolve()
    assert manager.current_path("session-b") == second.resolve()


def test_resolve_rejects_parent_traversal(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()

    manager = WorkspaceManager()
    manager.bind("session-a", root)

    with pytest.raises(WorkspaceBoundaryError):
        manager.resolve("session-a", "..")


def test_tool_result_has_stable_serializable_shape():
    result = ToolResult.ok(output="created", changed_files=["src/app.py"])

    assert result.to_dict() == {
        "success": True,
        "data": {},
        "output": "created",
        "error": None,
        "changed_files": ["src/app.py"],
        "process_id": None,
        "requires_confirmation": False,
        "confirmation_reason": None,
        "error_kind": None,
    }
    assert ToolRisk.WRITE_SAFE.value == "write_safe"
