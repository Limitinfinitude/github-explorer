"""
Multi-Agent Swarm 共享状态定义

所有子智能体共享同一个 SwarmState，每个子图只读写自己需要的字段。
LangGraph 在子图边界自动过滤状态。
"""
import operator
from typing import Annotated, Optional, Literal
from typing_extensions import TypedDict
from langgraph.graph import add_messages


class SwarmState(TypedDict):
    # === 核心字段 ===
    messages: Annotated[list, add_messages]
    user_message: str
    session_id: str
    repo: Optional[str]
    intent: Optional[str]  # chat|analyze|execute|hunt|architect|issue|fix|devops|swarm
    project_info: Optional[dict]
    execution_steps: Annotated[list, operator.add]
    response: str
    needs_confirm: bool
    confirm_question: str
    confirmed: bool

    # === 协调器字段 ===
    active_agents: Annotated[list, operator.add]   # 已调度的子智能体名称
    agent_results: Annotated[list, operator.add]   # 各子智能体结果 [{agent, status, summary}]

    # === Repo Hunter ===
    search_results: Optional[list]
    repo_health: Optional[dict]          # {stars, commit_freq, bus_factor, health_score}
    worth_learning: Optional[str]        # high|medium|low

    # === Architectural Analyst ===
    file_tree: Optional[str]
    architecture_mermaid: Optional[str]  # Mermaid 语法
    design_patterns: Optional[list]

    # === Issue Strategist ===
    issue_details: Optional[dict]
    issue_comments: Optional[list]
    pain_points: Optional[list]
    solutions: Optional[list]

    # === The Fixer ===
    fix_branch: Optional[str]
    fix_diff: Optional[str]
    lint_result: Optional[dict]
    test_result: Optional[dict]
    fix_iterations: int
    fix_status: Optional[str]            # success|failed|max_retries

    # === DevOps Guardian ===
    workflow_runs: Optional[list]
    ci_status: Optional[str]             # passing|failing|none
