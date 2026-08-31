"""聊天消息落库、事件归档与增量查询测试。"""
from fastapi.testclient import TestClient

from agent.memory import Memory

import main


def _store(tmp_path):
    return Memory(tmp_path / "agent.db")


def test_chat_message_roundtrip_preserves_process_data(tmp_path):
    store = _store(tmp_path)
    store.save_chat_message("s1", {
        "role": "assistant",
        "content": "完成。",
        "time": "2026-08-20T10:00:00Z",
        "thinking": ["思考一", "思考二"],
        "narrations": ["正在读取 README.md"],
        "steps": [{"toolName": "read_file", "done": True, "status": "succeeded"}],
        "agentRun": {"status": "completed"},
    })
    store.save_chat_message("s1", {"role": "user", "content": "你好", "time": "2026-08-20T09:00:00Z"})

    messages = store.get_chat_messages("s1")
    assert len(messages) == 2
    assistant = messages[0]
    assert assistant["thinking"] == ["思考一", "思考二"]
    assert assistant["narrations"] == ["正在读取 README.md"]
    assert assistant["steps"][0]["toolName"] == "read_file"
    assert assistant["agentRun"]["status"] == "completed"
    assert messages[1]["role"] == "user"
    assert store.get_chat_messages("other") == []


def test_chat_messages_api_roundtrip(tmp_path, monkeypatch):
    import sys
    import agent.memory  # noqa: F401  确保模块已加载（agent/__init__ 用实例遮蔽了同名属性）
    store = _store(tmp_path)
    monkeypatch.setattr(sys.modules["agent.memory"], "memory", store)
    # 聊天端点在 routes_agent，其模块级显式绑定 memory（组合根单点），需一并替换
    import routes_agent
    monkeypatch.setattr(routes_agent, "memory", store, raising=False)

    response = TestClient(main.app).post("/api/chats/sess-1/messages", json={
        "role": "assistant",
        "content": "完成。",
        "thinking": ["推理内容"],
        "steps": [{"toolName": "read_file"}],
    })
    assert response.status_code == 200

    loaded = TestClient(main.app).get("/api/chats/sess-1").json()
    assert loaded["session_id"] == "sess-1"
    assert len(loaded["messages"]) == 1
    assert loaded["messages"][0]["thinking"] == ["推理内容"]
    assert loaded["messages"][0]["steps"] == [{"toolName": "read_file"}]


def test_get_agent_events_supports_incremental_read(tmp_path):
    store = _store(tmp_path)
    first = store.record_agent_event({"task_id": "t1", "session_id": "s1", "type": "a", "content": "1"})
    second = store.record_agent_event({"task_id": "t1", "session_id": "s1", "type": "b", "content": "2"})

    assert first == 1 and second == 2
    incremental = store.get_agent_events("t1", after_sequence=1)
    assert [event["sequence"] for event in incremental] == [2]
    assert incremental[0]["type"] == "b"
    assert store.get_agent_events("t1", after_sequence=2) == []


def test_prune_agent_events_removes_old_rows_only(tmp_path):
    store = _store(tmp_path)
    store.record_agent_event({"task_id": "t1", "session_id": "s1", "type": "a", "content": "1"})
    # 手动把 created_at 改到 60 天前，模拟过期事件
    store.conn.execute("UPDATE agent_events SET created_at = '2020-01-01 00:00:00'")
    store.conn.commit()

    counts = store.prune_agent_events(days=30)

    assert counts["agent_events"] >= 1
    assert store.get_agent_events("t1") == []
    # 新写入的事件不受影响
    store.record_agent_event({"task_id": "t2", "session_id": "s1", "type": "b", "content": "2"})
    assert len(store.get_agent_events("t2")) == 1
