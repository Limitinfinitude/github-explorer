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
