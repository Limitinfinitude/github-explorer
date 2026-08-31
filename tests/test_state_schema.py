"""State 分区契约与治理闸门的守卫测试。

固化架构审计的三个结论：
⑤ state 四分区（信封/认知/运行/环境）——任何键必须归类，治理集群物化为 state["run"]；
④ 生死判定只存在于 LoopGuard（宿主循环消费决定，模型不管理自己的生死）；
③ guard 是纯策略——只有判定，没有 LLM、没有 I/O。
"""
import pytest
from dataclasses import asdict

from agent.memory import Memory
from agent.runtime.guard import DIAGNOSTIC_TOOLS, LoopGuard
from agent.runtime.models import ToolRisk, ToolResult
from agent.runtime.registry import ToolRegistry
from agent.runtime.runtime import LocalAgentRuntime
from agent.runtime.state_schema import (
    COGNITION_STATE_KEYS,
    ENVELOPE_STATE_KEYS,
    ENVIRONMENT_STATE_KEYS,
    RUNTIME_STATE_KEYS,
    RunState,
    normalize_state,
)
from agent.runtime.workspace import WorkspaceManager


# ---------- 分区契约 ----------

def test_partitions_are_disjoint_and_cover_runstate():
    groups = [ENVELOPE_STATE_KEYS, COGNITION_STATE_KEYS, RUNTIME_STATE_KEYS, ENVIRONMENT_STATE_KEYS]
    union = set().union(*groups)
    assert sum(len(g) for g in groups) == len(union), "分区之间有重叠"
    run_keys = set(asdict(RunState()))
    assert run_keys <= RUNTIME_STATE_KEYS, "RunState 字段必须全部属于运行分区"
    assert run_keys <= union


def test_normalize_state_migrates_legacy_flat_state():
    legacy = {
        "task_id": "t1", "session_id": "s1", "status": "interrupted",
        "round": 3, "round_limit": 12, "resume_available": True,
        "resume_count": 1, "resume_reason": "stream_closed",
        "tool_call_ledger": {"c1": {}}, "active_batch": None,
        "allow_tools": True, "tokens_scale": 1.2,
        "messages": [{"role": "user", "content": "hi"}], "plan": ["a"],
    }
    state = normalize_state(dict(legacy))
    run = state["run"]
    assert run["round"] == 3
    assert run["resume_available"] is True
    assert run["resume_reason"] == "stream_closed"
    assert run["tokens_scale"] == 1.2
    assert "round" not in state and "resume_available" not in state
    # 认知与信封键保持扁平
    assert state["messages"] == legacy["messages"]
    assert state["plan"] == ["a"]
    assert state["status"] == "interrupted"
    # 幂等
    assert normalize_state(state) == state


def test_normalize_state_fills_defaults_for_minimal_dict():
    state = normalize_state({"task_id": "t", "session_id": "s", "status": "running"})
    assert state["run"]["round"] == 0
    assert state["run"]["failure_counts"] == {}
    assert state["run"]["_hook_session_started"] is False


# ---------- 运行时产生的 state 符合分区 ----------

def test_runtime_state_keys_are_all_classified(tmp_path):
    async def fake_llm(**kwargs):
        return {"text": "已收到任务。", "tool_uses": [], "stop_reason": "end_turn"}

    root = tmp_path / "project"
    root.mkdir()
    workspaces = WorkspaceManager()
    workspaces.bind("session", root)
    store = Memory(tmp_path / "state.db")
    runtime = LocalAgentRuntime(workspaces, lambda _: ToolRegistry(), fake_llm, task_store=store)

    task_id = "schema-task"

    async def _run():
        return [e async for e in runtime.run("session", "完成一个小任务", task_id=task_id)]

    import asyncio
    events = asyncio.run(_run())
    assert events[-1]["type"] == "done"

    state = store.get_agent_task(task_id)
    classified = ENVELOPE_STATE_KEYS | COGNITION_STATE_KEYS | RUNTIME_STATE_KEYS | ENVIRONMENT_STATE_KEYS
    unknown = set(state) - classified - {"run"}
    assert not unknown, f"出现未归类键: {unknown}"
    assert set(state["run"]) == set(asdict(RunState()))
    assert state["run"]["round_limit"] == runtime.max_rounds
    assert state["run"]["allow_tools"] is True


# ---------- LoopGuard：生死与预算的判定 ----------

class _FakeRuntime:
    def __init__(self, max_rounds=12, diagnostic_tool_budget=28, max_identical_failures=3):
        self.max_rounds = max_rounds
        self.diagnostic_tool_budget = diagnostic_tool_budget
        self.max_identical_failures = max_identical_failures


def _run(**overrides):
    base = asdict(RunState())
    base.update(overrides)
    return base


def test_guard_rounds_exhausted_reads_live_budget():
    rt = _FakeRuntime(max_rounds=5)
    guard = LoopGuard(rt)
    assert not guard.rounds_exhausted(_run(round=4, round_limit=5))
    assert guard.rounds_exhausted(_run(round=5, round_limit=5))
    # 活读：round_limit 缺失时回退 runtime 当前值（测试可运行时调参）
    rt.max_rounds = 3
    assert guard.rounds_exhausted(_run(round=3, round_limit=0))


def test_guard_stops_only_after_replan_grace_round():
    rt = _FakeRuntime(diagnostic_tool_budget=2)
    guard = LoopGuard(rt)
    read_tools = [{"name": "read_file", "input": {}}]
    # 未重规划 → 不判死（走重规划路径）
    assert guard.should_stop_repeated_diagnostics(
        _run(diagnostic_unique_count=5, replanned=False, round=3), read_tools) is None
    # 重规划后的宽限轮（round == replan_round + 1）→ 不判死
    assert guard.should_stop_repeated_diagnostics(
        _run(diagnostic_unique_count=5, replanned=True, replan_round=2, round=3), read_tools) is None
    # 宽限轮过后仍只读 → 判死
    decision = guard.should_stop_repeated_diagnostics(
        _run(diagnostic_unique_count=5, replanned=True, replan_round=2, round=4), read_tools)
    assert decision is not None and decision.stop
    assert "诊断预算" in decision.message
    # 出现材料性工具 → 不判死
    assert guard.should_stop_repeated_diagnostics(
        _run(diagnostic_unique_count=5, replanned=True, material_tool_seen=True, round=9), read_tools) is None
    # 请求里有非诊断工具 → 不判死
    assert guard.should_stop_repeated_diagnostics(
        _run(diagnostic_unique_count=5, replanned=True, round=9),
        [{"name": "edit_files", "input": {}}]) is None


def test_guard_replan_needed_and_failure_budget():
    rt = _FakeRuntime(diagnostic_tool_budget=3, max_identical_failures=2)
    guard = LoopGuard(rt)
    assert guard.replan_needed(_run(diagnostic_unique_count=3))
    assert not guard.replan_needed(_run(diagnostic_unique_count=2))
    assert not guard.replan_needed(_run(diagnostic_unique_count=9, replanned=True))
    assert not guard.replan_needed(_run(diagnostic_unique_count=9, material_tool_seen=True))

    run = _run(failure_counts={"sig": 1})
    assert not guard.failure_over_budget(run, "sig")
    run["failure_counts"] = {"sig": 2}
    assert guard.failure_over_budget(run, "sig")
    assert guard.failure_over_budget(run, "other") is False


def test_diagnostic_tools_are_read_only_set():
    assert "repo_map" in DIAGNOSTIC_TOOLS and "read_file" in DIAGNOSTIC_TOOLS
    for mutating in ("edit_files", "run_command", "create_directory", "clone_repository"):
        assert mutating not in DIAGNOSTIC_TOOLS


def test_guard_stage_budget_exhausted_reads_used_vs_limit():
    rt = _FakeRuntime()
    guard = LoopGuard(rt)
    assert not guard.stage_budget_exhausted({"used": 7, "limit": 8})
    assert guard.stage_budget_exhausted({"used": 8, "limit": 8})
    assert guard.stage_budget_exhausted({"used": 9, "limit": 8})
