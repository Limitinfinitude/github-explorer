from pathlib import Path

from .commands import CommandRunner
from .models import ToolResult
from .project_tools import command_executable
from .workspace import WorkspaceManager


class Verifier:
    def __init__(self, workspaces: WorkspaceManager, runner: CommandRunner) -> None:
        self.workspaces = workspaces
        self.runner = runner

    def commands(self, session_id: str, project_info: dict) -> list[str]:
        root = Path(project_info["root"])
        commands: list[str] = []

        if "python" in project_info.get("languages", []):
            python = self.runner.project_python(session_id, str(root))
            has_tests = (root / "tests").is_dir() and any((root / "tests").glob("test*.py"))
            if has_tests:
                commands.append(f"{command_executable(python)} -m pytest -q")
            else:
                # 排除虚拟环境与第三方目录：compileall 递归会把 .venv 里的
                # 旧版第三方包（如 Python 2 语法）误判为项目自身验证失败。
                commands.append(
                    f"{command_executable(python)} -m compileall -q "
                    "-x \"/(\\.venv|node_modules|__pycache__|\\.git)/\" ."
                )

        if "node" in project_info.get("languages", []):
            scripts = project_info.get("node_scripts", {})
            test_script = scripts.get("test", "")
            if test_script:
                commands.append("npm test -- --run" if "vitest" in test_script else "npm test")
            elif "build" in scripts:
                commands.append("npm run build")
        return commands

    @staticmethod
    def command_kind(command: str) -> str:
        if "-m pytest" in command or "npm test" in command:
            return "unit"
        if "compileall" in command:
            return "static"
        if "build" in command:
            return "build"
        return "command"

    def run(self, session_id: str, project_info: dict, path: str = ".") -> ToolResult:
        project_root = Path(project_info["root"])
        python_executable = None
        if "python" in project_info.get("languages", []):
            try:
                python_executable = self.runner.project_python(session_id, str(project_root))
            except FileNotFoundError as exc:
                return ToolResult.fail(
                    str(exc),
                    data={"checks": [], "environment": {
                        "project_root": str(project_root),
                        "python_executable": None,
                    }},
                )
        checks = []
        for command in self.commands(session_id, project_info):
            result = self.runner.run(session_id, command, cwd=path, timeout=300)
            checks.append({
                "command": command,
                "kind": self.command_kind(command),
                "success": result.success,
                "returncode": result.data.get("returncode"),
                "cwd": result.data.get("cwd"),
                "python_executable": str(python_executable) if python_executable else None,
                "output": result.output,
                "error": result.error,
            })
            if not result.success:
                return ToolResult.fail("项目验证失败", data={"checks": checks}, output=result.output)
        if not checks:
            return ToolResult.fail("没有检测到可运行的项目验证命令", data={"checks": []})
        return ToolResult.ok(data={"checks": checks}, output="项目验证通过")
