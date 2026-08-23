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
    _SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go"}
    _JS_SYMBOL = re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?"
        r"(?:(class|interface|type|enum|function)\s+([A-Za-z_$][\w$]*)|"
        r"(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=)",
    )
    _JS_IMPORT = re.compile(
        r"""(?:from\s+|import\s+|require\()\s*["']([^"']+)["']"""
    )
    _GO_SYMBOL = re.compile(
        r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)\(|"
        r"^type\s+([A-Za-z_][\w]*)\s+(?:struct|interface)",
    )
    _GO_IMPORT = re.compile(r"^\s*(?:_\s+|\w+\s+)?\"([^\"]+)\"$")

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

        go_module = self._go_module_name(root)
        indexed: list[dict] = []
        scanned = 0
        for source in self._source_files(root):
            if scanned >= max_files:
                break
            scanned += 1
            relative = source.relative_to(root).as_posix()
            content = self._read(source)
            if content is None:
                continue
            indexed.append({
                "path": relative,
                "symbols": self._symbols(source, content),
                "imports": self._import_targets(source, content, root, go_module),
            })

        # 项目内引用计数：被越多文件 import 的文件越可能是核心，标注出来
        known = {item["path"] for item in indexed}
        ref_counts: dict[str, int] = {}
        for item in indexed:
            for target in item["imports"]:
                if target in known:
                    ref_counts[target] = ref_counts.get(target, 0) + 1

        entries: list[str] = []
        # 高被引文件优先展示；测试文件降权（大仓库里 *_test.go/test_*.py
        # 常占满名额，挤掉真正的实现文件）
        def _sort_key(item: dict):
            path = item["path"]
            is_test = "_test" in path or path.startswith("test_") or "/tests/" in path
            return (-ref_counts.get(path, 0), is_test, path)

        ordered = sorted(indexed, key=_sort_key)
        for item in ordered:
            refs = ref_counts.get(item["path"], 0)
            head = f"{item['path']}" + (f"  [被 {refs} 处引用]" if refs else "")
            block = [head, *(f"  {symbol}" for symbol in item["symbols"])]
            candidate = "\n".join([*entries, *block])
            if len(candidate) > max_chars:
                break
            entries.extend(block)

        output = "\n".join(entries) or (
            "未发现可索引的 Python/TypeScript/JavaScript/Go 源文件"
        )
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

    @staticmethod
    def _read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def _go_module_name(root: Path) -> str:
        try:
            for line in (root / "go.mod").read_text(encoding="utf-8").splitlines():
                if line.startswith("module "):
                    return line.split(None, 1)[1].strip()
        except OSError:
            pass
        return ""

    def _symbols(self, path: Path, content: str) -> list[str]:
        suffix = path.suffix.lower()
        if suffix == ".py":
            return self._python_symbols(content)
        if suffix == ".go":
            return self._go_symbols(content)
        return self._javascript_symbols(content)

    def _import_targets(
        self, path: Path, content: str, root: Path, go_module: str,
    ) -> list[str]:
        """提取 import 并归一化为项目内相对路径；外部依赖返回空。"""
        suffix = path.suffix.lower()
        targets: list[str] = []
        if suffix == ".py":
            targets = self._python_import_targets(content, path, root)
        elif suffix == ".go":
            targets = self._go_import_targets(content, root, go_module)
        else:
            targets = self._js_import_targets(content, path, root)
        return targets

    @staticmethod
    def _python_import_targets(content: str, path: Path, root: Path) -> list[str]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        candidates: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                candidates.append(node.module.replace(".", "/"))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidates.append(alias.name.replace(".", "/"))
        results = []
        for module in candidates:
            base = root / module
            for variant in (Path(module + ".py"), Path(module) / "__init__.py"):
                if (root / variant).is_file():
                    results.append(variant.as_posix())
                    break
            else:
                if base.is_dir() and (base / "__init__.py").is_file():
                    results.append((Path(module) / "__init__.py").as_posix())
        return results

    @classmethod
    def _go_import_targets(cls, content: str, root: Path, go_module: str) -> list[str]:
        if not go_module:
            return []
        results = []
        in_imports = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "import (":
                in_imports = True
                continue
            if in_imports and stripped == ")":
                in_imports = False
                continue
            match = cls._GO_IMPORT.match(line if in_imports else "")
            if not match and not in_imports:
                if stripped.startswith("import "):
                    match = re.match(r'import\s+(?:\w+\s+)?"([^"]+)"', stripped)
                else:
                    continue
            if not match:
                continue
            target = match.group(1)
            if not target.startswith(go_module + "/"):
                continue
            relative = target[len(go_module) + 1:]
            for variant in (relative + ".go", relative):
                if (root / variant).is_file():
                    results.append(variant)
                    break
        return results

    @classmethod
    def _js_import_targets(cls, content: str, path: Path, root: Path) -> list[str]:
        results = []
        for match in cls._JS_IMPORT.finditer(content):
            spec = match.group(1)
            if not spec.startswith("."):
                continue
            base = (path.parent / spec).resolve()
            try:
                base = base.relative_to(root)
            except ValueError:
                continue
            for suffix in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx"):
                candidate = Path(base.as_posix() + suffix)
                if (root / candidate).is_file():
                    results.append(candidate.as_posix())
                    break
        return results

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
    def _go_symbols(cls, content: str) -> list[str]:
        symbols: list[str] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            match = cls._GO_SYMBOL.match(line)
            if not match:
                continue
            func_name, type_name = match.groups()
            if func_name:
                symbols.append(f"func {func_name} (line {line_number})")
            else:
                symbols.append(f"type {type_name} (line {line_number})")
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
