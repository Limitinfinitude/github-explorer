import ast
import os
import re
from pathlib import Path

from .models import ToolResult
from .workspace import WorkspaceManager


class ContextEngine:
    # 大仓库（如 6 万文件）全量遍历 + 排序极慢，扫描预算内提前停止
    _SCAN_BUDGET = 20_000
    _IGNORED_DIRS = {
        ".git", ".venv", "__pycache__", "node_modules", "web_dist",
        "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    }
    _SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}
    _JS_SYMBOL = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?"
        r"(?:(class|interface|type|enum|function)\s+([A-Za-z_$][\w$]*)|"
        r"(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=)",
    )

    def __init__(self, workspaces: WorkspaceManager) -> None:
        self.workspaces = workspaces

    def repo_map(
        self,
        session_id: str,
        path: str = ".",
        max_files: int = 120,
        max_chars: int = 16_000,
    ) -> ToolResult:
        root = self.workspaces.resolve(session_id, path)
        if not root.is_dir():
            return ToolResult.fail(f"Repo Map 目录不存在: {path}")

        entries: list[str] = []
        scanned = 0
        for source in self._source_files(root):
            if scanned >= max_files:
                break
            scanned += 1
            relative = source.relative_to(root).as_posix()
            symbols = self._symbols(source)
            block = [relative, *(f"  {symbol}" for symbol in symbols)]
            candidate = "\n".join([*entries, *block])
            if len(candidate) > max_chars:
                break
            entries.extend(block)

        output = "\n".join(entries) or "未发现可索引的 Python/TypeScript/JavaScript 源文件"
        return ToolResult.ok(
            output=output,
            data={"map": output, "files_scanned": scanned, "truncated": scanned >= max_files},
        )

    def _source_files(self, root: Path):
        """惰性遍历源文件；扫描条目超过预算即停止（避免大仓库卡死）。"""
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in self._IGNORED_DIRS
            )
            for name in sorted(filenames):
                scanned += 1
                if scanned > self._SCAN_BUDGET:
                    return
                if Path(name).suffix.lower() not in self._SOURCE_SUFFIXES:
                    continue
                path = Path(dirpath) / name
                try:
                    if path.stat().st_size <= 512_000:
                        yield path
                except OSError:
                    continue

    def _symbols(self, path: Path) -> list[str]:
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        if path.suffix.lower() == ".py":
            return self._python_symbols(content)
        return self._javascript_symbols(content)

    @staticmethod
    def _python_symbols(content: str) -> list[str]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        symbols: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node.col_offset == 0:
                if isinstance(node, ast.Import):
                    names = ", ".join(alias.name for alias in node.names)
                    symbols.append(f"import {names} (line {node.lineno})")
                else:
                    symbols.append(f"from {node.module or '.'} import ... (line {node.lineno})")
            elif isinstance(node, ast.ClassDef):
                symbols.append(f"class {node.name} (line {node.lineno})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                symbols.append(f"{prefix} {node.name} (line {node.lineno})")
        return symbols[:80]

    @classmethod
    def _javascript_symbols(cls, content: str) -> list[str]:
        symbols: list[str] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            match = cls._JS_SYMBOL.match(line)
            if not match:
                continue
            kind, named, variable = match.groups()
            symbols.append(f"{kind or 'const'} {named or variable} (line {line_number})")
        return symbols[:80]
