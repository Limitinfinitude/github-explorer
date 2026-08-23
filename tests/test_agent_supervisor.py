import asyncio

import pytest

from agent.memory import Memory
from agent.runtime.supervisor import AgentTaskSupervisor


class ControlledRuntime:
    def __init__(self, store: Memory):
        self.store = store
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def register_task(self, session_id: str, task_id: str) -> None:
        self.store.save_agent_task({
            "task_id": task_id,
            "session_id": session_id,
            "user_message": "",
            "status": "pending",
            "summary": {},
        })

    async def run(self, session_id, user_message, history=None, task_id=None, model_context=None, approval_mode="confirm"):
        state = self.store.get_agent_task(task_id)
        state.update(user_message=user_message, status="running")
        self.store.save_agent_task(state)
        self.store.record_agent_event({
            "task_id": task_id,
            "session_id": session_id,
            "type": "task_started",
        })
        self.started.set()
        yield {"type": "plan", "task_id": task_id, "session_id": session_id, "steps": ["执行"]}

        await self.release.wait()
        state.update(status="completed", final_text="已完成")
        self.store.save_agent_task(state)
        self.store.record_agent_event({
            "task_id": task_id,
            "session_id": session_id,
            "type": "token",
            "content": "已完成",
        })
        self.store.record_agent_event({
            "task_id": task_id,
            "session_id": session_id,
            "type": "task_completed",
            "content": "已完成",
            "status": "completed",
        })
        yield {"type": "done", "task_id": task_id, "session_id": session_id, "content": "已完成", "status": "completed"}


def test_disconnecting_subscriber_does_not_cancel_background_task(tmp_path):
    async def scenario():
        store = Memory(tmp_path / "supervisor.db")
        runtime = ControlledRuntime(store)
        supervisor = AgentTaskSupervisor(runtime, store, poll_interval=0.001)

        task_id = supervisor.start("session-a", "执行长任务")
        await runtime.started.wait()

        subscription = supervisor.subscribe(task_id)
        first = await anext(subscription)
        assert first["type"] == "task_started"
        await subscription.aclose()

        runtime.release.set()
        await supervisor.wait(task_id)
        return store.get_agent_task(task_id)

    task = asyncio.run(scenario())

    assert task["status"] == "completed"
    assert task["final_text"] == "已完成"


def test_reconnect_after_page_switch_replays_final_reply(tmp_path):
    async def scenario():
        store = Memory(tmp_path / "page-switch.db")
        runtime = ControlledRuntime(store)
        supervisor = AgentTaskSupervisor(runtime, store, poll_interval=0.001)

        task_id = supervisor.start("session-a", "执行长任务")
        await runtime.started.wait()
        first_subscription = supervisor.subscribe(task_id)
        first = await anext(first_subscription)
        assert first["type"] == "task_started"
        await first_subscription.aclose()

        runtime.release.set()
        await supervisor.wait(task_id)
        replayed = [event async for event in supervisor.subscribe(task_id, after_sequence=1)]
        return replayed

    replayed = asyncio.run(scenario())

    assert [event["type"] for event in replayed] == ["token", "done"]
    assert replayed[-1]["content"] == "已完成"


def test_replay_is_ordered_and_emits_one_done_event(tmp_path):
    async def scenario():
        store = Memory(tmp_path / "replay.db")
        runtime = ControlledRuntime(store)
        supervisor = AgentTaskSupervisor(runtime, store, poll_interval=0.001)

        task_id = supervisor.start("session-a", "执行任务")
        await runtime.started.wait()
        runtime.release.set()
        await supervisor.wait(task_id)

        all_events = [event async for event in supervisor.subscribe(task_id)]
        replayed = [event async for event in supervisor.subscribe(task_id, after_sequence=1)]
        return all_events, replayed

    all_events, replayed = asyncio.run(scenario())

    assert [event["type"] for event in all_events] == ["task_started", "token", "done"]
    assert [event["sequence"] for event in all_events] == [1, 2, 3]
    assert sum(event["type"] == "done" for event in all_events) == 1
    assert [event["type"] for event in replayed] == ["token", "done"]


def test_start_rejects_a_second_nonterminal_task_for_the_same_session(tmp_path):
    async def scenario():
        store = Memory(tmp_path / "duplicate.db")
        runtime = ControlledRuntime(store)
        supervisor = AgentTaskSupervisor(runtime, store, poll_interval=0.001)

        supervisor.start("session-a", "first")
        await runtime.started.wait()
        with pytest.raises(ValueError, match="已有任务正在运行"):
            supervisor.start("session-a", "second")
        runtime.release.set()

    asyncio.run(scenario())


def test_resume_interrupted_task_reuses_task_id_and_rejects_second_resume(tmp_path):
    class ResumeRuntime:
        def __init__(self, store):
            self.store = store
            self.calls = []

        def register_task(self, session_id, task_id):
            return None

        async def resume_interrupted(self, session_id, task_id, model_context=None):
            self.calls.append((session_id, task_id, model_context))
            state = self.store.get_agent_task(task_id)
            state["status"] = "completed"
            state["final_text"] = "resumed"
            self.store.save_agent_task(state)
            self.store.record_agent_event({
                "task_id": task_id,
                "session_id": session_id,
                "type": "task_completed",
                "content": "resumed",
                "status": "completed",
            })
            yield {"type": "done", "task_id": task_id, "session_id": session_id, "status": "completed", "content": "resumed"}

    async def scenario():
        store = Memory(tmp_path / "resume-supervisor.db")
        store.save_agent_task({
            "task_id": "interrupted-task",
            "session_id": "session-a",
            "user_message": "resume",
            "status": "interrupted",
            "resume_available": True,
            "summary": {},
        })
        runtime = ResumeRuntime(store)
        supervisor = AgentTaskSupervisor(runtime, store, poll_interval=0.001)
        task_id = supervisor.resume_interrupted("session-a", "interrupted-task", model_context={"id": "test"})
        assert task_id == "interrupted-task"
        with pytest.raises(ValueError, match="正在恢复|不可恢复"):
            supervisor.resume_interrupted("session-a", "interrupted-task", model_context={"id": "test"})
        await supervisor.wait(task_id)
        return runtime.calls, store.get_agent_task(task_id)

    calls, state = asyncio.run(scenario())
    assert calls == [("session-a", "interrupted-task", {"id": "test"})]
    assert state["status"] == "completed"
