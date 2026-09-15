"""聊天消息级的编辑/删除同步测试：
- replace_chat_messages 整表覆盖（编辑重发 / 删除 / 重新生成）
- suppress_chat_task 抑制补收（删除过的任务不再被 recovery 回放复活）
"""

from agent.memory import Memory


def _assistant(content: str, task_id: str | None = None, **kw) -> dict:
    msg = {
        "role": "assistant", "content": content, "time": "2026-09-02 10:00:00",
        "thinking": [], "narrations": [], "steps": [], "cmdBlocks": [],
        "timeline": [], "workElapsed": 12,
    }
    if task_id:
        msg["agentRun"] = {"taskId": task_id, "status": "completed"}
    msg.update(kw)
    return msg


def test_replace_chat_messages_overwrites(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.save_chat_message("s1", {"role": "user", "content": "第一问"})
    memory.save_chat_message("s1", _assistant("第一答", "task-1"))
    memory.save_chat_message("s1", {"role": "user", "content": "第二问"})
    assert len(memory.get_chat_messages("s1")) == 3

    saved = memory.replace_chat_messages("s1", [
        {"role": "user", "content": "第一问"},
        _assistant("改写后的答", "task-1b", workElapsed=99),
    ])
    messages = memory.get_chat_messages("s1")
    assert saved == 2
    assert len(messages) == 2
    assert messages[1]["content"] == "改写后的答"
    assert messages[1]["agentRun"]["taskId"] == "task-1b"
    assert messages[1]["workElapsed"] == 99


def test_replace_chat_messages_isolates_sessions(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.save_chat_message("s1", {"role": "user", "content": "A"})
    memory.save_chat_message("s2", {"role": "user", "content": "B"})

    memory.replace_chat_messages("s1", [{"role": "user", "content": "A2"}])

    assert [m["content"] for m in memory.get_chat_messages("s1")] == ["A2"]
    assert [m["content"] for m in memory.get_chat_messages("s2")] == ["B"]


def test_replace_can_clear_all_messages(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.save_chat_message("s1", {"role": "user", "content": "A"})
    memory.replace_chat_messages("s1", [])
    assert memory.get_chat_messages("s1") == []


def test_suppress_chat_task_is_idempotent(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.suppress_chat_task("s1", "task-1")
    memory.suppress_chat_task("s1", "task-1")
    memory.suppress_chat_task("s1", "task-2")
    memory.suppress_chat_task("s2", "task-9")

    assert sorted(memory.get_suppressed_chat_tasks("s1")) == ["task-1", "task-2"]
    assert memory.get_suppressed_chat_tasks("s2") == ["task-9"]
    assert memory.get_suppressed_chat_tasks("unknown") == []


def test_suppress_ignores_empty_task_id(tmp_path):
    memory = Memory(tmp_path / "memory.db")
    memory.suppress_chat_task("s1", "")
    assert memory.get_suppressed_chat_tasks("s1") == []
