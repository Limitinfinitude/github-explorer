from collections.abc import Mapping
from typing import Any


def calculate_task_metrics(*, task: Mapping[str, Any], activity: Mapping[str, Any]) -> dict[str, Any]:
    summary = task.get("summary") if isinstance(task.get("summary"), Mapping) else {}
    checks = [item for item in summary.get("verification", []) if isinstance(item, Mapping)]
    acceptance = [item for item in summary.get("acceptance", []) if isinstance(item, Mapping)]
    tool_runs = [item for item in activity.get("tool_runs", []) if isinstance(item, Mapping)]
    events = [item for item in activity.get("events", []) if isinstance(item, Mapping)]

    failed_checks = sum(not bool(item.get("success")) for item in checks)
    invalid_acceptance = sum(item.get("status") != "passed" for item in acceptance)
    unrecovered_tools = sum(
        not bool((item.get("result") or {}).get("success")) and not item.get("recovered_by_call_id")
        for item in tool_runs
    )
    recovered_tools = sum(bool(item.get("recovered_by_call_id")) for item in tool_runs)
    failed_tools = sum(not bool((item.get("result") or {}).get("success")) for item in tool_runs)
    evidence_total = len(acceptance)
    evidenced = sum(
        any(bool(evidence.get("valid")) for evidence in item.get("evidence", []) if isinstance(evidence, Mapping))
        for item in acceptance
    )
    model_events = [item for item in events if item.get("type") == "model_request_completed"]
    execution_facts = bool(summary.get("changed_files") or checks or summary.get("processes") or tool_runs)
    acceptance_passed = bool(acceptance) and invalid_acceptance == 0
    status = str(task.get("status") or "")

    return {
        "false_completion": status == "completed" and bool(failed_checks or invalid_acceptance or unrecovered_tools),
        "false_incomplete": status == "incomplete" and acceptance_passed and failed_checks == 0 and unrecovered_tools == 0 and execution_facts,
        "evidence_coverage": round(evidenced / evidence_total, 4) if evidence_total else None,
        "tool_recovery_rate": round(recovered_tools / failed_tools, 4) if failed_tools else None,
        "unrecovered_tool_failures": unrecovered_tools,
        "model_rounds": len(model_events),
        "model_latency_ms": sum(int((item.get("payload") or {}).get("latency_ms") or 0) for item in model_events),
        "total_tokens": sum(int(((item.get("payload") or {}).get("usage") or {}).get("total_tokens") or 0) for item in model_events),
        "event_count": len(events),
    }
