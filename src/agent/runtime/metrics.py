from collections.abc import Mapping
from collections import Counter
from typing import Any


METRICS_VERSION = "2026-08-15.v1"


def _event_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _budget_exhausted_stages(summary: Mapping[str, Any], events: list[Mapping[str, Any]]) -> list[str]:
    stages = {
        str(stage)
        for stage, budget in (summary.get("stage_budgets") or {}).items()
        if isinstance(budget, Mapping) and str(budget.get("status")) == "exhausted"
    }
    for event in events:
        if event.get("type") == "stage_budget_exhausted":
            stage = _event_payload(event).get("stage") or event.get("stage")
            if stage:
                stages.add(str(stage))
        if event.get("type") == "budget_warning":
            payload = _event_payload(event)
            if payload.get("diagnostic_tool_count") is not None:
                stages.add("diagnostic")
    return sorted(stages)


def _terminal_reason(
    *,
    status: str,
    events: list[Mapping[str, Any]],
    unrecovered_tools: int,
    execution_facts: bool,
    acceptance_passed: bool,
) -> str:
    event_types = [str(event.get("type") or "") for event in events]
    if status == "completed":
        return "completed"
    if status == "waiting_approval" or "approval_required" in event_types:
        return "approval_pending"
    if status == "cancelled":
        return "cancelled"
    if status == "interrupted" or "task_interrupted" in event_types:
        return "interrupted"
    if "stage_budget_exhausted" in event_types:
        return "stage_budget_exhausted"
    if "budget_warning" in event_types and any(
        _event_payload(event).get("diagnostic_tool_count") is not None
        for event in events
        if event.get("type") == "budget_warning"
    ):
        return "diagnostic_budget_exhausted"
    if "tool_repair_exhausted" in event_types:
        return "tool_repair_exhausted"
    if unrecovered_tools:
        return "unrecovered_tool_failure"
    if "model_request_failed" in event_types and not execution_facts:
        return "model_error"
    if not execution_facts:
        return "no_execution_facts"
    if status == "running":
        return "running"
    if acceptance_passed:
        return "incomplete"
    return status or "unknown"


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
    model_error_count = sum(item.get("type") == "model_request_failed" for item in events)
    provider_truncation_count = sum(item.get("type") == "model_response_truncated" for item in events)
    execution_facts = bool(summary.get("changed_files") or checks or summary.get("processes") or tool_runs)
    acceptance_passed = bool(acceptance) and invalid_acceptance == 0
    status = str(task.get("status") or "")
    budget_exhausted_stages = _budget_exhausted_stages(summary, events)
    successful_tools = sum(bool((item.get("result") or {}).get("success")) for item in tool_runs)
    changed_files = set(summary.get("changed_files") or [])
    for changeset in activity.get("changesets", []):
        changed_files.update(changeset.get("files") or [])
    approval_count = sum(event.get("type") == "approval_required" for event in events)
    verification_passed = bool(checks) and failed_checks == 0
    completion_evidence = (
        "verified" if (acceptance_passed or verification_passed) and execution_facts
        else "partial" if execution_facts or checks or acceptance
        else "none"
    )
    last_event = events[-1] if events else {}
    terminal_reason = _terminal_reason(
        status=status,
        events=events,
        unrecovered_tools=unrecovered_tools,
        execution_facts=execution_facts,
        acceptance_passed=acceptance_passed,
    )

    return {
        "false_completion": status == "completed" and bool(failed_checks or invalid_acceptance or unrecovered_tools),
        "false_incomplete": status == "incomplete" and completion_evidence == "verified" and unrecovered_tools == 0,
        "evidence_coverage": round(evidenced / evidence_total, 4) if evidence_total else None,
        "tool_recovery_rate": round(recovered_tools / failed_tools, 4) if failed_tools else None,
        "unrecovered_tool_failures": unrecovered_tools,
        "model_rounds": len(model_events),
        "model_error_count": model_error_count,
        "provider_truncation_count": provider_truncation_count,
        "model_latency_ms": sum(int((item.get("payload") or {}).get("latency_ms") or 0) for item in model_events),
        "total_tokens": sum(int(((item.get("payload") or {}).get("usage") or {}).get("total_tokens") or 0) for item in model_events),
        "event_count": len(events),
        "terminal_reason": terminal_reason,
        "completion_evidence": completion_evidence,
        "budget_exhausted_stages": budget_exhausted_stages,
        "diagnostic_tool_count": int(task.get("diagnostic_tool_count") or 0),
        "diagnostic_unique_count": int(task.get("diagnostic_unique_count") or 0),
        "approval_count": approval_count,
        "failed_tool_count": failed_tools,
        "recovered_tool_count": recovered_tools,
        "successful_tool_count": successful_tools,
        "tool_count": len(tool_runs),
        "changed_file_count": len(changed_files),
        "last_event_type": last_event.get("type") or None,
        "last_event_at": last_event.get("created_at") or None,
        "acceptance_passed": acceptance_passed,
        "acceptance_total": len(acceptance),
        "acceptance_passed_count": sum(item.get("status") == "passed" for item in acceptance),
    }


def aggregate_observability(metrics: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate persisted task metrics for the local observability overview."""
    status_counts = Counter(str(item.get("status") or "unknown") for item in metrics)
    reason_counts = Counter(str(item.get("terminal_reason") or "unknown") for item in metrics)
    evidence_counts = Counter(str(item.get("completion_evidence") or "none") for item in metrics)
    latency_values = [int(item.get("model_latency_ms") or 0) for item in metrics]
    return {
        "task_count": len(metrics),
        "status_counts": dict(status_counts),
        "terminal_reason_counts": dict(reason_counts),
        "completion_evidence_counts": dict(evidence_counts),
        "budget_exhausted_count": sum(bool(item.get("budget_exhausted_stages")) for item in metrics),
        "approval_count": sum(int(item.get("approval_count") or 0) for item in metrics),
        "failed_tool_count": sum(int(item.get("failed_tool_count") or 0) for item in metrics),
        "recovered_tool_count": sum(int(item.get("recovered_tool_count") or 0) for item in metrics),
        "false_completion_count": sum(bool(item.get("false_completion")) for item in metrics),
        "false_incomplete_count": sum(bool(item.get("false_incomplete")) for item in metrics),
        "average_model_latency_ms": round(sum(latency_values) / len(latency_values)) if latency_values else 0,
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in metrics),
    }
