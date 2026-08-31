"""治理闸门（govern）：循环生死与预算的全部规则。

Guard 是纯策略对象：只读状态、返回决定；不写 state、不落库、不发事件——
写回与通知由宿主循环执行（verify() 模式：判定在闸门，动作在循环）。

「什么时候停」的唯一来源：轮次、诊断预算、重规划触发、同失败熔断、阶段预算
全部集中于此，宿主循环只消费决定——模型不管理自己的生死（审计问题④）。
「拦截/分级」的纯文本规则（boundary_violation / classify_command_risk）
也收在本模块：治理规则同源，tooling.py 保留兼容别名（审计 E 期）。

关于「Harness 是否偷偷思考」（审计问题③）：本模块是纯规则、绝不调 LLM。
治理层有且仅有两处 LLM 接缝，都不在本模块、也都不替代 Agent 决策：
- 压缩摘要（compaction）：Harness 触发、LLM 生成认知内容，产物回流上下文；
- guardian 安全审查（runtime._guardian_review）：guardian 档位用一个温度 0 的
  模型做破坏性工具的「裁判」，fail-closed（超时/异常一律拒绝）。

预算参数从 runtime 活读而非构造时快照：测试可在构造后调参
（runtime.diagnostic_tool_budget = 2）。换任何 LLM，这里原样工作。
"""

from dataclasses import dataclass
import re
from typing import Any, Mapping

# 诊断类工具：只读扩散、不产生材料变更（诊断预算据此判定）
DIAGNOSTIC_TOOLS = frozenset({
    "list_directory", "read_file", "search_text", "repo_map", "detect_project",
    "get_process", "list_processes", "check_port", "wait_http",
})


# ================= 命令文本规则（E 期：治理规则的单一来源） =================
# 这些纯文本判定（正则）原先散在 tooling.py 作游离函数，与 guard 分两处。
# 现在收进本模块：治理的「拦截/分级」判定全部同源（tooling.py 保留兼容别名）。
_DESTRUCTIVE_COMMANDS = re.compile(
    r"\b(Remove-Item|rm|del|rmdir|mkfs|diskpart)\b|"
    r"^\s*format(?:\.com)?(?:\s|$)|git\s+(reset\s+--hard|clean\s+-[a-z]*f)",
    re.IGNORECASE,
)
_PRIVILEGED_COMMANDS = re.compile(
    r"\b(sudo|runas)\b|Start-Process.+-Verb\s+RunAs|\b(winget|choco)\s+install\b",
    re.IGNORECASE,
)
_EXTERNAL_COMMANDS = re.compile(
    r"\bgit\s+push\b|\bgh\s+(repo\s+create|pr\s+create|issue\s+create|release\s+create)\b|\bnpm\s+publish\b",
    re.IGNORECASE,
)

# 边界拦截（BOUNDARY）：评测完整性 + 全局环境污染。
# - 受限路径引用：判分脚本目录（checks/）与评测结果文件（results-*.jsonl）。
#   语义对齐 SWE-bench「测试对 agent 物理隐藏」——agent 在非完全访问档下不可触碰；
# - 全局工具链/系统级写入：setx（注册表持久化环境变量）、reg add、npm/pnpm/yarn -g、
#   go install（写全局 GOBIN）。这些会跨任务污染本机环境。
_BOUNDARY_REFERENCE_RE = re.compile(
    r"(?:^|[\\/])(?:checks[\\/]|results-[A-Za-z0-9_.-]*\.jsonl)",
    re.IGNORECASE,
)
_GLOBAL_WRITE_RE = re.compile(
    r"\bsetx\b|\breg\s+add\b|\b(?:npm|pnpm)\s+(?:install|i|add)\s+(?:-g\b|--global\b)|"
    r"\byarn\s+global\s+add\b|\bgo\s+install\b",
    re.IGNORECASE,
)


def boundary_violation(command: str) -> str | None:
    """检测命令是否引用工作区外受限路径或做全局工具链写入。返回违规原因或 None。"""
    if _BOUNDARY_REFERENCE_RE.search(command):
        return "命令引用受限路径（判分脚本/评测结果目录）"
    if _GLOBAL_WRITE_RE.search(command):
        return "命令执行全局工具链/系统级写入（如 setx、npm install -g、go install）"
    return None


def classify_command_risk(args: dict):
    """命令风险分级（延迟导入 ToolRisk 防环：models 是基础层）。"""
    from .models import ToolRisk

    command = str(args.get("command", ""))
    if boundary_violation(command):
        return ToolRisk.BOUNDARY
    if _DESTRUCTIVE_COMMANDS.search(command):
        return ToolRisk.DESTRUCTIVE
    if _PRIVILEGED_COMMANDS.search(command):
        return ToolRisk.PRIVILEGED
    if _EXTERNAL_COMMANDS.search(command):
        return ToolRisk.EXTERNAL
    return ToolRisk.PROCESS


@dataclass(frozen=True)
class GuardDecision:
    """闸门判定结果。stop=True 时 message 是给用户的终止文案。"""
    stop: bool
    message: str = ""


class LoopGuard:
    """宿主循环的生死与预算判定器。

    只读 state["run"]（治理集群）与 runtime 的预算参数；决策与执行分离：
    这里回答「允许吗 / 该停吗」，循环负责执行停机序列。
    """

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    # —— 轮次预算 ——
    def rounds_exhausted(self, run: Mapping) -> bool:
        limit = int(run.get("round_limit") or self._runtime.max_rounds)
        return int(run.get("round", 0)) >= limit

    # —— 诊断预算：重规划后仍只读不写 → 判死 ——
    def should_stop_repeated_diagnostics(
        self, run: Mapping, tool_uses: list,
    ) -> GuardDecision | None:
        if not run.get("replanned"):
            return None
        if run.get("material_tool_seen"):
            return None
        if int(run.get("diagnostic_unique_count", 0)) < self._runtime.diagnostic_tool_budget:
            return None
        if not tool_uses or not all(t["name"] in DIAGNOSTIC_TOOLS for t in tool_uses):
            return None
        # 重规划后宽限一轮：修复型任务常需最后一两次确认读取，
        # 立即判死太急（fx11-13 fusion 连续三轮卡在这里）
        if int(run.get("round", 0)) <= int(run.get("replan_round", 0)) + 1:
            return None
        return GuardDecision(
            stop=True,
            message=(
                "诊断预算已用尽，重规划后仍只请求诊断工具；"
                "Harness 已停止继续扩散读取，本次任务未完成。"
            ),
        )

    # —— 诊断预算：达到上限且尚无重规划 → 触发重规划 ——
    def replan_needed(self, run: Mapping) -> bool:
        count = int(run.get("diagnostic_unique_count", 0))
        if run.get("replanned") or run.get("material_tool_seen"):
            return False
        return count >= self._runtime.diagnostic_tool_budget

    # —— 同一签名重复失败熔断 ——
    def failure_over_budget(self, run: Mapping, signature: str) -> bool:
        counts = run.get("failure_counts") or {}
        return int(counts.get(signature, 0)) >= self._runtime.max_identical_failures

    # —— 阶段预算熔断：某个语义阶段（inspect/implement/test/run）耗尽即停 ——
    def stage_budget_exhausted(self, budget: Mapping) -> bool:
        return int(budget.get("used", 0)) >= int(budget.get("limit", 0))
