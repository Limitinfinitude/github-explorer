"""
项目检测与操作工具 — detect_project, install_deps, get_run_command, run_lint, run_tests, get_system_info
"""
import os
import platform
from pathlib import Path

from langsmith import traceable

from .runner import run_command


@traceable(name="tool_detect_project")
def detect_project(repo_path: str) -> dict:
    """
    检测项目类型、语言、包管理器等信息。
    """
    path = Path(repo_path)
    info = {
        "type": "unknown",
        "language": None,
        "package_manager": None,
        "has_docker": False,
        "has_readme": False,
        "env_example": None,
        "config_files": [],
    }

    # 语言检测
    signatures = {
        "python": ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile"],
        "node": ["package.json", "yarn.lock", "pnpm-lock.yaml"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
        "rust": ["Cargo.toml"],
        "go": ["go.mod"],
        "dotnet": ["*.csproj", "*.sln"],
    }

    for lang, files in signatures.items():
        for f in files:
            if (path / f).exists() or list(path.glob(f)):
                info["type"] = lang
                info["language"] = lang
                break

    # 包管理器检测
    pm_map = [
        ("yarn.lock", "yarn"),
        ("pnpm-lock.yaml", "pnpm"),
        ("package-lock.json", "npm"),
        ("Pipfile", "pipenv"),
        ("poetry.lock", "poetry"),
        ("requirements.txt", "pip"),
    ]
    for filename, pm in pm_map:
        if (path / filename).exists():
            info["package_manager"] = pm
            break

    # Docker 检测
    info["has_docker"] = (path / "Dockerfile").exists() or (
        path / "docker-compose.yml"
    ).exists()

    # README 检测
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        if (path / name).exists():
            info["has_readme"] = True
            break

    # 环境变量模板检测
    for name in [".env.example", ".env.sample", ".env.template"]:
        if (path / name).exists():
            info["env_example"] = name
            break

    return info


@traceable(name="tool_install_deps")
def install_deps(project_info: dict, cwd: str) -> dict:
    """
    根据项目类型安装依赖。
    """
    lang = project_info.get("type")
    pm = project_info.get("package_manager")

    install_commands = {
        "python": {
            "pip": "pip install -r requirements.txt",
            "pipenv": "pipenv install",
            "poetry": "poetry install",
            None: "pip install -r requirements.txt",
        },
        "node": {
            "yarn": "yarn install",
            "pnpm": "pnpm install",
            "npm": "npm install",
            None: "npm install",
        },
        "java": {"None": "mvn install"},
        "rust": {"None": "cargo build"},
        "go": {"None": "go mod download"},
    }

    lang_cmds = install_commands.get(lang, {})
    if isinstance(lang_cmds, dict):
        cmd = lang_cmds.get(pm, lang_cmds.get(None, "echo '未知项目类型'"))
    else:
        cmd = lang_cmds

    return run_command(cmd, cwd=cwd, timeout=300)


@traceable(name="tool_get_run_command")
def get_run_command(project_info: dict) -> str:
    """
    根据项目类型获取运行命令。
    """
    lang = project_info.get("type")
    commands = {
        "python": "python main.py",
        "node": "npm start",
        "java": "mvn exec:java",
        "rust": "cargo run",
        "go": "go run .",
    }
    return commands.get(lang, "echo '未知项目类型，请手动运行'")


@traceable(name="tool_run_lint")
def run_lint(repo_path: str, language: str) -> dict:
    """运行 lint 检查"""
    lint_cmds = {
        "python": "python -m ruff check . --output-format=text",
        "javascript": "npx eslint . --format=compact",
        "typescript": "npx eslint . --format=compact",
        "go": "golangci-lint run ./...",
        "rust": "cargo clippy -- -D warnings",
    }
    cmd = lint_cmds.get(language, f"echo 'No linter configured for {language}'")
    return run_command(cmd, cwd=repo_path, timeout=120)


@traceable(name="tool_run_tests")
def run_tests(repo_path: str, language: str) -> dict:
    """运行测试"""
    test_cmds = {
        "python": "python -m pytest --tb=short -q 2>&1 || true",
        "javascript": "npm test 2>&1 || true",
        "typescript": "npm test 2>&1 || true",
        "go": "go test ./... 2>&1 || true",
        "rust": "cargo test 2>&1 || true",
    }
    cmd = test_cmds.get(language, f"echo 'No test runner configured for {language}'")
    return run_command(cmd, cwd=repo_path, timeout=300)


def get_system_info() -> dict:
    """获取当前系统信息"""
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "shell": "powershell" if platform.system() == "Windows" else "bash",
        "home": str(Path.home()),
        "cwd": os.getcwd(),
    }
