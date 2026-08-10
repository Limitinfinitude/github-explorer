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
                commands.append(f"{command_executable(python)} -m compileall -q .")

        if "node" in project_info.get("languages", []):
            scripts = project_info.get("node_scripts", {})
            test_script = scripts.get("test", "")
            if test_script:
                commands.append("npm test -- --run" if "vitest" in test_script else "npm test")
            elif "build" in scripts:
                commands.append("npm run build")
        return commands

    def run(self, session_id: str, project_info: dict, path: str = ".") -> ToolResult:
        checks = []
        for command in self.commands(session_id, project_info):
            result = self.runner.run(session_id, command, cwd=path, timeout=300)
            checks.append({
                "command": command,
                "success": result.success,
                "returncode": result.data.get("returncode"),
                "output": result.output,
                "error": result.error,
            })
            if not result.success:
                return ToolResult.fail("项目验证失败", data={"checks": checks}, output=result.output)
        if not checks:
            return ToolResult.ok(data={"checks": []}, output="没有检测到可运行的验证命令")
        return ToolResult.ok(data={"checks": checks}, output="项目验证通过")
