"""
DevOps Guardian（部署守卫）子智能体 - 负责 CI/CD 状态检查

子图流程：[START] → fetch_workflows → analyze_ci → [END]
"""
import asyncio as _asyncio

from langgraph.graph import StateGraph, END
from langsmith import traceable

from ..swarm_state import SwarmState
from ..tools import get_workflow_runs
from ..llm import call_llm
from ..prompts import DEVOPS_GUARDIAN_PROMPT


@traceable(name="devops_fetch_workflows")
async def fetch_workflows_node(state: SwarmState) -> dict:
    """获取 GitHub Actions workflow 运行记录"""
    repo = state.get("repo", "")
    if not repo:
        return {"workflow_runs": []}

    result = await _asyncio.to_thread(get_workflow_runs, repo, 10)
    runs = result.get("runs", []) if isinstance(result, dict) else []
    return {"workflow_runs": runs}


@traceable(name="devops_analyze_ci")
async def analyze_ci_node(state: SwarmState) -> dict:
    """
    分析 CI/CD 运行记录，生成报告。

    - 将 workflow_runs 格式化为文本列表
    - 调用 LLM 分析并生成报告
    - 根据 conclusion 判断 ci_status
    """
    runs = state.get("workflow_runs", [])

    # 判断 ci_status
    if not runs:
        ci_status = "none"
    elif any(r.get("conclusion") == "failure" for r in runs):
        ci_status = "failing"
    else:
        ci_status = "passing"

    # 格式化 runs 为文本
    if not runs:
        runs_formatted = "（无 workflow 运行记录）"
    else:
        lines = []
        for i, run in enumerate(runs, 1):
            name = run.get("name", "unknown")
            status = run.get("status", "unknown")
            conclusion = run.get("conclusion", "N/A")
            created = run.get("created_at", "N/A")
            lines.append(
                f"{i}. {name} | 状态: {status} | "
                f"结论: {conclusion} | 创建时间: {created}"
            )
        runs_formatted = "\n".join(lines)

    prompt = f"分析以下 GitHub Actions 运行记录：\n\n{runs_formatted}"

    response = await call_llm(
        DEVOPS_GUARDIAN_PROMPT,
        [{"role": "user", "content": prompt}],
    )

    return {
        "ci_status": ci_status,
        "response": response,
    }


def build_devops_graph():
    """编译 DevOps Guardian 子图。"""
    graph = StateGraph(SwarmState)

    graph.add_node("fetch_workflows", fetch_workflows_node)
    graph.add_node("analyze_ci", analyze_ci_node)

    graph.set_entry_point("fetch_workflows")
    graph.add_edge("fetch_workflows", "analyze_ci")
    graph.add_edge("analyze_ci", END)

    return graph.compile()
