"""Project-level read-only projection for the journey and developer evidence views."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import ntpath
from typing import Any

from .tracing import sanitize
from .metrics import calculate_task_metrics


_TERMINAL_FAILURES = {"failed", "blocked", "cancelled", "interrupted", "incomplete"}
_PROJECT_ACTION_PROMPTS = {
    "inspect": (
        "对当前项目执行项目体检：识别技术栈、入口、依赖、环境要求和可运行性风险；"
        "读取必要文件，给出有证据的结论和下一步，不修改业务代码。"
    ),
    "prepare": (
        "为当前项目准备本地开发环境：先识别项目类型，再按项目约定创建或复用项目内环境、"
        "安装依赖并验证关键命令；失败时说明原因并调整方案。"
    ),
    "start": (
        "启动当前项目并验证服务：识别正确入口，使用受管后台进程启动，检查进程、端口和 HTTP；"
        "最终给出可访问地址、进程状态和未完成事项。"
    ),
    "guide": (
        "生成当前项目导读：基于真实源码说明用途、技术栈、目录、核心模块、启动链路和适合继续学习的入口；"
        "引用实际文件，不修改项目。"
    ),
    "verify": (
        "运行当前项目的验证：识别已有测试、构建或静态检查命令，执行最相关的验证并解释失败；"
        "不要把未通过的验证描述为完成。"
    ),
}


def _normalize_workspace_root(workspace_root: str) -> str:
    """Normalize a Windows workspace for identity without requiring it to exist."""
    value = str(workspace_root or "").strip().replace("/", "\\")
    normalized = ntpath.normpath(value) if value else ""
    return normalized.rstrip("\\").casefold()


def project_id_for_workspace(workspace_root: str) -> str:
    normalized = _normalize_workspace_root(workspace_root)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"project-{digest}"


def project_session_id_for_workspace(workspace_root: str) -> str:
    return project_id_for_workspace(workspace_root).replace("project-", "project-session-", 1)


def project_action_prompt(action: str) -> str:
    try:
        return _PROJECT_ACTION_PROMPTS[action]
    except KeyError as exc:
        raise ValueError(f"不支持的项目动作: {action}") from exc


def _first_error_line(result: Mapping[str, Any]) -> str:
    """Extract the first meaningful line of a failed tool result."""
    for key in ("error", "stderr", "message"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:160]
    output = result.get("output")
    if isinstance(output, str):
        for line in output.strip().splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:160]
    return "未知错误"


def _failure_patterns(tool_runs: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate unrecovered tool failures into a compact failure catalog."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for run in tool_runs or []:
        if not isinstance(run, Mapping):
            continue
        if run.get("recovered_by_call_id"):
            continue
        result = run.get("result") if isinstance(run.get("result"), Mapping) else {}
        if result.get("success", False):
            continue
        tool_name = str(run.get("tool_name") or "tool")
        error = _first_error_line(result)
        key = (tool_name, error)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "tool_name": tool_name,
                "error": error,
                "count": 0,
                "last_at": str(run.get("created_at") or ""),
            }
        group["count"] += 1
        created_at = str(run.get("created_at") or "")
        if created_at and created_at > group["last_at"]:
            group["last_at"] = created_at
    return sorted(groups.values(), key=lambda item: item["count"], reverse=True)


def build_project_summary(tasks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    projects: dict[str, dict[str, Any]] = {}
    ordered = sorted(
        enumerate(tasks),
        key=lambda item: (str(item[1].get("updated_at") or ""), -item[0]),
        reverse=True,
    )
    for _, task in ordered:
        root = str(task.get("workspace_root") or "")
        if not root:
            continue
        project_id = project_id_for_workspace(root)
        project = projects.setdefault(project_id, {
            "project_id": project_id,
            "workspace_root": root,
            "latest_task_id": None,
            "task_count": 0,
        })
        if project["latest_task_id"] is None:
            project["workspace_root"] = root
            project["latest_task_id"] = task.get("task_id")
            project["updated_at"] = task.get("updated_at")
        project["task_count"] += 1
    return sorted(
        projects.values(),
        key=lambda item: str(item.get("updated_at") or ""),
        reverse=True,
    )


def _stage_for_task(
    task: Mapping[str, Any] | None,
    activity: Mapping[str, Any],
) -> str:
    if not task:
        return "intake"
    summary = task.get("summary") if isinstance(task.get("summary"), Mapping) else {}
    processes = summary.get("processes") if isinstance(summary.get("processes"), list) else []
    verification = summary.get("verification") if isinstance(summary.get("verification"), list) else []
    changed_files = summary.get("changed_files") if isinstance(summary.get("changed_files"), list) else []
    changesets = activity.get("changesets") if isinstance(activity.get("changesets"), list) else []
    successful_tools = {
        str(name) for name in summary.get("successful_tools", [])
    } if isinstance(summary.get("successful_tools"), list) else set()
    if processes:
        return "run"
    if verification:
        return "verify"
    if changed_files or changesets or successful_tools.intersection({"edit_files", "create_directory"}):
        return "experiment"
    if successful_tools.intersection({"read_file", "search_text", "repo_map"}):
        return "understand"
    return "inspect"


def _stage_status(task: Mapping[str, Any] | None) -> str:
    if not task:
        return "not_started"
    status = str(task.get("status") or "pending")
    if status == "completed":
        return "completed"
    if status in _TERMINAL_FAILURES:
        return status
    if status == "waiting_approval":
        return "waiting_approval"
    return "running"


def _project_message(task: Mapping[str, Any], workspace_root: str) -> str:
    message = str(task.get("user_message") or "").strip()
    if message and set(message) != {"?"}:
        return message
    workspace_name = ntpath.basename(ntpath.normpath(workspace_root)) if workspace_root else ""
    return workspace_name or "尚未开始项目任务"


def build_project_overview(*, project_id: str, workspace: Mapping[str, Any] | None,
                           task: Mapping[str, Any] | None, activity: Mapping[str, Any] | None,
                           traces: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Build a deterministic projection; this function never executes a tool."""
    activity = activity or {}
    task = task or {}
    summary = task.get("summary") if isinstance(task.get("summary"), dict) else {}
    processes = summary.get("processes") if isinstance(summary.get("processes"), list) else []
    changes = activity.get("changesets") if isinstance(activity.get("changesets"), list) else []
    files = sorted({str(path) for change in changes if isinstance(change, dict)
                    for path in change.get("files", [])})
    verification = summary.get("verification") if isinstance(summary.get("verification"), list) else []
    status = _stage_status(task)
    stage = _stage_for_task(task, activity)
    failed = status in _TERMINAL_FAILURES or any(not item.get("success", False) for item in verification if isinstance(item, dict))
    if not task:
        next_action = "运行项目体检"
    elif status == "waiting_approval":
        next_action = "打开项目对话处理确认"
    elif status == "running":
        next_action = "打开项目对话查看进度"
    elif failed:
        next_action = "查看失败原因并重试"
    elif files and not verification:
        next_action = "运行验证"
    elif not activity.get("tool_runs"):
        next_action = "运行项目体检"
    elif verification and not any(process.get("status") == "running" for process in processes if isinstance(process, Mapping)):
        next_action = "启动并验证"
    else:
        next_action = "打开项目对话继续"
    root = str(
        (workspace or {}).get("root")
        or (workspace or {}).get("workspace")
        or task.get("workspace_root")
        or ""
    )
    return {
        "project_id": project_id,
        "project_session_id": project_session_id_for_workspace(root),
        "workspace_root": root,
        "current_path": str((workspace or {}).get("current_path") or root),
        "stage": stage,
        "stage_status": status,
        "next_action": next_action,
        "summary": {
            "task_id": task.get("task_id"),
            "message": _project_message(task, root),
            "status": task.get("status") or "not_started",
            "changed_file_count": len(files),
            "verification_count": len(verification),
            "process_count": len(processes),
            "failed": failed,
        },
        "evidence_counts": {
            "events": len(activity.get("events") or []),
            "tool_runs": len(activity.get("tool_runs") or []),
            "changesets": len(changes),
            "files": len(files),
            "artifacts": len(activity.get("artifacts") or []),
        },
        "changed_files": files,
        "active_processes": processes,
        "latest_verification": verification[-1] if verification else None,
        "stage_budgets": summary.get("stage_budgets", {}),
        "quality_metrics": calculate_task_metrics(task=task, activity=activity),
        "failure_patterns": _failure_patterns(activity.get("tool_runs") or []),
        "trace": next(
            (trace for trace in (traces or []) if trace.get("task_id") == project_id),
            None,
        ),
    }


def build_project_evidence(*, project_id: str, workspace: Mapping[str, Any] | None,
                           task: Mapping[str, Any] | None, activity: Mapping[str, Any] | None,
                           history: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Return the developer evidence layer without exposing secrets by default."""
    activity = activity or {}
    task = task or {}
    records = list(history or ([{"task": task, "activity": activity}] if task else []))
    records.sort(
        key=lambda item: str(
            item.get("task", {}).get("updated_at")
            or item.get("task", {}).get("created_at")
            or ""
        ),
        reverse=True,
    )
    entries: list[dict[str, Any]] = []
    task_history: list[dict[str, Any]] = []
    for record in records:
        record_task = record.get("task") if isinstance(record.get("task"), Mapping) else {}
        record_activity = record.get("activity") if isinstance(record.get("activity"), Mapping) else {}
        task_id = str(record_task.get("task_id") or "")
        created_at = str(record_task.get("updated_at") or record_task.get("created_at") or "")
        task_history.append({
            "task_id": task_id,
            "session_id": record_task.get("session_id"),
            "message": record_task.get("user_message") or "未命名任务",
            "status": record_task.get("status") or "unknown",
            "created_at": created_at,
        })
        for index, tool in enumerate(record_activity.get("tool_runs") or []):
            result = tool.get("result") if isinstance(tool.get("result"), Mapping) else {}
            recovered = bool(tool.get("recovered_by_call_id"))
            entries.append({
                "id": f"{task_id}-tool-{index}", "task_id": task_id, "category": "tools",
                "status": "recovered" if recovered else ("succeeded" if result.get("success") else "failed"),
                "title": str(tool.get("tool_name") or "tool"),
                "created_at": tool.get("created_at") or created_at,
                "details": {
                    "args": tool.get("args") or {}, "result": result,
                    "recovered_by_call_id": tool.get("recovered_by_call_id"),
                },
            })
        for index, event in enumerate(record_activity.get("events") or []):
            event_type = str(event.get("type") or "event")
            entries.append({
                "id": f"{task_id}-event-{event.get('sequence', index)}", "task_id": task_id,
                "category": "events", "status": "failed" if "fail" in event_type or event_type == "error" else "info",
                "title": event_type, "created_at": event.get("created_at") or created_at,
                "details": event.get("payload") or {},
            })
        for index, change in enumerate(record_activity.get("changesets") or []):
            entries.append({
                "id": f"{task_id}-file-{index}", "task_id": task_id, "category": "files",
                "status": "changed", "title": f"{len(change.get('files') or [])} 个文件变更",
                "created_at": change.get("created_at") or created_at, "details": change,
            })
        summary = record_task.get("summary") if isinstance(record_task.get("summary"), Mapping) else {}
        for index, check in enumerate(summary.get("verification") or []):
            entries.append({
                "id": f"{task_id}-verification-{index}", "task_id": task_id,
                "category": "verification", "status": "succeeded" if check.get("success") else "failed",
                "title": str(check.get("command") or check.get("kind") or "验证"),
                "created_at": created_at, "details": check,
            })
        for index, process in enumerate(summary.get("processes") or []):
            entries.append({
                "id": f"{task_id}-process-{index}", "task_id": task_id, "category": "processes",
                "status": str(process.get("status") or "unknown"),
                "title": str(process.get("command") or process.get("process_id") or "后台进程"),
                "created_at": created_at, "details": process,
            })
        trace = record.get("trace") if isinstance(record.get("trace"), Mapping) else None
        if trace:
            entries.append({
                "id": f"{task_id}-trace", "task_id": task_id, "category": "observability",
                "status": str(trace.get("status") or "recorded"), "title": "本地 Trace",
                "created_at": trace.get("updated_at") or trace.get("created_at") or created_at,
                "details": trace,
            })
    payload = {
        "project_id": project_id,
        "workspace_root": str((workspace or {}).get("root") or ""),
        "task": task,
        "task_history": task_history,
        "entries": sorted(entries, key=lambda item: str(item.get("created_at") or ""), reverse=True),
        "events": activity.get("events", []),
        "tool_runs": activity.get("tool_runs", []),
        "changesets": activity.get("changesets", []),
        "artifacts": activity.get("artifacts", []),
        "developer_layers": ["events", "tools", "files", "verification", "processes", "local_trace"],
    }
    return sanitize(payload)


_STATUS_LABELS = {
    "completed": "已完成",
    "incomplete": "未完成",
    "failed": "失败",
    "blocked": "受阻",
    "cancelled": "已取消",
    "interrupted": "已中断",
    "waiting_approval": "等待确认",
    "running": "进行中",
    "not_started": "未开始",
    "pending": "排队中",
}

_TERMINAL_REASON_LABELS = {
    "completed": "正常完成",
    "stage_budget_exhausted": "阶段预算已用尽",
    "diagnostic_budget_exhausted": "诊断预算已用尽",
    "approval_pending": "等待审批",
    "tool_repair_exhausted": "工具修复已耗尽",
    "unrecovered_tool_failure": "存在未恢复工具失败",
    "interrupted": "运行被中断",
    "cancelled": "任务已取消",
    "model_error": "模型请求失败",
    "no_execution_facts": "没有执行事实",
    "running": "仍在运行",
}

_STAGE_LABELS = {
    "intake": "材料导入",
    "inspect": "项目体检",
    "run": "跑起来",
    "understand": "看懂",
    "experiment": "实验室",
    "verify": "验证",
    "record": "记录",
}

_VERIFICATION_LABELS = {
    "verified": "已验证",
    "partial": "部分完成",
    "none": "无证据",
}


def _project_report_label(mapping: dict[str, str], key: str | None, fallback: str) -> str:
    return mapping.get(str(key or "")) or (str(key or "") if key else fallback)


def build_project_report_markdown(
    *,
    overview: Mapping[str, Any],
    evidence: Mapping[str, Any],
    memories: list[Mapping[str, Any]] | None = None,
) -> str:
    """Render a self-contained Markdown project report from persisted facts."""
    import datetime as _dt
    from collections.abc import Mapping as _Mapping

    def _lines() -> list[str]:
        summary = overview.get("summary") if isinstance(overview.get("summary"), _Mapping) else {}
        metrics = overview.get("quality_metrics") if isinstance(overview.get("quality_metrics"), _Mapping) else {}
        counts = overview.get("evidence_counts") if isinstance(overview.get("evidence_counts"), _Mapping) else {}
        root = str(overview.get("workspace_root") or "")
        workspace_name = ntpath.basename(ntpath.normpath(root)) if root else "未绑定工作区"
        lines = [
            f"# 项目报告 — {workspace_name}",
            "",
            f"> 生成时间：{_dt.datetime.now().astimezone().isoformat(timespec='seconds')} · 项目 ID：{overview.get('project_id', '')}",
            "",
            "## 概览",
            "",
            f"- 工作区：`{root or '未绑定'}`",
            f"- 当前阶段：{_project_report_label(_STAGE_LABELS, overview.get('stage'), '项目体检')}",
            f"- 阶段状态：{_project_report_label(_STATUS_LABELS, overview.get('stage_status'), '未开始')}",
            f"- 下一步：{overview.get('next_action') or '—'}",
            f"- 最近任务：{summary.get('message') or '—'}",
            f"- 变更文件：{summary.get('changed_file_count', 0)} · 验证项：{summary.get('verification_count', 0)} · 后台进程：{summary.get('process_count', 0)}",
            "",
            "## 终态与可信度",
            "",
            f"- 完成证据：{_project_report_label(_VERIFICATION_LABELS, metrics.get('completion_evidence'), '无证据')}",
            f"- 结束原因：{_project_report_label(_TERMINAL_REASON_LABELS, metrics.get('terminal_reason'), '未分类')}",
            f"- 完成状态误报：{'是' if metrics.get('false_completion') else '否'}",
            f"- 未完成误判：{'是' if metrics.get('false_incomplete') else '否'}",
            f"- 验收清单：{metrics.get('acceptance_passed_count', 0)}/{metrics.get('acceptance_total', 0)} 项通过",
            f"- 恢复率：{_fmt_ratio(metrics.get('tool_recovery_rate'))}（未恢复失败 {metrics.get('unrecovered_tool_failures', 0)} 次）",
            "",
            "## 模型成本",
            "",
            f"- 模型轮次：{metrics.get('model_rounds', 0)}",
            f"- 总 token：{metrics.get('total_tokens', 0)}",
            f"- 平均延迟：{metrics.get('model_latency_ms', 0)} ms",
            f"- 模型错误：{metrics.get('model_error_count', 0)} · 提供方截断：{metrics.get('provider_truncation_count', 0)}",
            "",
        ]
        failures = overview.get("failure_patterns") or []
        lines.append("## 失败模式")
        lines.append("")
        if not failures:
            lines.append("未发现未恢复的工具失败。")
        else:
            lines.append("| 工具 | 次数 | 错误摘要 |")
            lines.append("|---|---|---|")
            for pattern in failures:
                error = str(pattern.get("error") or "").replace("|", "\\|")
                lines.append(f"| {pattern.get('tool_name', '')} | {pattern.get('count', 0)} | {error} |")
        lines.append("")
        history = evidence.get("task_history") if isinstance(evidence.get("task_history"), list) else []
        lines.append("## 任务历史")
        lines.append("")
        if not history:
            lines.append("暂无任务记录。")
        else:
            for item in history:
                status = _project_report_label(_STATUS_LABELS, item.get("status"), "未知")
                lines.append(
                    f"- {item.get('created_at') or '未知时间'} · {status} · "
                    f"{str(item.get('message') or '未命名任务').replace(chr(10), ' ')} "
                    f"(`{item.get('task_id') or ''}`)"
                )
        lines.append("")
        lines.append("## 项目记忆")
        lines.append("")
        if not memories:
            lines.append("尚未沉淀项目事实。")
        else:
            for fact in memories:
                confidence = float(fact.get("confidence") or 0)
                lines.append(
                    f"- **{str(fact.get('content') or '').replace(chr(10), ' ')}**"
                    f"（来源 {fact.get('source_type') or '未知'}，"
                    f"验证 {fact.get('verification_status') or 'unverified'}，"
                    f"置信度 {confidence:.0%}）"
                )
        lines.append("")
        lines.append("## 执行证据统计")
        lines.append("")
        lines.append(f"- 事件 {counts.get('events', 0)} 条 · 工具运行 {counts.get('tool_runs', 0)} 次 · "
                     f"变更集 {counts.get('changesets', 0)} 个 · 涉及文件 {counts.get('files', 0)} 个 · "
                     f"产物 {counts.get('artifacts', 0)} 个")
        lines.append("")
        lines.append("_本报告由本地工作台生成；完整逐条证据请使用工作台中的「开发者证据层」导出。_")
        return lines

    return "\n".join(_lines())


def _fmt_ratio(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return str(value)
