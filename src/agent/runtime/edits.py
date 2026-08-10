import difflib
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .models import ToolResult
from .workspace import WorkspaceManager


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    relative_path: str
    existed: bool
    before: str
    after: str


@dataclass(frozen=True)
class ChangeSet:
    files: tuple[FileSnapshot, ...]
    diff: str


class EditEngine:
    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces
        self._last_changeset: dict[str, ChangeSet] = {}

    def apply(self, session_id: str, edits: list[dict]) -> ToolResult:
        if not edits:
            return ToolResult.fail("没有提供文件编辑")

        prepared: list[FileSnapshot] = []
        try:
            for edit in edits:
                prepared.append(self._prepare(session_id, edit))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return ToolResult.fail(str(exc))

        changed = [snapshot for snapshot in prepared if snapshot.before != snapshot.after]
        if not changed:
            return ToolResult.ok(output="文件内容没有变化", data={"diff": ""})

        written: list[FileSnapshot] = []
        try:
            for snapshot in changed:
                self._atomic_write(snapshot.path, snapshot.after)
                written.append(snapshot)
        except OSError as exc:
            self._restore(reversed(written))
            return ToolResult.fail(f"写入失败，已回滚: {exc}")

        diff = "".join(self._diff(snapshot) for snapshot in changed)
        changeset = ChangeSet(files=tuple(changed), diff=diff)
        self._last_changeset[session_id] = changeset
        changed_files = [snapshot.relative_path for snapshot in changed]
        verification = [self._verify_snapshot(snapshot) for snapshot in changed]
        return ToolResult.ok(
            data={"diff": diff, "verification": verification},
            output=f"已修改 {len(changed_files)} 个文件",
            changed_files=changed_files,
        )

    def undo_last(self, session_id: str) -> ToolResult:
        changeset = self._last_changeset.get(session_id)
        if changeset is None:
            return ToolResult.fail("当前会话没有可撤销的文件修改")
        try:
            self._restore(reversed(changeset.files))
        except OSError as exc:
            return ToolResult.fail(f"撤销失败: {exc}")
        del self._last_changeset[session_id]
        changed_files = [snapshot.relative_path for snapshot in changeset.files]
        return ToolResult.ok(output="已撤销最近一次文件修改", changed_files=changed_files)

    def _prepare(self, session_id: str, edit: dict) -> FileSnapshot:
        relative_path = str(edit.get("path", "")).strip()
        if not relative_path:
            raise ValueError("编辑缺少 path")
        path = self.workspaces.resolve(session_id, relative_path)
        if path.exists() and not path.is_file():
            raise ValueError(f"目标不是文件: {relative_path}")
        if not path.parent.is_dir():
            raise ValueError(f"父目录不存在: {path.parent}")

        existed = path.exists()
        before = path.read_text(encoding="utf-8") if existed else ""
        operation = edit.get("operation")
        content = edit.get("content")
        if not isinstance(content, str):
            raise ValueError(f"编辑内容必须是字符串: {relative_path}")

        if operation == "write":
            after = content
        elif operation == "replace":
            search = edit.get("search")
            if not isinstance(search, str) or not search:
                raise ValueError(f"替换操作缺少 search: {relative_path}")
            count = before.count(search)
            if count != 1:
                raise ValueError(f"搜索文本必须唯一匹配，当前匹配 {count} 次: {relative_path}")
            after = before.replace(search, content, 1)
        else:
            raise ValueError(f"不支持的编辑操作: {operation}")

        return FileSnapshot(
            path=path,
            relative_path=path.relative_to(self.workspaces.get(session_id).root).as_posix(),
            existed=existed,
            before=before,
            after=after,
        )

    def _atomic_write(self, path: Path, content: str) -> None:
        temp = path.with_name(f".{path.name}.agent-{uuid.uuid4().hex}.tmp")
        try:
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink()

    def _restore(self, snapshots) -> None:
        for snapshot in snapshots:
            if snapshot.existed:
                self._atomic_write(snapshot.path, snapshot.before)
            elif snapshot.path.exists():
                snapshot.path.unlink()

    @staticmethod
    def _verify_snapshot(snapshot: FileSnapshot) -> dict:
        try:
            if not snapshot.path.is_file():
                return {"path": snapshot.relative_path, "success": False, "detail": "文件不存在"}
            current = snapshot.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return {"path": snapshot.relative_path, "success": False, "detail": str(exc)}
        if current != snapshot.after:
            return {"path": snapshot.relative_path, "success": False, "detail": "回读内容与写入内容不一致"}
        return {"path": snapshot.relative_path, "success": True, "detail": "文件存在且回读一致"}

    @staticmethod
    def _diff(snapshot: FileSnapshot) -> str:
        return "".join(difflib.unified_diff(
            snapshot.before.splitlines(keepends=True),
            snapshot.after.splitlines(keepends=True),
            fromfile=f"a/{snapshot.relative_path}",
            tofile=f"b/{snapshot.relative_path}",
        ))
