"""
Issue Strategist（头脑风暴员）子图

负责分析 GitHub Issue 的痛点并提出解决方案。
流程：[START] → fetch_issue → fetch_comments → analyze_pains → propose_solutions → [END]
"""
import re

from langgraph.graph import StateGraph, END
from langsmith import traceable

from ..swarm_state import SwarmState
from ..tools import get_issue_details, get_issue_comments
from ..llm import call_llm
from ..prompts import ISSUE_STRATEGIST_PROMPT


# ========== 节点函数 ==========


@traceable(name="issue_fetch_issue")
async def fetch_issue_node(state: SwarmState) -> dict:
    """
    从 user_message 中提取 issue 编号，调用 GitHub API 获取详情。
    支持匹配 #数字 或 issue 数字 两种格式。
    """
    user_msg = state.get("user_message", "")
    repo = state.get("repo", "")

    if not repo:
        return {"issue_details": {"success": False, "error": "未指定仓库"}}

    # 正则提取 issue 编号：优先 #数字，其次 issue 数字
    match = re.search(r"#(\d+)", user_msg)
    if not match:
        match = re.search(r"issue\s*(\d+)", user_msg, re.IGNORECASE)

    if not match:
        return {"issue_details": {"success": False, "error": "未在消息中找到 Issue 编号，格式示例: #42 或 issue 42"}}

    issue_number = int(match.group(1))
    result = get_issue_details(repo, issue_number)
    return {"issue_details": result}


@traceable(name="issue_fetch_comments")
async def fetch_comments_node(state: SwarmState) -> dict:
    """获取 Issue 的所有评论。"""
    issue = state.get("issue_details", {})
    if not issue or not issue.get("success"):
        return {"issue_comments": []}

    repo = state.get("repo", "")
    issue_number = issue["number"]
    result = get_issue_comments(repo, issue_number)

    if result.get("success"):
        return {"issue_comments": result.get("comments", [])}
    return {"issue_comments": []}


@traceable(name="issue_analyze_pains")
async def analyze_pains_node(state: SwarmState) -> dict:
    """分析 Issue body + 评论，提取核心痛点。"""
    issue = state.get("issue_details", {})
    if not issue or not issue.get("success"):
        return {"pain_points": ["无法获取 Issue 详情"]}

    title = issue.get("title", "")
    body = issue.get("body", "") or "(无描述)"
    comments = state.get("issue_comments", [])

    # 格式化评论列表
    if comments:
        comments_formatted = "\n".join(
            f"- [{c['user']}] ({c['created_at'][:10]}): {c['body'][:500]}"
            for c in comments
        )
    else:
        comments_formatted = "(无评论)"

    prompt = (
        f"分析以下 Issue 讨论中的核心痛点：\n\n"
        f"Issue: {title}\n{body}\n\n"
        f"评论：\n{comments_formatted}"
    )

    response = await call_llm(
        system=ISSUE_STRATEGIST_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    # 将 LLM 的分析文本直接存入 pain_points
    return {"pain_points": [response]}


@traceable(name="issue_propose_solutions")
async def propose_solutions_node(state: SwarmState) -> dict:
    """基于痛点分析，生成 3 个解决方案。"""
    issue = state.get("issue_details", {})
    pain_points = state.get("pain_points", [])

    title = issue.get("title", "未知 Issue")
    body = issue.get("body", "") or "(无描述)"
    labels = ", ".join(issue.get("labels", []))
    pains_text = "\n".join(f"- {p}" for p in pain_points) if pain_points else "(未识别到痛点)"

    prompt = (
        f"基于以下 Issue 和痛点分析，提出 3 个解决方案：\n\n"
        f"Issue: {title}\n"
        f"标签: {labels or '无'}\n"
        f"描述:\n{body}\n\n"
        f"痛点分析:\n{pains_text}\n\n"
        f"请为每个方案提供：思路、优点、缺点、难度（低/中/高），并推荐最佳方案。"
    )

    response = await call_llm(
        system=ISSUE_STRATEGIST_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    return {
        "solutions": [response],
        "response": response,
    }


# ========== 子图编译 ==========


def build_issue_graph() -> StateGraph:
    """
    编译 Issue Strategist 子图。

    流程: START → fetch_issue → fetch_comments → analyze_pains → propose_solutions → END
    """
    graph = StateGraph(SwarmState)

    graph.add_node("fetch_issue", fetch_issue_node)
    graph.add_node("fetch_comments", fetch_comments_node)
    graph.add_node("analyze_pains", analyze_pains_node)
    graph.add_node("propose_solutions", propose_solutions_node)

    graph.set_entry_point("fetch_issue")
    graph.add_edge("fetch_issue", "fetch_comments")
    graph.add_edge("fetch_comments", "analyze_pains")
    graph.add_edge("analyze_pains", "propose_solutions")
    graph.add_edge("propose_solutions", END)

    return graph.compile()
