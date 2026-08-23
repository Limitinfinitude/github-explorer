import json
from pathlib import Path

from agent.runtime.tool_calls import normalize_tool_calls, reconcile_tool_messages, tool_recovery_key


def _tool_use_ids(messages):
    return [
        block["id"]
        for message in messages
        if message.get("role") == "assistant" and isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_use"
    ]


def _tool_result_ids(messages):
    return [
        block["tool_use_id"]
        for message in messages
        if message.get("role") == "user" and isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]


def test_normalize_tool_calls_preserves_unique_provider_id_and_replaces_unsafe_ids():
    calls = normalize_tool_calls([
        {"id": "provider-1", "name": "read_file", "input": {"path": "a"}},
        {"id": "provider-1", "name": "read_file", "input": {"path": "b"}},
        {"id": "text-tool-1", "name": "read_file", "input": {"path": "c"}},
        {"name": "read_file", "input": {"path": "d"}},
    ])

    assert calls[0]["id"] == "provider-1"
    assert calls[0]["source_call_id"] == "provider-1"
    assert len({call["id"] for call in calls}) == 4
    assert all(call["id"] for call in calls)
    assert all(not call["id"].startswith("text-tool-") for call in calls[1:])


def test_normalize_tool_calls_avoids_ids_already_used_by_the_task():
    calls = normalize_tool_calls([
        {"id": "provider-1", "name": "read_file", "input": {"path": "a"}},
    ], existing_ids={"provider-1"})

    assert calls[0]["id"] != "provider-1"
    assert calls[0]["source_call_id"] == "provider-1"


def test_tool_recovery_key_separates_command_categories_and_normalizes_cwd(tmp_path):
    current = tmp_path / "project"

    failed_test = tool_recovery_key(
        "run_command", {"command": "pytest -q", "cwd": "."}, current,
    )
    successful_test = tool_recovery_key(
        "run_command", {"command": ".venv\\Scripts\\python.exe -m pytest -q", "cwd": "."}, current,
    )
    unrelated_command = tool_recovery_key(
        "run_command", {"command": "Get-Location", "cwd": "."}, current,
    )

    assert failed_test == successful_test
    assert failed_test != unrelated_command
    assert str(current.resolve()).replace("\\", "/").casefold() in failed_test


def test_tool_recovery_key_links_http_batches_by_explicit_group_id(tmp_path: Path):
    current = tmp_path / "project"
    current.mkdir()

    first = tool_recovery_key(
        "http_request_batch",
        {"group_id": "books-v2", "requests": [{"method": "GET", "url": "http://127.0.0.1:8000/health"}]},
        current,
    )
    second = tool_recovery_key(
        "http_request_batch",
        {"group_id": "books-v2", "requests": [{"method": "GET", "url": "http://127.0.0.1:8000/health"}]},
        current,
    )

    assert first == second == "http-batch:books-v2"


def test_reconcile_keeps_a_complete_tool_turn_unchanged():
    messages = [
        {"role": "user", "content": "read it"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "reading"},
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {"path": "a"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"},
        ]},
        {"role": "assistant", "content": "done"},
    ]

    assert reconcile_tool_messages(messages) == messages


def test_reconcile_removes_orphan_result_but_preserves_user_text():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "keep this"},
            {"type": "tool_result", "tool_use_id": "missing", "content": "orphan"},
        ]},
    ]

    safe = reconcile_tool_messages(messages)

    assert safe == [{"role": "user", "content": [{"type": "text", "text": "keep this"}]}]


def test_reconcile_keeps_only_one_result_for_each_call():
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "first"},
            {"type": "tool_result", "tool_use_id": "call-1", "content": "duplicate"},
        ]},
    ]

    safe = reconcile_tool_messages(messages)
    results = [
        block for message in safe for block in message.get("content", [])
        if isinstance(message.get("content"), list) and block.get("type") == "tool_result"
    ]

    assert results == [{"type": "tool_result", "tool_use_id": "call-1", "content": "first"}]


def test_reconcile_completes_the_whole_batch_from_ledger_or_interruption():
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}},
            {"type": "tool_use", "id": "call-2", "name": "run_command", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"},
        ]},
        {"role": "user", "content": "continue"},
    ]
    ledger = {
        "call-2": {
            "call_id": "call-2",
            "tool_name": "run_command",
            "status": "failed",
            "result": {"success": False, "error": "command failed"},
            "error_kind": "execution",
        },
    }

    safe = reconcile_tool_messages(messages, ledger)

    assert _tool_use_ids(safe) == ["call-1", "call-2"]
    assert _tool_result_ids(safe) == ["call-1", "call-2"]
    result_message = safe[1]
    call_2 = next(block for block in result_message["content"] if block["tool_use_id"] == "call-2")
    assert json.loads(call_2["content"])["error"] == "command failed"
    assert safe[2] == {"role": "user", "content": "continue"}


def test_reconcile_synthesizes_interrupted_result_when_ledger_is_open():
    messages = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call-1", "name": "read_file", "input": {}},
        ]},
    ]
    ledger = {"call-1": {"call_id": "call-1", "tool_name": "read_file", "status": "running"}}

    safe = reconcile_tool_messages(messages, ledger)

    assert _tool_use_ids(safe) == _tool_result_ids(safe) == ["call-1"]
    result = safe[1]["content"][0]
    payload = json.loads(result["content"])
    assert payload["success"] is False
    assert payload["error_kind"] == "interrupted"
