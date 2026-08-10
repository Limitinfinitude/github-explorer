"""
本地启动管理模块

提供 Git 仓库克隆、项目类型自动检测、启动脚本生成等功能。
支持 Python / Node.js / Java / Rust / Go 等主流项目类型。
"""

import os
import shutil
import subprocess
import stat
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_CLONE_DIR = Path("./cloned_repos").resolve()


# 项目类型 -> (标识文件列表, 启动命令模板)
PROJECT_SIGNATURES: dict[str, tuple[list[str], list[str]]] = {
    "python": (
        ["requirements.txt", "setup.py", "pyproject.toml", "Pipfile"],
        ["pip install -r requirements.txt", "python main.py"],
    ),
    "node": (
        ["package.json"],
        ["npm install", "npm start"],
    ),
    "java_maven": (
        ["pom.xml"],
        ["mvn clean install", "mvn exec:java"],
    ),
    "java_gradle": (
        ["build.gradle", "build.gradle.kts"],
        ["gradle build", "gradle run"],
    ),
    "rust": (
        ["Cargo.toml"],
        ["cargo build", "cargo run"],
    ),
    "go": (
        ["go.mod"],
        ["go mod tidy", "go run ."],
    ),
}

# 用户友好的类型名称
TYPE_DISPLAY_NAMES: dict[str, str] = {
    "python": "Python",
    "node": "Node.js",
    "java_maven": "Java (Maven)",
    "java_gradle": "Java (Gradle)",
    "rust": "Rust",
    "go": "Go",
}


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def clone_repo(repo_url: str, target_dir: Optional[str] = None) -> Path:
    """
    克隆 Git 仓库到本地目录。

    Parameters
    ----------
    repo_url : str
        Git 仓库地址，支持 HTTPS / SSH。
    target_dir : str | None
        目标目录名称（相对于 clone 根目录）。为 None 时从 URL 自动推断。

    Returns
    -------
    Path
        克隆完成后的本地路径。

    Raises
    ------
    FileNotFoundError
        git 命令不可用时抛出。
    subprocess.CalledProcessError
        git clone 失败时抛出。
    """
    _ensure_git_available()

    clone_root = DEFAULT_CLONE_DIR
    clone_root.mkdir(parents=True, exist_ok=True)

    if target_dir is None:
        # 从 URL 推断目录名：去掉 .git 后缀，取最后一段
        name = repo_url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        target_dir = name

    dest = clone_root / target_dir

    if dest.exists():
        # 目录已存在，尝试 pull 更新
        print(f"[local_launcher] 目录已存在，执行 git pull: {dest}")
        subprocess.run(
            ["git", "pull"],
            cwd=str(dest),
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        print(f"[local_launcher] 克隆仓库: {repo_url} -> {dest}")
        subprocess.run(
            ["git", "clone", repo_url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )

    return dest


def detect_project_type(repo_path: str | Path) -> Optional[str]:
    """
    检测项目类型。

    Parameters
    ----------
    repo_path : str | Path
        项目根目录路径。

    Returns
    -------
    str | None
        项目类型标识符（如 "python"、"node" 等），无法识别时返回 None。
    """
    repo_path = Path(repo_path)

    if not repo_path.is_dir():
        raise FileNotFoundError(f"项目目录不存在: {repo_path}")

    for proj_type, (signatures, _commands) in PROJECT_SIGNATURES.items():
        for sig_file in signatures:
            if (repo_path / sig_file).is_file():
                return proj_type

    return None


def generate_launch_script(repo_path: str | Path, shell: str = "auto") -> Path:
    """
    根据项目类型自动生成启动脚本。

    Parameters
    ----------
    repo_path : str | Path
        项目根目录路径。
    shell : str
        目标 shell 类型: "auto" | "bash" | "powershell"。
        auto 会根据当前操作系统选择。

    Returns
    -------
    Path
        生成的启动脚本路径。

    Raises
    ------
    ValueError
        无法识别项目类型时抛出。
    """
    repo_path = Path(repo_path).resolve()
    proj_type = detect_project_type(repo_path)

    if proj_type is None:
        raise ValueError(
            f"无法识别项目类型: {repo_path}。"
            "支持的标识文件: requirements.txt, setup.py, package.json, "
            "pom.xml, build.gradle, Cargo.toml, go.mod"
        )

    _signatures, commands = PROJECT_SIGNATURES[proj_type]
    display_name = TYPE_DISPLAY_NAMES.get(proj_type, proj_type)

    if shell == "auto":
        shell = "powershell" if os.name == "nt" else "bash"

    if shell == "powershell":
        script_path = repo_path / "launch.ps1"
        script_content = _build_powershell_script(display_name, commands)
    else:
        script_path = repo_path / "launch.sh"
        script_content = _build_bash_script(display_name, commands)

    script_path.write_text(script_content, encoding="utf-8")

    # bash 脚本需要可执行权限
    if shell == "bash":
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"[local_launcher] 已生成启动脚本: {script_path}")
    return script_path


def get_launch_instructions(repo_path: str | Path) -> dict:
    """
    获取项目的启动说明。

    Parameters
    ----------
    repo_path : str | Path
        项目根目录路径。

    Returns
    -------
    dict
        包含以下字段:
        - project_type   : str   项目类型标识
        - display_name   : str   用户友好的类型名称
        - commands        : list  推荐执行的命令列表
        - readme_summary  : str   README 摘要（如有）
        - launch_script   : str   生成的启动脚本路径
        - manual_steps    : list  需要手动完成的步骤
    """
    repo_path = Path(repo_path).resolve()
    proj_type = detect_project_type(repo_path)

    if proj_type is None:
        return {
            "project_type": "unknown",
            "display_name": "未知项目",
            "commands": [],
            "readme_summary": _extract_readme_summary(repo_path),
            "launch_script": None,
            "manual_steps": ["无法自动识别项目类型，请查阅 README 手动配置。"],
        }

    _signatures, commands = PROJECT_SIGNATURES[proj_type]
    display_name = TYPE_DISPLAY_NAMES.get(proj_type, proj_type)

    # 尝试生成启动脚本
    try:
        script_path = generate_launch_script(repo_path)
        script_str = str(script_path)
    except Exception:
        script_str = None

    manual_steps = _get_manual_steps(proj_type, repo_path)

    return {
        "project_type": proj_type,
        "display_name": display_name,
        "commands": commands,
        "readme_summary": _extract_readme_summary(repo_path),
        "launch_script": script_str,
        "manual_steps": manual_steps,
    }


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _ensure_git_available() -> None:
    """确认 git 命令可用。"""
    if shutil.which("git") is None:
        raise FileNotFoundError(
            "未找到 git 命令。请先安装 Git: https://git-scm.com/downloads"
        )


def _build_bash_script(display_name: str, commands: list[str]) -> str:
    """生成 bash 启动脚本内容。"""
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"# 自动生成的 {display_name} 项目启动脚本",
        f"# 工作目录: $(cd \"$(dirname \"$0\")\" && pwd)",
        "",
        "cd \"$(cd \"$(dirname \"$0\")\" && pwd)\"",
        "",
        "echo '============================================'",
        f"echo '  {display_name} 项目启动'",
        "echo '============================================'",
        "",
    ]

    for i, cmd in enumerate(commands, 1):
        lines.append(f"echo '[{i}/{len(commands)}] 执行: {cmd}'")
        lines.append(cmd)
        lines.append("")

    lines.append("echo ''")
    lines.append("echo '启动完成！'")
    return "\n".join(lines) + "\n"


def _build_powershell_script(display_name: str, commands: list[str]) -> str:
    """生成 PowerShell 启动脚本内容。"""
    lines = [
        "# 自动生成的 {} 项目启动脚本".format(display_name),
        "$ErrorActionPreference = 'Stop'",
        "Set-Location $PSScriptRoot",
        "",
        "Write-Host '============================================'",
        "Write-Host '  {} 项目启动'".format(display_name),
        "Write-Host '============================================'",
        "",
    ]

    for i, cmd in enumerate(commands, 1):
        lines.append(f"Write-Host '[{i}/{len(commands)}] 执行: {cmd}'")
        lines.append(cmd)
        lines.append("")

    lines.append("Write-Host ''")
    lines.append("Write-Host '启动完成！'")
    return "\n".join(lines) + "\n"


def _extract_readme_summary(repo_path: Path, max_lines: int = 30) -> str:
    """提取 README 文件的前 N 行作为摘要。"""
    for name in ("README.md", "README.rst", "README.txt", "README", "readme.md"):
        readme_path = repo_path / name
        if readme_path.is_file():
            try:
                content = readme_path.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()[:max_lines]
                return "\n".join(lines)
            except Exception:
                continue
    return ""


def _get_manual_steps(proj_type: str, repo_path: Path) -> list[str]:
    """根据项目类型返回可能需要手动完成的步骤。"""
    steps: list[str] = []

    if proj_type == "python":
        # 检查是否需要虚拟环境
        if not (repo_path / "venv").is_dir() and not (repo_path / ".venv").is_dir():
            steps.append("建议创建虚拟环境: python -m venv venv")
        # 检查是否有 .env 文件模板
        if (repo_path / ".env.example").is_file() and not (repo_path / ".env").is_file():
            steps.append("请复制 .env.example 为 .env 并填写配置: cp .env.example .env")
        # 检查是否有数据库迁移
        if (repo_path / "manage.py").is_file():
            steps.append("Django 项目: 请执行 python manage.py migrate 进行数据库迁移")

    elif proj_type == "node":
        # 检查 node_modules
        if not (repo_path / "node_modules").is_dir():
            steps.append("需要安装依赖: npm install")
        # 检查 .env
        if (repo_path / ".env.example").is_file() and not (repo_path / ".env").is_file():
            steps.append("请复制 .env.example 为 .env 并填写配置")

    elif proj_type in ("java_maven", "java_gradle"):
        steps.append("请确保已安装 JDK 11+ 并配置 JAVA_HOME")

    elif proj_type == "rust":
        steps.append("请确保已安装 Rust 工具链: https://rustup.rs/")

    elif proj_type == "go":
        steps.append("请确保已安装 Go 1.18+: https://go.dev/dl/")

    return steps
