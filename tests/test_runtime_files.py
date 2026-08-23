from pathlib import Path

from agent.runtime.edits import EditEngine
from agent.runtime.file_tools import FileTools
from agent.runtime.workspace import WorkspaceManager


def make_runtime(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    manager = WorkspaceManager()
    manager.bind("session", root)
    return root, FileTools(manager), EditEngine(manager)


def test_read_and_search_stay_in_workspace_and_ignore_dependencies(tmp_path: Path):
    root, files, _ = make_runtime(tmp_path)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("print('needle')\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "hidden.js").write_text("needle", encoding="utf-8")

    read = files.read_file("session", "src/app.py")
    search = files.search_text("session", "needle")

    assert read.success is True
    assert read.data["content"] == "print('needle')\n"
    assert search.success is True
    assert [match["path"] for match in search.data["matches"]] == ["src/app.py"]


def test_create_directory_reports_workspace_relative_change(tmp_path: Path):
    root, files, _ = make_runtime(tmp_path)

    result = files.create_directory("session", "src/package")

    assert result.success is True
    assert (root / "src" / "package").is_dir()
    assert result.changed_files == ["src/package"]
    assert result.data["path_kinds"] == {"src/package": "directory"}


def test_replace_requires_unique_search_text(tmp_path: Path):
    root, _, edits = make_runtime(tmp_path)
    path = root / "app.py"
    path.write_text("value = 1\nvalue = 1\n", encoding="utf-8")

    result = edits.apply("session", [{
        "path": "app.py",
        "operation": "replace",
        "search": "value = 1",
        "content": "value = 2",
    }])

    assert result.success is False
    assert "唯一" in result.error
    assert path.read_text(encoding="utf-8") == "value = 1\nvalue = 1\n"


def test_multiple_edits_to_same_file_are_applied_in_order_and_counted_once(tmp_path: Path):
    root, _, edits = make_runtime(tmp_path)
    path = root / "app.py"
    path.write_text("alpha\nbeta\n", encoding="utf-8")
    result = edits.apply("session", [
        {"path": "app.py", "operation": "replace", "search": "alpha", "content": "ALPHA"},
        {"path": "app.py", "operation": "replace", "search": "beta", "content": "BETA"},
    ])
    assert result.success is True
    assert result.changed_files == ["app.py"]
    assert path.read_text(encoding="utf-8") == "ALPHA\nBETA\n"
    assert result.data["path_kinds"] == {"app.py": "file"}
    assert result.data["verification"][0]["kind"] == "write_readback"


def test_write_creates_an_empty_file(tmp_path: Path):
    root, _, edits = make_runtime(tmp_path)
    result = edits.apply("session", [{
        "path": "requirements.txt", "operation": "write", "content": "",
    }])
    assert result.success is True
    assert (root / "requirements.txt").is_file()
    assert result.changed_files == ["requirements.txt"]


def test_batch_dry_run_prevents_partial_writes(tmp_path: Path):
    root, _, edits = make_runtime(tmp_path)
    first = root / "first.txt"
    second = root / "second.txt"
    first.write_text("before one", encoding="utf-8")
    second.write_text("before two", encoding="utf-8")

    result = edits.apply("session", [
        {
            "path": "first.txt",
            "operation": "replace",
            "search": "before one",
            "content": "after one",
        },
        {
            "path": "second.txt",
            "operation": "replace",
            "search": "missing",
            "content": "after two",
        },
    ])

    assert result.success is False
    assert first.read_text(encoding="utf-8") == "before one"
    assert second.read_text(encoding="utf-8") == "before two"


def test_changeset_returns_diff_and_can_be_undone(tmp_path: Path):
    root, _, edits = make_runtime(tmp_path)
    path = root / "app.py"
    path.write_text("answer = 41\n", encoding="utf-8")

    applied = edits.apply("session", [{
        "path": "app.py",
        "operation": "replace",
        "search": "answer = 41",
        "content": "answer = 42",
    }])

    assert applied.success is True
    assert applied.changed_files == ["app.py"]
    assert "-answer = 41" in applied.data["diff"]
    assert "+answer = 42" in applied.data["diff"]
    assert path.read_text(encoding="utf-8") == "answer = 42\n"

    undone = edits.undo_last("session")
    assert undone.success is True
    assert path.read_text(encoding="utf-8") == "answer = 41\n"
