import json
from datetime import datetime, timedelta

import pytest

from agent.memory import Memory


def _seed_usage_events(store: Memory) -> None:
    now = datetime.utcnow()
    events = []
    for index in range(3):
        events.append((
            f"task-{index % 2}",
            json.dumps({"usage": {"input_tokens": 100 * (index + 1), "output_tokens": 10}}),
            (now - timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"),
        ))
    # 一个 6 小时前的旧事件：应计入 by_day 但不计入 5h 窗口
    events.append((
        "task-old",
        json.dumps({"usage": {"input_tokens": 1000, "output_tokens": 100}}),
        (now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
    ))
    for sequence, (task_id, payload, created) in enumerate(events, start=1):
        store.conn.execute(
            """INSERT INTO agent_events (task_id, session_id, sequence, event_type, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, "s", sequence, "model_request_completed", payload, created),
        )
    store.conn.commit()


def test_token_usage_aggregates_by_day_task_and_window(tmp_path):
    store = Memory(tmp_path / "usage.db")
    _seed_usage_events(store)

    usage = store.get_token_usage(days=7, top=5)

    assert usage["total"]["calls"] == 4
    assert usage["total"]["input_tokens"] == 1000 + 100 + 200 + 300
    assert usage["total"]["output_tokens"] == 100 + 10 * 3
    assert usage["total"]["total_tokens"] == usage["total"]["input_tokens"] + usage["total"]["output_tokens"]
    # 5 小时窗口只含前三个事件
    assert usage["last_5h"]["calls"] == 3
    assert usage["last_5h"]["input_tokens"] == 600
    # 聚合行都带 total_tokens
    assert all("total_tokens" in day for day in usage["by_day"])
    assert all("total_tokens" in task for task in usage["top_tasks"])
    # 消耗最大的任务排最前（task-old: 1100）
    assert usage["top_tasks"][0]["task_id"] == "task-old"


def test_token_usage_empty_db_returns_zeroes(tmp_path):
    store = Memory(tmp_path / "empty.db")
    usage = store.get_token_usage()
    assert usage["total"]["calls"] == 0
    assert usage["total"]["total_tokens"] == 0
    assert usage["by_day"] == []
    assert usage["top_tasks"] == []
