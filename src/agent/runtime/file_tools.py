import os
from pathlib import Path

from .models import ToolResult
from .workspace import WorkspaceManager


IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
}


class FileTools:
    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces

    def _relative(self, session_id: str, path: Path) -> str:
        return path.relative_to(self.workspaces.get(session_id).root).as_posix()

    def list_directory(self, session_id: str, path: str = ".", limit: int = 200) -> ToolResult:
        target = self.workspaces.resolve(session_id, path)
        if not target.is_dir():
            return ToolResult.fail(f"目录不存在: {path}")

        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.name in IGNORED_DIRECTORIES:
                continue
            entries.append({
                "name": child.name,
                "path": self._relative(session_id, child),
                "type": "directory" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            })
            if len(entries) >= limit:
                break
        return ToolResult.ok(data={"entries": entries}, output=f"列出 {len(entries)} 个条目")

    def read_file(
        self,
        session_id: str,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
        max_chars: int = 50_000,
    ) -> ToolResult:
        target = self.workspaces.resolve(session_id, path)
        if not target.is_file():
            return ToolResult.fail(f"文件不存在: {path}")
        if target.stat().st_size > 2_000_000:
            return ToolResult.fail(f"文件过大，不能直接读取: {path}")

        try:
            content = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult.fail(f"不是 UTF-8 文本文件: {path}")
        if "\x00" in content:
            return ToolResult.fail(f"二进制文件不能作为文本读取: {path}")

        lines = content.splitlines(keepends=True)
        start = max(start_line - 1, 0)
        stop = len(lines) if end_line is None else min(end_line, len(lines))
        selected = "".join(lines[start:stop])[:max_chars]
        return ToolResult.ok(
            data={
                "path": self._relative(session_id, target),
                "content": selected,
                "start_line": start + 1,
                "end_line": min(stop, len(lines)),
                "total_lines": len(lines),
            },
            output=selected,
        )

    def search_text(
        self,
        session_id: str,
        query: str,
        path: str = ".",
        limit: int = 100,
    ) -> ToolResult:
        root = self.workspaces.resolve(session_id, path)
        if root.is_file():
            # 兼容 grep 习惯：path 传文件路径时只搜该文件
            targets = [root]
        elif root.is_dir():
            targets = None  # 递归搜索整个目录
        else:
            return ToolResult.fail(f"搜索路径不存在: {path}")

        matches = []
        if targets is None:
            for current, dirs, filenames in os.walk(root):
                dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRECTORIES)
                for filename in sorted(filenames):
                    self._collect_search_matches(
                        Path(current) / filename, query, matches, limit, session_id,
                    )
                    if len(matches) >= limit:
                        break
                if len(matches) >= limit:
                    break
        else:
            for file_path in targets:
                self._collect_search_matches(
                    file_path, query, matches, limit, session_id,
                )
                if len(matches) >= limit:
                    break

        return ToolResult.ok(data={"matches": matches}, output=f"找到 {len(matches)} 处匹配")

    def _collect_search_matches(
        self,
        file_path: Path,
        query: str,
        matches: list,
        limit: int,
        session_id: str,
    ) -> None:
        try:
            if file_path.stat().st_size > 1_000_000:
                return
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return
        for line_number, line in enumerate(lines, 1):
            if query in line:
                matches.append({
                    "path": self._relative(session_id, file_path),
                    "line": line_number,
                    "text": line[:500],
                })
                if len(matches) >= limit:
                    return

    def create_directory(self, session_id: str, path: str) -> ToolResult:
        target = self.workspaces.resolve(session_id, path)
        target.mkdir(parents=True, exist_ok=True)
        relative = self._relative(session_id, target)
        return ToolResult.ok(
            output=f"已创建目录: {relative}",
            data={"path_kinds": {relative: "directory"}},
            changed_files=[relative],
        )
