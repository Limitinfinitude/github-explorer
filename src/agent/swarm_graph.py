"""
Multi-Agent Swarm 主图 — 协调器 + 5 个子智能体

架构：
  coordinator(分类路由)
    ├─ repo_hunter       子图: 搜索+健康度+学习价值
    ├─ architect_analyst  子图: 文件树+Mermaid图+设计模式
    ├─ issue_strategist   子图: Issue分析+痛点+3方案
    ├─ the_fixer          子图: 写码→Lint→Test→自修正循环→PR
    ├─ devops_guardian    子图: CI/CD状态+Actions分析
    ├─ chat/analyze/execute (旧路径，向后兼容)
    └─ → aggregator(汇总) → END
"""
import re

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langsmith import traceable

from .swarm_state import SwarmState
from .llm import call_llm_json
from .prompts import SWARM_CLASSIFY_PROMPT
from .nodes import chat_node, analyze_node, request_confirm_node, execute_node
from .subgraphs import (
    build_repo_hunter_graph,
    build_architect_graph,
    build_issue_graph,
    build_fixer_graph,
    build_devops_graph,
)


# ========== Coordinator 节点 ==========


_AGENT_NAMES = {
    "hunt": "Repo Hunter（探索者）",
    "architect": "Architectural Analyst（讲解员）",
    "issue": "Issue Strategist（头脑风暴员）",
    "fix": "The Fixer（代码研究员）",
    "devops": "DevOps Guardian（部署守卫）",
    "swarm": "Swarm（多智能体协作）",
    "chat": "Chat（对话）",
    "analyze": "Analyze（分析）",
    "execute": "Execute（执行）",
}

_SWARM_INTENTS = {"hunt", "architect", "issue", "fix", "devops", "swarm"}


@traceable(name="swarm_coordinator")
async def coordinator_node(state: SwarmState) -> dict:
    """扩展版 classify，支持 9 种意图路由"""
    valid_intents = {
        "chat", "analyze", "execute",
        "hunt", "architect", "issue", "fix", "devops", "swarm",
    }
    # 如果外部已指定合法意图，跳过分类
    if state.get("intent") in valid_intents:
        return {}

    user_msg = state.get("user_message", "")
    user_lower = user_msg.lower()

    # 规则预判（快速路径）— 长关键词优先，短关键词用词边界匹配
    hunt_kw = ["搜索", "推荐", "发现", "趋势", "评估", "健康度", "值得学", "热门",
                "search", "trending", "discover", "explore", "find repos", "top repos"]
    arch_kw = ["架构", "结构", "设计模式", "模块", "UML", "Mermaid", "流程图",
               "architecture", "structure", "design pattern", "diagram"]
    issue_kw = ["Issue", "issue", "问题", "痛点", "讨论", "方案",
                "pain point", "discuss"]
    # 短关键词需要词边界匹配，避免 "pr" 匹配 "project"
    fix_kw_long = ["修复", "Fix", "Bug", "bug", "代码修改", "repair", "patch"]
    fix_kw_short = {"pr": r"\bpr\b", "fix": r"\bfix\b"}
    devops_kw = ["CI", "ci", "CD", "cd", "Actions", "actions", "部署", "Workflow", "workflow", "构建",
                 "deploy", "pipeline"]
    swarm_kw = ["全面分析", "深度分析", "完整分析", "全方位",
                "full analysis", "comprehensive", "deep dive"]

    intent = None
    if any(k in user_msg for k in swarm_kw):
        intent = "swarm"
    elif (any(k in user_msg for k in fix_kw_long) or
          any(re.search(p, user_lower) for p in fix_kw_short.values())) and state.get("repo"):
        intent = "fix"
    elif any(k in user_msg for k in issue_kw) and state.get("repo"):
        intent = "issue"
    elif any(k in user_msg for k in arch_kw) and state.get("repo"):
        intent = "architect"
    elif any(k in user_msg for k in devops_kw) and state.get("repo"):
        intent = "devops"
    elif any(k in user_msg for k in hunt_kw):
        intent = "hunt"

    # LLM 兜底分类
    if not intent:
        try:
            result = await call_llm_json(
                SWARM_CLASSIFY_PROMPT,
                [{"role": "user", "content": user_msg}],
                max_tokens=100,
                temperature=0.1,
            )
            candidate = result.get("intent", "chat")
            if candidate in valid_intents:
                intent = candidate
        except Exception:
            pass

    if not intent:
        intent = "chat"

    return {"intent": intent}


# ========== 条件路由 ==========


def coordinator_route(state: SwarmState) -> str:
    """coordinator 之后路由到子智能体或旧路径"""
    intent = state.get("intent", "chat")
    route_map = {
        "hunt": "repo_hunter",
        "architect": "architectural_analyst",
        "issue": "issue_strategist",
        "fix": "the_fixer",
        "devops": "devops_guardian",
        "swarm": "repo_hunter",  # 全流程从 hunter 开始
        "chat": "chat",
        "analyze": "analyze",
        "execute": "request_confirm",
    }
    return route_map.get(intent, "chat")


def after_hunter_route(state: SwarmState) -> str:
    """hunter 完成后，如果是 swarm 模式则继续到 architect"""
    if state.get("intent") == "swarm" and state.get("repo"):
        return "architectural_analyst"
    return "aggregator"


def after_architect_route(state: SwarmState) -> str:
    """architect 完成后，如果是 swarm 模式且有 issue 则继续"""
    if state.get("intent") == "swarm":
        # swarm 模式下继续到 devops
        return "devops_guardian"
    return "aggregator"


def route_confirm(state: SwarmState) -> str:
    """confirm 之后路由"""
    if state.get("confirmed"):
        return "execute"
    return "aggregator"


# ========== Aggregator 节点 ==========


@traceable(name="swarm_aggregator")
async def aggregator_node(state: SwarmState) -> dict:
    """汇总所有子智能体结果，生成最终回复"""
    response = state.get("response", "")
    intent = state.get("intent", "chat")

    # 根据 intent 推断使用的智能体
    agent_name = _AGENT_NAMES.get(intent, intent)
    is_swarm = intent in _SWARM_INTENTS

    if is_swarm:
        active_agents = [agent_name]
        agent_results = [{"agent": agent_name, "status": "已完成"}]

        # swarm 模式下记录链式调用的智能体
        if intent == "swarm":
            for name in ["Repo Hunter", "Architectural Analyst", "DevOps Guardian"]:
                if name not in [a.split("（")[0] for a in active_agents]:
                    active_agents.append(f"{name}（协作）")
                    agent_results.append({"agent": f"{name}（协作）", "status": "已完成"})
    else:
        active_agents = [agent_name]
        agent_results = [{"agent": agent_name, "status": "已完成"}]

    if response:
        return {
            "response": response,
            "active_agents": active_agents,
            "agent_results": agent_results,
        }

    # 兜底回复
    return {
        "response": "任务已完成。如需进一步操作，请告诉我。",
        "active_agents": active_agents,
        "agent_results": agent_results,
    }


# ========== 构建图 ==========


def _build_swarm_graph() -> StateGraph:
    """构建 Multi-Agent Swarm 主图"""
    graph = StateGraph(SwarmState)

    # --- 协调器 ---
    graph.add_node("coordinator", coordinator_node)

    # --- 5 个子智能体（编译后的子图作为节点）---
    graph.add_node("repo_hunter", build_repo_hunter_graph())
    graph.add_node("architectural_analyst", build_architect_graph())
    graph.add_node("issue_strategist", build_issue_graph())
    graph.add_node("the_fixer", build_fixer_graph())
    graph.add_node("devops_guardian", build_devops_graph())

    # --- 向后兼容的旧节点 ---
    graph.add_node("chat", chat_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("request_confirm", request_confirm_node)
    graph.add_node("execute", execute_node)

    # --- 汇总节点 ---
    graph.add_node("aggregator", aggregator_node)

    # --- 入口 ---
    graph.set_entry_point("coordinator")

    # --- coordinator 条件路由 ---
    graph.add_conditional_edges(
        "coordinator",
        coordinator_route,
        {
            "repo_hunter": "repo_hunter",
            "architectural_analyst": "architectural_analyst",
            "issue_strategist": "issue_strategist",
            "the_fixer": "the_fixer",
            "devops_guardian": "devops_guardian",
            "chat": "chat",
            "analyze": "analyze",
            "request_confirm": "request_confirm",
        },
    )

    # --- 子智能体完成后 → aggregator（或 swarm 链式调用）---
    graph.add_conditional_edges("repo_hunter", after_hunter_route, {
        "architectural_analyst": "architectural_analyst",
        "aggregator": "aggregator",
    })
    graph.add_conditional_edges("architectural_analyst", after_architect_route, {
        "devops_guardian": "devops_guardian",
        "aggregator": "aggregator",
    })
    graph.add_edge("issue_strategist", "aggregator")
    graph.add_edge("the_fixer", "aggregator")
    graph.add_edge("devops_guardian", "aggregator")

    # --- 旧路径 → aggregator ---
    graph.add_edge("chat", "aggregator")
    graph.add_edge("analyze", "aggregator")
    graph.add_conditional_edges("request_confirm", route_confirm, {
        "execute": "execute",
        "aggregator": "aggregator",
    })
    graph.add_edge("execute", "aggregator")

    # --- aggregator → END ---
    graph.add_edge("aggregator", END)

    return graph


# ========== 延迟初始化 ==========

_compiled_swarm = None


async def get_swarm_graph():
    """获取编译后的 Swarm 图实例（延迟初始化）"""
    global _compiled_swarm
    if _compiled_swarm is not None:
        return _compiled_swarm

    checkpointer = MemorySaver()

    builder = _build_swarm_graph()
    _compiled_swarm = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["request_confirm", "the_fixer"],
    )
    return _compiled_swarm
