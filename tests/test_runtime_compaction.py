from copy import deepcopy

from agent.runtime.compaction import CompactionEngine


def test_compaction_extracts_workspace_changes_verification_and_failures():
    state = {
        "user_message": "启动服务并验证",
        "summary": {
            "changed_files": ["app.py"],
            "verification": [{"name": "pytest", "success": True}],
            "processes": [{"process_id": 42, "status": "running"}],
        },
        "workspace_root": "C:/project",
        "current_path": "C:/project/src",
        "messages": [
            {"role": "assistant", "content": "工具失败：端口被占用"},
        ],
    }

    handoff = CompactionEngine().deterministic_handoff(state)

    assert handoff.goal == "启动服务并验证"
    assert handoff.changed_files == ["app.py"]
    assert handoff.verification[0]["success"] is True
    assert handoff.current_path == "C:/project/src"
    assert any("端口被占用" in item for item in handoff.failures)


def test_compaction_keeps_latest_user_request_fits_budget_and_preserves_input():
    messages = [
        {"role": "user", "content": "old" * 10_000},
        {"role": "assistant", "content": "old result" * 10_000},
        {"role": "user", "content": "latest request"},
    ]
    original = deepcopy(messages)

    fitted, handoff = CompactionEngine(max_tokens=1_000).compact(
        system="system",
        messages=messages,
        state={"user_message": "latest request", "summary": {}, "messages": messages},
    )

    assert messages == original
    assert fitted[-1]["content"] == "latest request"
    assert any("ContextHandoff" in str(item["content"]) for item in fitted)
    assert CompactionEngine.estimate_tokens("system", fitted) <= 1_000
    assert handoff.source_message_count == 3


def test_compaction_redacts_secret_values_from_handoff():
    state = {
        "user_message": "测试连接",
        "summary": {},
        "messages": [
            {
                "role": "user",
                "content": "api_key=sk-example-secret-value-1234567890",
            },
            {
                "role": "assistant",
                "content": "请求失败，Authorization: Bearer private-token-value-123456",
            },
        ],
    }

    handoff = CompactionEngine().deterministic_handoff(state)
    rendered = handoff.to_context_message()["content"]

    assert "sk-example-secret-value" not in rendered
    assert "private-token-value" not in rendered
