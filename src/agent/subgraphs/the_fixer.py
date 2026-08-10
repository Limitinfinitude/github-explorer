"""
The Fixer（代码研究员）子智能体 - 核心亮点：Self-Correction 循环

子图流程（带循环）：
[START] → prepare_repo → write_code → run_lint → check_lint
                                                    ├─ pass → run_tests → check_tests
                                                    │                        ├─ pass → create_pr → [END]
                                                    │                        └─ fail → (iterations<3?) → write_code
                                                    └─ fail → (iterations<3?) → write_code
                                                          └─ iterations≥3 → give_up → [END]
"""

import httpx

from langgraph.graph import StateGraph, END
from langsmith import traceable

from ..swarm_state import SwarmState
from ..tools import (
    clone_repo,
    detect_project,
    run_lint,
    run_tests,
    create_branch,
    commit_file,
    create_pull_request,
    fetch_repo_info,
    run_command,
)
from ..llm import call_llm
from ..prompts import FIXER_SYSTEM_PROMPT, FIXER_WRITE_CODE_PROMPT, FIXER_SELF_CORRECT_PROMPT


@traceable(name="fixer_prepare_repo")
async def prepare_repo_node(state: SwarmState) -> dict:
    """
    准备仓库环境：
    - 从 issue_details 提取 issue number
    - 克隆仓库（如未克隆）
    - 检测项目类型
    - 创建修复分支
    """
    repo = state.get("repo", "")
    issue_details = state.get("issue_details", {}) or {}
    issue_number = issue_details.get("number", 0)

    # 克隆仓库
    local_path = state.get("local_path")
    if not local_path:
        clone_result = clone_repo(repo)
        local_path = clone_result.get("path", "")
        if not clone_result.get("success"):
            return {
                "response": f"克隆仓库失败: {clone_result.get('output', '')}",
                "fix_status": "failed",
                "execution_steps": [{"step": "clone_repo", "status": "failed", "detail": clone_result.get("output", "")}],
            }

    # 检测项目类型
    project_info = detect_project(local_path)
    language = project_info.get("language") or project_info.get("type", "unknown")

    # 创建分支
    branch_name = f"fix/issue-{issue_number}" if issue_number else "fix/auto-patch"
    # fetch_repo_info 当前不含 default_branch，需从 API 单独取 SHA
    default_branch = "main"
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        resp = httpx.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=30)
        if resp.status_code == 200:
            default_branch = resp.json().get("default_branch", "main")
    except Exception:
        default_branch = "main"

    # 获取 base SHA
    base_sha = ""
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/branches/{default_branch}",
            headers=headers,
            timeout=30,
        )
        if resp.status_code == 200:
            base_sha = resp.json()["commit"]["sha"]
    except Exception:
        pass

    if base_sha:
        branch_result = create_branch(repo, branch_name, base_sha)
        if not branch_result.get("success"):
            return {
                "response": f"创建分支失败: {branch_result.get('error', '')}",
                "fix_status": "failed",
                "execution_steps": [{"step": "create_branch", "status": "failed", "detail": branch_result.get("error", "")}],
            }

    return {
        "fix_branch": branch_name,
        "project_info": {"language": language, "local_path": local_path, **project_info},
        "execution_steps": [
            {"step": "clone_repo", "status": "success", "detail": local_path},
            {"step": "detect_project", "status": "success", "detail": f"语言: {language}"},
            {"step": "create_branch", "status": "success", "detail": branch_name},
        ],
    }


@traceable(name="fixer_write_code")
async def write_code_node(state: SwarmState) -> dict:
    """
    生成修复代码：
    - 首次尝试：使用 FIXER_WRITE_CODE_PROMPT
    - 重试时：使用 FIXER_SELF_CORRECT_PROMPT（包含之前的错误信息）
    """
    fix_iterations = state.get("fix_iterations", 0)

    issue_details = state.get("issue_details", {}) or {}
    solutions = state.get("solutions", []) or []
    project_info = state.get("project_info", {}) or {}
    language = project_info.get("language", "unknown")

    issue_title = issue_details.get("title", "")
    issue_body = issue_details.get("body", "")
    solution = solutions[0] if solutions else "请根据 Issue 描述自行分析并修复"

    if fix_iterations > 0:
        # Self-Correction：使用之前的错误信息构建 prompt
        lint_result = state.get("lint_result", {}) or {}
        test_result = state.get("test_result", {}) or {}
        previous_code = state.get("fix_diff", "")

        prompt = FIXER_SELF_CORRECT_PROMPT.format(
            iteration=fix_iterations + 1,
            lint_output=lint_result.get("output", "无"),
            test_output=test_result.get("output", "无"),
            previous_code=previous_code or "无",
        )
    else:
        # 首次尝试
        prompt = FIXER_WRITE_CODE_PROMPT.format(
            issue_title=issue_title,
            issue_body=issue_body,
            solution=solution,
            language=language,
            related_files="(待补充)",
            previous_errors="",
        )

    response = await call_llm(FIXER_SYSTEM_PROMPT, [{"role": "user", "content": prompt}])

    return {
        "fix_diff": response,
        "fix_iterations": fix_iterations + 1,
    }


@traceable(name="fixer_run_lint")
async def run_lint_node(state: SwarmState) -> dict:
    """运行 lint 检查"""
    project_info = state.get("project_info", {}) or {}
    language = project_info.get("language", "unknown")
    local_path = project_info.get("local_path", "")

    result = run_lint(local_path, language)

    return {"lint_result": result}


def check_lint(state: SwarmState) -> str:
    """
    条件路由：根据 lint 结果决定下一步。

    - lint 成功 → run_tests
    - iterations >= 3 → give_up
    - 否则 → write_code（重试）
    """
    lint_result = state.get("lint_result", {}) or {}
    fix_iterations = state.get("fix_iterations", 0)

    if lint_result.get("success"):
        return "run_tests"
    if fix_iterations >= 3:
        return "give_up"
    return "write_code"


@traceable(name="fixer_run_tests")
async def run_tests_node(state: SwarmState) -> dict:
    """运行测试"""
    project_info = state.get("project_info", {}) or {}
    language = project_info.get("language", "unknown")
    local_path = project_info.get("local_path", "")

    result = run_tests(local_path, language)

    return {"test_result": result}


def check_tests(state: SwarmState) -> str:
    """
    条件路由：根据测试结果决定下一步。

    - test 成功 → create_pr
    - iterations >= 3 → give_up
    - 否则 → write_code（重试）
    """
    test_result = state.get("test_result", {}) or {}
    fix_iterations = state.get("fix_iterations", 0)

    if test_result.get("success"):
        return "create_pr"
    if fix_iterations >= 3:
        return "give_up"
    return "write_code"


@traceable(name="fixer_create_pr")
async def create_pr_node(state: SwarmState) -> dict:
    """创建 Pull Request"""
    repo = state.get("repo", "")
    fix_branch = state.get("fix_branch", "")
    issue_details = state.get("issue_details", {}) or {}
    issue_number = issue_details.get("number", "")
    issue_title = issue_details.get("title", "修复")
    fix_diff = state.get("fix_diff", "")

    title = f"Fix #{issue_number}: {issue_title}" if issue_number else f"Auto-fix: {issue_title}"
    body = (
        f"## 自动修复\n\n"
        f"**Issue**: #{issue_number}\n\n"
        f"**修复内容**:\n{fix_diff[:2000]}\n\n"
        f"*由 The Fixer 自动生成*"
    )

    pr_result = create_pull_request(repo, title, body, head=fix_branch, base="main")

    if pr_result.get("success"):
        pr_url = pr_result.get("url", "")
        return {
            "response": f"PR 已创建: {pr_url}",
            "fix_status": "success",
        }
    else:
        return {
            "response": f"创建 PR 失败: {pr_result.get('error', '')}",
            "fix_status": "failed",
        }


@traceable(name="fixer_give_up")
async def give_up_node(state: SwarmState) -> dict:
    """超过最大重试次数，放弃修复"""
    lint_result = state.get("lint_result", {}) or {}
    test_result = state.get("test_result", {}) or {}

    detail = ""
    if not lint_result.get("success"):
        detail = f"\nLint 错误: {lint_result.get('output', '无')[:500]}"
    elif not test_result.get("success"):
        detail = f"\n测试失败: {test_result.get('output', '无')[:500]}"

    return {
        "response": f"自修正超过 3 次，建议人工介入。{detail}",
        "fix_status": "max_retries",
    }


def build_fixer_graph():
    """编译 The Fixer 子图（含 Self-Correction 循环）。"""
    graph = StateGraph(SwarmState)

    # 注册节点
    graph.add_node("prepare_repo", prepare_repo_node)
    graph.add_node("write_code", write_code_node)
    graph.add_node("run_lint", run_lint_node)
    graph.add_node("run_tests", run_tests_node)
    graph.add_node("create_pr", create_pr_node)
    graph.add_node("give_up", give_up_node)

    # 入口
    graph.set_entry_point("prepare_repo")

    # 线性边
    graph.add_edge("prepare_repo", "write_code")
    graph.add_edge("write_code", "run_lint")
    graph.add_edge("create_pr", END)
    graph.add_edge("give_up", END)

    # 条件边：lint 后路由
    graph.add_conditional_edges("run_lint", check_lint, {
        "run_tests": "run_tests",
        "write_code": "write_code",
        "give_up": "give_up",
    })

    # 条件边：test 后路由
    graph.add_conditional_edges("run_tests", check_tests, {
        "create_pr": "create_pr",
        "write_code": "write_code",
        "give_up": "give_up",
    })

    return graph.compile()
