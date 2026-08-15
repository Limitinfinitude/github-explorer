from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


def build_evaluation_report(memory: Any, *, limit: int = 100) -> dict[str, Any]:
    """Create a compact, reproducible report from persisted task facts."""
    traces = memory.list_agent_traces(max(1, min(int(limit), 500)))
    tasks: list[dict[str, Any]] = []
    for trace in traces:
        task_id = trace.get("task_id")
        activity = memory.get_agent_task_activity(task_id) if task_id else {}
        tools = [
            {
                "tool_name": item.get("tool_name"),
                "created_at": item.get("created_at"),
                "success": bool((item.get("result") or {}).get("success")),
                "recovered_by_call_id": item.get("recovered_by_call_id"),
            }
            for item in activity.get("tool_runs", [])
        ]
        changed_files = sorted({
            file_name
            for change in activity.get("changesets", [])
            for file_name in (change.get("files") or [])
        })
        artifacts = [
            {
                key: artifact.get(key)
                for key in ("artifact_id", "call_id", "tool_name", "mime_type", "size", "created_at")
            }
            for artifact in activity.get("artifacts", [])
        ]
        tasks.append({
            "task_id": task_id,
            "session_id": trace.get("session_id"),
            "workspace_root": trace.get("workspace_root"),
            "status": trace.get("status"),
            "terminal_reason": trace.get("terminal_reason"),
            "completion_evidence": trace.get("completion_evidence"),
            "metrics_version": trace.get("metrics_version"),
            "tool_count": len(tools),
            "tools": tools,
            "changed_files": changed_files,
            "artifacts": artifacts,
            "verification": trace.get("verification"),
        })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics_version": next((item.get("metrics_version") for item in traces if item.get("metrics_version")), None),
        "summary": {
            "task_count": len(tasks),
            "status_counts": dict(Counter(str(item.get("status") or "unknown") for item in tasks)),
            "tool_count": sum(item["tool_count"] for item in tasks),
            "changed_file_count": len({file_name for item in tasks for file_name in item["changed_files"]}),
            "artifact_count": sum(len(item["artifacts"]) for item in tasks),
        },
        "tasks": tasks,
    }
