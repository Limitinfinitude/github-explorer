import os
from pathlib import Path
from threading import RLock
from typing import Union

from .models import Workspace


PathLike = Union[str, Path]


class WorkspaceError(ValueError):
    pass


class WorkspaceNotBoundError(WorkspaceError):
    pass


class WorkspaceBoundaryError(WorkspaceError):
    pass


class WorkspaceManager:
    def __init__(self) -> None:
        self._workspaces: dict[str, Workspace] = {}
        self._lock = RLock()

    @staticmethod
    def describe(root: PathLike) -> dict:
        """Return inexpensive project markers for workspace selection UI."""
        resolved = Path(root).expanduser().resolve()
        git = resolved / ".git"
        branch = None
        head = git / "HEAD"
        if head.is_file():
            try:
                value = head.read_text(encoding="utf-8").strip()
                if value.startswith("ref: refs/heads/"):
                    branch = value.removeprefix("ref: refs/heads/")
                elif value:
                    branch = "detached"
            except OSError:
                branch = None
        python = any((resolved / marker).exists() for marker in (
            "pyproject.toml", "requirements.txt", "setup.py", "Pipfile",
        ))
        node = (resolved / "package.json").is_file()
        venv = (resolved / ".venv").is_dir() or (resolved / "venv").is_dir()
        return {
            "name": resolved.name or str(resolved),
            "path": str(resolved),
            "git": git.exists(),
            "branch": branch,
            "python": python,
            "node": node,
            "venv": venv,
        }

    def bind(self, session_id: str, root: PathLike) -> Workspace:
        resolved = Path(root).expanduser().resolve()
        if not resolved.is_dir():
            raise WorkspaceError(f"工作区目录不存在: {resolved}")

        workspace = Workspace(session_id=session_id, root=resolved, current_path=resolved)
        with self._lock:
            self._workspaces[session_id] = workspace
        return workspace

    def current_path(self, session_id: str) -> Path:
        workspace = self.get(session_id)
        return workspace.current_path or workspace.root

    def set_current_path(self, session_id: str, path: PathLike) -> Path:
        workspace = self.get(session_id)
        candidate = self.resolve(session_id, path)
        if not candidate.is_dir():
            raise WorkspaceError(f"当前路径不是目录: {candidate}")
        updated = Workspace(session_id=session_id, root=workspace.root, current_path=candidate)
        with self._lock:
            self._workspaces[session_id] = updated
        return candidate

    def get(self, session_id: str) -> Workspace:
        with self._lock:
            workspace = self._workspaces.get(session_id)
        if workspace is None:
            raise WorkspaceNotBoundError(f"会话尚未绑定工作区: {session_id}")
        return workspace

    def resolve(self, session_id: str, path: PathLike = ".") -> Path:
        workspace = self.get(session_id)
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = (workspace.current_path or workspace.root) / candidate
        candidate = candidate.resolve()

        root_key = os.path.normcase(str(workspace.root))
        candidate_key = os.path.normcase(str(candidate))
        try:
            inside = os.path.commonpath((root_key, candidate_key)) == root_key
        except ValueError:
            inside = False
        if not inside:
            raise WorkspaceBoundaryError(f"路径超出工作区: {candidate}")
        return candidate
