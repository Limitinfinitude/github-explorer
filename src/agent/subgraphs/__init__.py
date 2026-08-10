"""
Multi-Agent Swarm 子图模块

5 个专业子智能体：
- repo_hunter: 项目探索与健康度评估
- architectural_analyst: 代码架构分析
- issue_strategist: Issue 分析与方案
- the_fixer: 代码修复（含自修正循环）
- devops_guardian: CI/CD 状态检查
"""

from .repo_hunter import build_repo_hunter_graph
from .architectural_analyst import build_architect_graph
from .issue_strategist import build_issue_graph
from .the_fixer import build_fixer_graph
from .devops_guardian import build_devops_graph

__all__ = [
    "build_repo_hunter_graph",
    "build_architect_graph",
    "build_issue_graph",
    "build_fixer_graph",
    "build_devops_graph",
]
