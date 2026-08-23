from agent.runtime.metrics import aggregate_observability, calculate_task_metrics


def test_task_metrics_detect_completion_quality_and_usage():
    metrics = calculate_task_metrics(
        task={
            "status": "completed",
            "summary": {
                "changed_files": ["app.py"],
                "verification": [{"success": True}],
                "acceptance": [{"status": "passed", "evidence": [{"valid": True}]}],
            },
        },
        activity={
            "events": [
                {"type": "model_request_completed", "payload": {"latency_ms": 1200, "usage": {"total_tokens": 900}}},
                {"type": "model_request_completed", "payload": {"latency_ms": 800, "usage": {"total_tokens": 600}}},
            ],
            "tool_runs": [
                {"result": {"success": False}, "recovered_by_call_id": "retry-1"},
                {"result": {"success": True}},
            ],
            "changesets": [{"files": ["app.py"]}],
        },
    )

    assert metrics["false_completion"] is False
    assert metrics["false_incomplete"] is False
    assert metrics["evidence_coverage"] == 1.0
    assert metrics["tool_recovery_rate"] == 1.0
    assert metrics["model_rounds"] == 2
    assert metrics["model_latency_ms"] == 2000
    assert metrics["total_tokens"] == 1500


def test_task_metrics_flags_completed_task_with_failed_verification():
    metrics = calculate_task_metrics(
        task={"status": "completed", "summary": {"verification": [{"success": False}]}},
        activity={"events": [], "tool_runs": [], "changesets": []},
    )

    assert metrics["false_completion"] is True


def test_task_metrics_exposes_model_protocol_failures_and_truncation():
    metrics = calculate_task_metrics(
        task={"status": "incomplete", "summary": {}},
        activity={
            "events": [
                {"type": "model_request_failed", "payload": {"error_type": "AuthenticationError"}},
                {"type": "model_response_truncated", "payload": {"stop_reason": "length"}},
            ],
            "tool_runs": [],
            "changesets": [],
        },
    )

    assert metrics["model_error_count"] == 1
    assert metrics["provider_truncation_count"] == 1


def test_task_metrics_explains_budget_terminal_state_separately_from_completion_evidence():
    metrics = calculate_task_metrics(
        task={
            "status": "incomplete",
            "summary": {
                "changed_files": ["app.py"],
                "verification": [{"success": True}],
                "acceptance": [{"status": "passed", "evidence": [{"valid": True}]}],
                "stage_budgets": {
                    "run": {"used": 3, "limit": 3, "status": "exhausted"},
                },
            },
        },
        activity={
            "events": [
                {"type": "stage_budget_exhausted", "payload": {"stage": "run"}, "created_at": "2026-08-15 10:00:00"},
            ],
            "tool_runs": [],
            "changesets": [{"files": ["app.py"]}],
        },
    )

    assert metrics["terminal_reason"] == "stage_budget_exhausted"
    assert metrics["completion_evidence"] == "verified"
    assert metrics["acceptance_passed"] is True
    assert metrics["acceptance_total"] == 1
    assert metrics["budget_exhausted_stages"] == ["run"]
    assert metrics["last_event_type"] == "stage_budget_exhausted"
    assert metrics["last_event_at"] == "2026-08-15 10:00:00"


def test_task_metrics_accepts_passed_verification_without_explicit_acceptance_ledger():
    metrics = calculate_task_metrics(
        task={
            "status": "incomplete",
            "summary": {"changed_files": ["app.py"], "verification": [{"success": True}]},
        },
        activity={"events": [], "tool_runs": [{"result": {"success": True}}], "changesets": []},
    )

    assert metrics["acceptance_passed"] is False
    assert metrics["completion_evidence"] == "verified"
    assert metrics["false_incomplete"] is True


def test_aggregate_observability_counts_quality_and_failure_reasons():
    summary = aggregate_observability([
        {
            "status": "completed",
            "false_completion": False,
            "false_incomplete": False,
            "terminal_reason": "completed",
            "completion_evidence": "verified",
            "budget_exhausted_stages": [],
            "approval_count": 0,
            "failed_tool_count": 0,
            "recovered_tool_count": 0,
            "model_latency_ms": 100,
            "total_tokens": 10,
        },
        {
            "status": "incomplete",
            "false_completion": False,
            "false_incomplete": True,
            "terminal_reason": "stage_budget_exhausted",
            "completion_evidence": "verified",
            "budget_exhausted_stages": ["run"],
            "approval_count": 1,
            "failed_tool_count": 2,
            "recovered_tool_count": 1,
            "model_latency_ms": 300,
            "total_tokens": 30,
        },
    ])

    assert summary["task_count"] == 2
    assert summary["status_counts"] == {"completed": 1, "incomplete": 1}
    assert summary["terminal_reason_counts"] == {"completed": 1, "stage_budget_exhausted": 1}
    assert summary["budget_exhausted_count"] == 1
    assert summary["approval_count"] == 1
    assert summary["failed_tool_count"] == 2
    assert summary["recovered_tool_count"] == 1
    assert summary["false_incomplete_count"] == 1
    assert summary["average_model_latency_ms"] == 200
    assert summary["total_tokens"] == 40
