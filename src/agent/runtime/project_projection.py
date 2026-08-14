"""Project-level read-only projection for the journey and developer evidence views."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import ntpath
from typing import Any

from .tracing import sanitize


_TERMINAL_FAILURES = {"failed", "blocked", "cancelled", "interrupted", "incomplete"}


def _normalize_workspace_root(workspace_root: str) -> str:
    """Normalize a Windows workspace for identity without requiring it to exist."""
    value = str(workspace_root or "").strip().replace("/", "\\")
    normalized = ntpath.normpath(value) if value else ""
    return normalized.rstrip("\\").casefold()


def project_id_for_workspace(workspace_root: str) -> str:
    normalized = _normalize_workspace_root(workspace_root)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"project-{digest}"


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


def _stage_for_task(task: Mapping[str, Any] | None) -> str:
    if not task:
        return "intake"
    text = str(task.get("user_message") or "").lower()
    if any(word in text for word in ("启动", "运行", "服务", "端口", "start", "run")):
        return "run"
    if any(word in text for word in ("测试", "test", "验证", "verify")):
        return "verify"
    if any(word in text for word in ("修改", "实现", "修复", "编辑", "改")):
        return "experiment"
    if any(word in text for word in ("分析", "架构", "看懂", "解释", "学习")):
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
    stage = _stage_for_task(task)
    failed = status in _TERMINAL_FAILURES or any(not item.get("success", False) for item in verification if isinstance(item, dict))
    next_action = "查看项目体检" if not task else (
        "查看失败原因并重试" if failed else
        "展开开发者证据" if status == "completed" else
        "继续当前任务"
    )
    root = str((workspace or {}).get("root") or (workspace or {}).get("workspace") or "")
    return {
        "project_id": project_id,
        "workspace_root": root,
        "current_path": str((workspace or {}).get("current_path") or root),
        "stage": stage,
        "stage_status": status,
        "next_action": next_action,
        "summary": {
            "task_id": task.get("task_id"),
            "message": task.get("user_message") or "尚未开始项目任务",
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
