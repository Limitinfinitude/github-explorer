from pathlib import Path

from agent.runtime.context import ContextEngine
from agent.runtime.workspace import WorkspaceManager


def test_repo_map_lists_source_symbols_and_ignores_dependencies(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(
        "import os\n\nclass Service:\n    def run(self):\n        return os.name\n",
        encoding="utf-8",
    )
    (root / "ui.ts").write_text(
        "export function renderApp() { return 'ok' }\n",
        encoding="utf-8",
    )
    (root / "node_modules").mkdir()
    (root / "node_modules" / "hidden.ts").write_text("export class Hidden {}", encoding="utf-8")
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)

    result = ContextEngine(workspaces).repo_map("session")

    assert result.success is True
    assert "app.py" in result.output
    assert "class Service" in result.output
    assert "def run" in result.output
    assert "function renderApp" in result.output
    assert "Hidden" not in result.output
