"""
agent.tools 包 — 从各子模块 re-export 所有公共函数，
确保 `from agent.tools import run_command` 等现有 import 不会 break。
"""
from .runner import run_command, run_command_stream, classify_command_risk
from .github_api import (
    clone_repo,
    fetch_repo_info,
    search_github,
    get_file_tree,
    read_file_content,
    get_issue_details,
    get_issue_comments,
    get_commit_frequency,
    get_workflow_runs,
    create_branch,
    commit_file,
    create_pull_request,
)
from .project import (
    detect_project,
    install_deps,
    get_run_command,
    run_lint,
    run_tests,
    get_system_info,
)
from .web import web_search, web_fetch, code_search
from .mcp import (
    mcp_search_repos,
    mcp_search_code,
    mcp_get_file_contents,
    mcp_list_issues,
    mcp_search_issues,
    mcp_web_fetch,
    get_mcp_tools_info,
)

__all__ = [
    # runner
    "run_command",
    "run_command_stream",
    "classify_command_risk",
    # github_api
    "clone_repo",
    "fetch_repo_info",
    "search_github",
    "get_file_tree",
    "read_file_content",
    "get_issue_details",
    "get_issue_comments",
    "get_commit_frequency",
    "get_workflow_runs",
    "create_branch",
    "commit_file",
    "create_pull_request",
    # project
    "detect_project",
    "install_deps",
    "get_run_command",
    "run_lint",
    "run_tests",
    "get_system_info",
    # web
    "web_search",
    "web_fetch",
    "code_search",
    # mcp
    "mcp_search_repos",
    "mcp_search_code",
    "mcp_get_file_contents",
    "mcp_list_issues",
    "mcp_search_issues",
    "mcp_web_fetch",
    "get_mcp_tools_info",
]
