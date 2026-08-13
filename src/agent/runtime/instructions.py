from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class InstructionSource:
    path: str
    scope: str
    content: str
    precedence: int


@dataclass
class InstructionContext:
    sources: list[InstructionSource] = field(default_factory=list)
    rendered: str = ""
    warnings: list[str] = field(default_factory=list)


class InstructionLoader:
    def __init__(
        self,
        workspace_root: Path | str,
        current_path: Path | str,
        *,
        user_home: Path | str | None = None,
        max_bytes: int = 32_768,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.current_path = Path(current_path).expanduser().resolve()
        self.user_home = Path(user_home).expanduser().resolve() if user_home else Path.home() / ".github-explorer"
        self.max_bytes = max(0, max_bytes)

    def load(self) -> InstructionContext:
        if not self._inside_workspace(self.current_path):
            return InstructionContext(warnings=[f"当前目录超出工作区: {self.current_path}"])

        candidates: list[tuple[Path, str, int]] = []
        user_file = self.user_home / "AGENTS.md"
        if user_file.is_file():
            candidates.append((user_file, "user", 0))

        relative = self.current_path.relative_to(self.workspace_root)
        directories = [self.workspace_root]
        cursor = self.workspace_root
        for part in relative.parts:
            cursor = cursor / part
            directories.append(cursor)

        for index, directory in enumerate(directories, start=1):
            override = directory / "AGENTS.override.md"
            regular = directory / "AGENTS.md"
            selected = override if override.is_file() else regular
            if selected.is_file():
                scope = "project" if directory == self.workspace_root else "directory"
                candidates.append((selected, scope, index))

        warnings: list[str] = []
        readable: list[InstructionSource] = []
        for path, scope, precedence in candidates:
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                warnings.append(f"无法读取指令文件 {path}: {exc}")
                continue
            readable.append(InstructionSource(str(path), scope, content, precedence))

        selected: list[InstructionSource] = []
        remaining = self.max_bytes
        for source in reversed(readable):
            size = len(source.content.encode("utf-8"))
            if size > remaining:
                warnings.append(f"指令预算不足，已跳过: {source.path}")
                continue
            selected.append(source)
            remaining -= size
        selected.reverse()

        rendered = "\n\n".join(
            f"[Agent instructions: {source.scope} | {source.path}]\n{source.content.strip()}"
            for source in selected
            if source.content.strip()
        )
        return InstructionContext(sources=selected, rendered=rendered, warnings=warnings)

    def _inside_workspace(self, path: Path) -> bool:
        try:
            return path == self.workspace_root or self.workspace_root in path.parents
        except OSError:
            return False
