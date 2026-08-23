import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from .commands import CommandRunner, clean_environment
from .models import ToolResult
from .workspace import WorkspaceManager


def command_executable(path: Path) -> str:
    quoted = f'"{path}"'
    return f"& {quoted}" if platform.system() == "Windows" else quoted


class ProjectTools:
    def __init__(self, workspaces: WorkspaceManager, runner: CommandRunner) -> None:
        self.workspaces = workspaces
        self.runner = runner

    def detect(self, session_id: str, path: str = ".") -> ToolResult:
        root = self.workspaces.resolve(session_id, path)
        if not root.is_dir():
            return ToolResult.fail(f"项目目录不存在: {path}")

        languages: list[str] = []
        package_managers: list[str] = []
        if any((root / name).exists() for name in (
            "requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "poetry.lock"
        )):
            languages.append("python")
            if (root / "poetry.lock").exists():
                package_managers.append("poetry")
            elif (root / "Pipfile").exists():
                package_managers.append("pipenv")
            else:
                package_managers.append("pip")

        node_scripts: dict[str, str] = {}
        package_json = root / "package.json"
        if package_json.exists():
            languages.append("node")
            if (root / "pnpm-lock.yaml").exists():
                package_managers.append("pnpm")
            elif (root / "yarn.lock").exists():
                package_managers.append("yarn")
            else:
                package_managers.append("npm")
            try:
                package_data = json.loads(package_json.read_text(encoding="utf-8"))
                node_scripts = package_data.get("scripts", {})
            except (json.JSONDecodeError, OSError):
                node_scripts = {}

        data = {
            "root": str(root),
            "languages": languages,
            "package_managers": package_managers,
            "node_scripts": node_scripts,
            "has_git": (root / ".git").exists(),
            "has_readme": any((root / name).exists() for name in ("README.md", "README.rst", "README")),
            "has_env_example": any((root / name).exists() for name in (".env.example", ".env.sample")),
        }
        return ToolResult.ok(data=data, output=f"检测到项目类型: {', '.join(languages) or 'unknown'}")

    def clone_repository(self, session_id: str, url: str, destination: str) -> ToolResult:
        if not url.startswith(("https://", "http://", "ssh://", "git@")):
            return ToolResult.fail("仓库地址必须使用 http(s)、ssh 或 git@ 格式")
        target = self.workspaces.resolve(session_id, destination)
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            return ToolResult.fail(f"克隆目标不是空目录: {destination}")
        if not target.parent.is_dir():
            return ToolResult.fail(f"克隆目标父目录不存在: {target.parent}")

        result = subprocess.run(
            ["git", "clone", "--", url, str(target)],
            cwd=self.workspaces.get(session_id).root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=clean_environment(),
            timeout=600,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        relative = target.relative_to(self.workspaces.get(session_id).root).as_posix()
        return ToolResult(
            success=result.returncode == 0,
            data={"returncode": result.returncode, "path": str(target)},
            output=output,
            error=None if result.returncode == 0 else f"git clone 退出码: {result.returncode}",
            changed_files=[relative] if target.exists() else [],
        )

    def ensure_venv(self, session_id: str, path: str = ".") -> ToolResult:
        root = self.workspaces.resolve(session_id, path)
        venv_python = root / ".venv" / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")
        if venv_python.is_file():
            return ToolResult.ok(
                output=f"项目虚拟环境已存在: {venv_python}",
                data={"cwd": str(root), "python_executable": str(venv_python)},
            )
        command = f"{command_executable(Path(sys.executable))} -m venv .venv"
        result = self.runner.run(session_id, command, cwd=path, timeout=180)
        if result.success and venv_python.is_file():
            result.data["cwd"] = str(root)
            result.data["python_executable"] = str(venv_python)
        return result

    def install_commands(self, session_id: str, project_info: dict) -> list[str]:
        commands = []
        root = Path(project_info["root"])
        if "python" in project_info.get("languages", []):
            python = self.runner.project_python(session_id, str(root))
            if (root / "requirements.txt").exists():
                commands.append(f"{command_executable(python)} -m pip install -r requirements.txt")
            elif (root / "uv.lock").exists():
                # uv 管理的项目（flatnotes 等）：这类应用常无 packages/build-system 配置，
                # pip/uv 的 `-e .` 会因包发现失败；uv sync 按 lock 安装依赖即可运行。
                commands.append("uv sync --no-install-project")
            elif (root / "pyproject.toml").exists():
                commands.append(f"{command_executable(python)} -m pip install -e .")

        if "node" in project_info.get("languages", []):
            managers = project_info.get("package_managers", [])
            if "pnpm" in managers:
                commands.append("pnpm install --frozen-lockfile")
            elif "yarn" in managers:
                commands.append("yarn install --frozen-lockfile")
            elif (root / "package-lock.json").exists():
                commands.append("npm ci")
            else:
                commands.append("npm install")
        return commands

    def install_dependencies(self, session_id: str, project_info: dict, path: str = ".") -> ToolResult:
        if "python" in project_info.get("languages", []):
            try:
                self.runner.project_python(session_id, str(Path(project_info["root"])))
            except FileNotFoundError as exc:
                return ToolResult.fail(str(exc), data={"checks": [], "environment": {
                    "project_root": str(project_info["root"]),
                    "python_executable": None,
                }})
        checks = []
        for command in self.install_commands(session_id, project_info):
            result = self.runner.run(session_id, command, cwd=path, timeout=600)
            checks.append({"command": command, **result.to_dict()})
            if not result.success:
                return ToolResult.fail("依赖安装失败", data={"checks": checks}, output=result.output)
        return ToolResult.ok(data={"checks": checks}, output="依赖安装完成")
