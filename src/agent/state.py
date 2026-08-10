"""
Agent 状态定义 — LangGraph StateGraph 的核心类型
"""
import operator
from typing import Annotated, TypedDict, Literal, Optional
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """主图状态"""
    # 对话消息（langgraph 自动追加）
    messages: Annotated[list, add_messages]
    # 当前用户输入
    user_message: str
    # 会话 ID（用于记忆和检查点）
    session_id: str
    # 当前关联的仓库 (owner/name)
    repo: Optional[str]
    # classify 节点输出的意图
    intent: Literal["chat", "analyze", "execute"]
    # 项目元数据（从 GitHub 获取或本地检测）
    project_info: Optional[dict]
    # 执行步骤日志（累积追加）
    execution_steps: Annotated[list, operator.add]
    # 分析结果
    analysis_result: Optional[dict]
    # 是否需要人工确认
    needs_confirm: bool
    # 确认问题文本
    confirm_question: str
    # 用户是否已确认
    confirmed: bool
    # 最终回复文本
    response: str
