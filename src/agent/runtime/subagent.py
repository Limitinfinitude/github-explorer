"""子智能体执行器：主代理通过 spawn_subagent 委托聚焦任务。

委托声明对齐 deepseek-harness 研究结论（DSH-提示词架构-深度研究.md §6.1）：
- 权限范围固定不可扩大：只能使用显式委托的工具白名单；
- 工具被拒绝（权限门要求确认）时不重试，在结论里说明限制让主代理处理；
- 结果以摘要形式交回主代理，不共享内部消息与思考过程。
"""

import asyncio
import json
import uuid
from pathlib import Path

from .models import ToolResult
from .state_schema import normalize_state

SUBAGENT_SYSTEM = """你是一个被委托的子代理，由主代理分配聚焦任务。你无法直接与用户交流，结论由主代理转达。
- 语言策略：内部思考一律用英文（think in English）；给主代理的结论用中文。
- 任务：{task}
- 权限范围固定不可扩大：你只能使用以下工具：{tools}。未列出的工具不存在于你的工具集。
- 只能在主代理的工作区（{workspace}）内活动，不得触碰工作区外路径。
- 工具调用被拒绝（需要确认或权限不足）时不要重试，在最终回复里说明限制让主代理处理。
- 只输出任务结论：先一句结论，再最多 3 条要点（引用关键文件路径或验证结果）；不要复述过程、不要输出计划。"""

# 未指定工具白名单时的只读默认集：不产生副作用，适合信息收集类委托
DEFAULT_SUBAGENT_TOOLS = (
    "list_directory", "read_file", "search_text", "repo_map",
    "detect_project", "web_fetch", "use_skill", "get_process", "check_port",
)

MAX_SUBAGENT_ROUNDS = 8
MAX_SUBAGENT_TOOL_CALLS = 12
SUBAGENT_OUTPUT_TOKENS = 4000
SUBAGENT_RESULT_CHARS = 2000
# 并行 fan-out 的并发上限与单次派发的任务数上限（防失控爆炸）
MAX_FANOUT_CONCURRENCY = 4
MAX_FANOUT_TASKS = 8


def subagent_system_prompt(task: str, tools: list[str], workspace_root: str) -> str:
    return SUBAGENT_SYSTEM.format(
        task=task,
        tools="、".join(tools) if tools else "（无，直接回答）",
        workspace=workspace_root,
    )


async def _execute_tool(registry, name: str, args: dict, timeout: float = 120) -> ToolResult:
    """执行子代理工具调用：与主循环相同地路由 async/sync handler。"""
    if registry.has_async_handler(name):
        return await asyncio.wait_for(
            registry.execute_async(name, args),
            timeout=timeout,
        )
    return await asyncio.wait_for(
        asyncio.to_thread(registry.execute, name, args),
        timeout=timeout,
    )


async def run_subagent(
    runtime,
    state: dict,
    registry,
    task: str,
    allowed_tools: list[str] | None = None,
    max_rounds: int = MAX_SUBAGENT_ROUNDS,
    max_tool_calls: int = MAX_SUBAGENT_TOOL_CALLS,
) -> ToolResult:
    """运行一个子代理并回收结论。state 复用主代理的模型绑定与事件记账。

    预算（轮次/工具调用次数）由宿主 runtime 注入——治理参数不写死在子循环里，
    与主循环同一条治理链路（测试可在 runtime 上调参）。
    """
    # 入口归一化：委托方可能给最小 state（无治理集群），_call_model 等依赖 state["run"]
    normalize_state(state)
    tools = list(allowed_tools or DEFAULT_SUBAGENT_TOOLS)
    registered_names = {
        item["name"] for item in registry.schemas()
    }
    tools = [name for name in tools if name in registered_names]
    tool_schemas = [
        item for item in registry.schemas() if item["name"] in tools
    ]
    subagent_id = uuid.uuid4().hex[:10]
    workspace_root = str(state.get("workspace_root", ""))
    system = subagent_system_prompt(task, tools, workspace_root)
    messages: list[dict] = [{"role": "user", "content": task}]

    runtime._record_event({
        "session_id": state["session_id"],
        "task_id": state["task_id"],
        "type": "subagent_started",
        "subagent_id": subagent_id,
        "task": task[:500],
        "tools": tools,
    })

    rounds = 0
    tool_calls = 0
    final_text = ""
    budget_exhausted = False
    try:
        while rounds < max_rounds:
            rounds += 1
            response = await runtime._call_model(
                state,
                "subagent",
                system=system,
                messages=messages,
                tools=[] if budget_exhausted else tool_schemas,
                max_tokens=SUBAGENT_OUTPUT_TOKENS,
                temperature=0.2,
            )
            tool_uses = response.get("tool_uses") or []
            if not tool_uses:
                final_text = str(response.get("text") or "")
                break
            if budget_exhausted:
                # 预算用尽后的模型调用仍给出 tool_use：直接采纳文本部分
                final_text = str(response.get("text") or "") or final_text
                break
            assistant_message = {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": use["id"], "name": use["name"], "input": use["input"]}
                    for use in tool_uses
                ],
            }
            result_blocks = []
            for use in tool_uses:
                name = str(use.get("name") or "")
                args = use.get("input") or {}
                call_id = str(use.get("id") or "")
                if tool_calls >= max_tool_calls:
                    result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": json.dumps({
                            "success": False,
                            "error": "子代理工具调用预算已用尽",
                            "error_kind": "subagent_budget",
                        }, ensure_ascii=False),
                    })
                    continue
                tool_calls += 1
                if name not in tools:
                    # 白名单之外：委托声明语义——权限范围固定不可扩大
                    result = ToolResult.fail(
                        f"子代理无权使用工具 {name}（不在委托白名单内）",
                        error_kind="rejected",
                    )
                else:
                    try:
                        result = await _execute_tool(registry, name, args)
                    except asyncio.TimeoutError:
                        result = ToolResult.fail(f"子代理工具超时: {name}", error_kind="timeout")
                    except Exception as exc:
                        result = ToolResult.fail(f"子代理工具异常: {exc}", error_kind="tool_error")
                if result.requires_confirmation:
                    # 权限门拒绝：按委托声明不重试，如实说明
                    result = ToolResult.fail(
                        f"操作需要用户确认，子代理不执行（{result.confirmation_reason or '权限不足'}）；"
                        "请在结论中说明限制让主代理处理。",
                        error_kind="rejected",
                    )
                result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": json.dumps(result.to_dict(), ensure_ascii=False, default=str),
                })
            messages.append(assistant_message)
            messages.append({"role": "user", "content": result_blocks})
            if tool_calls >= max_tool_calls:
                budget_exhausted = True
                messages.append({
                    "role": "user",
                    "content": "工具调用预算已用尽。直接给出你的结论（说明已完成与受限之处）。",
                })
        if not final_text:
            final_text = "（子代理未产出结论）"
    finally:
        runtime._record_event({
            "session_id": state["session_id"],
            "task_id": state["task_id"],
            "type": "subagent_completed",
            "subagent_id": subagent_id,
            "rounds": rounds,
            "tool_calls": tool_calls,
            "chars": len(final_text),
        })

    summary = final_text.strip()[:SUBAGENT_RESULT_CHARS]
    return ToolResult.ok(
        data={
            "subagent_id": subagent_id,
            "rounds": rounds,
            "tool_calls": tool_calls,
        },
        output=summary,
    )


async def run_subagents(
    runtime,
    state: dict,
    registry,
    tasks: list[str],
    allowed_tools: list[str] | None = None,
    *,
    concurrency: int = MAX_FANOUT_CONCURRENCY,
    max_rounds: int = MAX_SUBAGENT_ROUNDS,
    max_tool_calls: int = MAX_SUBAGENT_TOOL_CALLS,
) -> ToolResult:
    """并行扇出多个聚焦子代理，各自独立收敛后把全部结论交回主代理——让主模型 synthesize。

    设计边界（延续 [[harness-layered-model]] 的 decide/govern 纪律）：
    - 每个子代理复用 run_subagent 的受限语义：白名单工具、独立预算、越界拒绝；
    - 这里只做「并发执行 + 收集」，不代写汇总结论——synthesize 是认知层（主模型）的事，
      治理层不替 Agent 综合判断（避免「Harness 偷偷思考」）。
    - 并发受 Semaphore 限流；任务数/并发上限硬约束，防一次 fan-out 打爆资源。
    - 单个子代理失败不阻断整批：失败项记录错误，成功项照常收编。
    """
    tasks = [str(t).strip() for t in (tasks or []) if str(t).strip()]
    tasks = tasks[:MAX_FANOUT_TASKS]
    if not tasks:
        return ToolResult.fail("没有可执行的任务", error_kind="invalid_input")
    concurrency = max(1, min(int(concurrency), MAX_FANOUT_CONCURRENCY))

    sem = asyncio.Semaphore(concurrency)

    async def _one(task: str) -> dict:
        async with sem:
            try:
                result = await run_subagent(
                    runtime, state, registry, task,
                    allowed_tools=allowed_tools,
                    max_rounds=max_rounds,
                    max_tool_calls=max_tool_calls,
                )
            except Exception as exc:
                result = ToolResult.fail(f"子代理异常: {exc}", error_kind="subagent_error")
            entry = {
                "task": task[:500],
                "subagent_id": (result.data or {}).get("subagent_id", ""),
                "rounds": (result.data or {}).get("rounds", 0),
                "tool_calls": (result.data or {}).get("tool_calls", 0),
                "success": result.success,
                "conclusion": result.output or "",
                "error": result.error,
            }
            runtime._record_event({
                "session_id": state["session_id"],
                "task_id": state["task_id"],
                "type": "subagent_fanout_item",
                **entry,
            })
            return entry

    runtime._record_event({
        "session_id": state["session_id"],
        "task_id": state["task_id"],
        "type": "subagent_fanout_started",
        "task_count": len(tasks),
        "concurrency": concurrency,
        "tools": list(allowed_tools or DEFAULT_SUBAGENT_TOOLS),
    })

    results = await asyncio.gather(*(_one(t) for t in tasks))
    succeeded = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    # 只把结论与证据交回，汇总留给主模型：以编号清单形式组织，便于 synthesize
    lines = []
    for i, r in enumerate(results, start=1):
        status = "完成" if r["success"] else "失败"
        lines.append(f"[{i}] {status}｜{r['task']}")
        if r["conclusion"]:
            lines.append(f"    结论：{r['conclusion']}")
        if r["error"]:
            lines.append(f"    错误：{r['error']}")
    output = (
        f"已并行执行 {len(results)} 个聚焦任务（成功 {len(succeeded)}，失败 {len(failed)}）。"
        f"以下为各任务结论，请综合成对主任务的答复：\n" + "\n".join(lines)
    )

    runtime._record_event({
        "session_id": state["session_id"],
        "task_id": state["task_id"],
        "type": "subagent_fanout_completed",
        "task_count": len(results),
        "succeeded": len(succeeded),
        "failed": len(failed),
    })

    return ToolResult.ok(
        data={
            "task_count": len(results),
            "succeeded": len(succeeded),
            "failed": len(failed),
            "results": results,
        },
        output=output[:SUBAGENT_RESULT_CHARS * 4],
    )
