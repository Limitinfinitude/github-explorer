"""
主图定义 — LangGraph StateGraph

展示：
- StateGraph 状态机
- 条件路由 (add_conditional_edges)
- 人工确认中断 (interrupt_before)
- SQLite 检查点持久化
"""
import asyncio

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import AgentState
from .nodes import (
    classify_node,
    chat_node,
    analyze_node,
    request_confirm_node,
    execute_node,
    respond_node,
)

# ========== 条件路由函数 ==========


def route_intent(state: AgentState) -> str:
    """classify 之后，根据 intent 路由到不同节点"""
    intent = state.get("intent", "chat")
    if intent == "analyze":
        return "analyze"
    if intent == "execute":
        return "request_confirm"
    return "chat"


def route_confirm(state: AgentState) -> str:
    """confirm 之后，根据用户选择路由"""
    if state.get("confirmed"):
        return "execute"
    return "respond"


# ========== 构建图 ==========


def _build_graph() -> StateGraph:
    """构建 StateGraph（不含编译）"""
    graph = StateGraph(AgentState)

    # 添加节点
    graph.add_node("classify", classify_node)
    graph.add_node("chat", chat_node)
    graph.add_node("analyze", analyze_node)
    graph.add_node("request_confirm", request_confirm_node)
    graph.add_node("execute", execute_node)
    graph.add_node("respond", respond_node)

    # 入口
    graph.set_entry_point("classify")

    # 条件路由：classify → chat / analyze / request_confirm
    graph.add_conditional_edges(
        "classify",
        route_intent,
        {"chat": "chat", "analyze": "analyze", "request_confirm": "request_confirm"},
    )

    # chat → respond → END
    graph.add_edge("chat", "respond")

    # analyze → respond → END
    graph.add_edge("analyze", "respond")

    # 条件路由：request_confirm → execute / respond
    graph.add_conditional_edges(
        "request_confirm",
        route_confirm,
        {"execute": "execute", "respond": "respond"},
    )

    # execute → respond → END
    graph.add_edge("execute", "respond")

    # respond → END
    graph.add_edge("respond", END)

    return graph


# ========== 延迟初始化（AsyncSqliteSaver 需要事件循环） ==========

_compiled_graph = None


async def get_graph():
    """获取编译后的图实例（延迟初始化，首次调用时创建）"""
    global _compiled_graph
    if _compiled_graph is not None:
        return _compiled_graph

    checkpointer = MemorySaver()

    builder = _build_graph()
    _compiled_graph = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=["request_confirm"],  # 危险操作前中断
    )
    return _compiled_graph


# 为旧代码提供兼容：同步场景下用 None，实际调用走 get_graph()
graph = None
