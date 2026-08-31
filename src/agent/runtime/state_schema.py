"""任务状态分区契约（审计问题⑤：认知状态 vs 运行状态 vs 环境引用）。

state 是落 SQLite 的持久化对象。历史上它是无 schema 的扁平 dict，
认知字段（模型看到什么）与治理字段（生死/预算/重试）混装，是典型的超级对象。
本模块把它拆成四个显式分区：

- ENVELOPE_STATE_KEYS    任务信封：对外契约，task store / API / supervisor 直接读写
- COGNITION_STATE_KEYS   认知状态：模型看到什么、想什么——decide 的输入与产物
- RUNTIME_STATE_KEYS     运行状态：生死、预算、重试、审批流、账目——govern 拥有
- ENVIRONMENT_STATE_KEYS 环境引用：世界的只读投影

运行状态集群进一步物化为 state["run"]（RunState 的字典形态），由
normalize_state 负责旧扁平格式的迁移（幂等）。认知与环境字段保持扁平：
messages / plan 被 events、resume、子代理广泛消费，扁平形态是它们的对外契约；
分区本身由 test_state_schema 强制——任何新键必须先归类才能进 state。
"""

from dataclasses import dataclass, field, asdict

ENVELOPE_STATE_KEYS = frozenset({
    "task_id", "session_id", "user_message", "status", "final_text",
    "workspace_root", "current_path", "summary",
})

COGNITION_STATE_KEYS = frozenset({
    "messages", "plan", "plan_mode", "acceptance_criteria",
    "session_requirements", "requirement_context", "implicit_requirement_positions",
    "model_context", "instruction_context", "instruction_sources", "instruction_warnings",
    "project_memories", "context_handoff", "step_back",
})

RUNTIME_STATE_KEYS = frozenset({
    "round", "round_limit", "allow_tools", "approval_mode",
    "tool_call_ledger", "active_batch", "tokens_scale",
    "diagnostic_tool_count", "diagnostic_unique_count", "diagnostic_observations",
    "material_tool_seen", "requires_material_change",
    "replanned", "replan_round", "search_fetch_count",
    "failure_counts", "schema_repair_counts", "unrecovered_failures",
    "guardian_denials", "compaction_count", "compacted_message_count",
    "force_compact", "plan_gate_resolved", "context_emitted",
    "resume_available", "resume_count", "resume_reason",
    "_hook_session_started",
})

ENVIRONMENT_STATE_KEYS = frozenset({
    "repo_map", "workspace_snapshot",
})


@dataclass
class RunState:
    """治理集群的默认值表——state["run"] 的 schema 与唯一合法键集。"""

    round: int = 0
    round_limit: int = 12
    allow_tools: bool = True
    approval_mode: str = "confirm"
    tool_call_ledger: dict = field(default_factory=dict)
    active_batch: dict | None = None
    tokens_scale: float = 1.0
    diagnostic_tool_count: int = 0
    diagnostic_unique_count: int = 0
    diagnostic_observations: list = field(default_factory=list)
    material_tool_seen: bool = False
    requires_material_change: bool = False
    replanned: bool = False
    replan_round: int = 0
    search_fetch_count: int = 0
    failure_counts: dict = field(default_factory=dict)
    schema_repair_counts: dict = field(default_factory=dict)
    unrecovered_failures: dict = field(default_factory=dict)
    guardian_denials: int = 0
    compaction_count: int = 0
    compacted_message_count: int = 0
    force_compact: bool = False
    plan_gate_resolved: bool = False
    context_emitted: bool = False
    resume_available: bool = False
    resume_count: int = 0
    resume_reason: str = ""
    _hook_session_started: bool = False


def normalize_state(state: dict) -> dict:
    """把旧扁平的 run 集群字段装进 state["run"] 并补齐默认键。幂等，就地修改。

    兼容三类输入：新格式（已有 run）、旧持久化行（扁平）、最小 dict
    （supervisor 异常兜底构造的残缺 state）。
    """
    run = state.get("run")
    if not isinstance(run, dict):
        run = {}
    for key in RUNTIME_STATE_KEYS:
        if key in state:
            run.setdefault(key, state.pop(key))
    for key, value in asdict(RunState()).items():
        run.setdefault(key, value)
    state["run"] = run
    return state
