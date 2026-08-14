# Thread Event Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist one ordered, redacted event stream for every local Agent task and expose it through the existing task/activity APIs without changing the SSE wire format.

**Architecture:** Add an append-only `agent_events` table to the existing SQLite `Memory` store. `LocalAgentRuntime` records the same structured events that it emits through SSE, while existing tool-run and changeset tables remain as compatibility projections. Task detail responses include the ordered event stream so Activity can migrate to one source without breaking older clients.

**Tech Stack:** Python 3.10+, SQLite, existing FastAPI routes, existing React/TypeScript Activity view, pytest and Node test runner.

---

### Task 1: Add event-store persistence tests

**Files:**
- Modify: `tests/test_memory.py`
- Modify: `src/agent/memory.py`

- [ ] **Step 1: Write failing tests**

Add tests that append two events for one task and assert sequence numbers are `1, 2`, payloads round-trip as dictionaries, events from another task are isolated, and secret-like values are stored as `[REDACTED]`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_memory.py -q`

Expected: FAIL because `Memory.record_agent_event` and `Memory.get_agent_events` do not exist.

- [ ] **Step 3: Implement the minimal table and methods**

Create `agent_events(id, task_id, session_id, sequence, event_type, payload_json, created_at)` with a unique `(task_id, sequence)` constraint. `record_agent_event` assigns the next sequence inside a transaction and sanitizes nested keys containing `key`, `token`, `secret`, `password`, or `authorization`; `get_agent_events` returns ordered JSON-safe dictionaries.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same command and expect all memory tests to pass.

### Task 2: Record runtime events without changing SSE

**Files:**
- Modify: `tests/test_local_agent_runtime.py`
- Modify: `src/agent/runtime/runtime.py`

- [ ] **Step 1: Write a failing integration test**

Run a simple task with a fake LLM and task store, then assert the stored event types include `task_started` and `task_completed`, and a tool task includes `tool_call`, `tool_result`, and `file_changed` when applicable.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest tests\test_local_agent_runtime.py -q`

Expected: FAIL because no `agent_events` rows are written.

- [ ] **Step 3: Add one runtime recording boundary**

Add `_record_event(event)` and call it immediately before events are yielded from `run`, `resume`, and `_drive`. Record `task_started` after initial state creation, `context_compacted` when `_fit_context` creates a handoff, and terminal `task_completed`/`task_failed` events. Preserve all current event dictionaries and SSE ordering.

- [ ] **Step 4: Run focused and backend tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_local_agent_runtime.py tests\test_memory.py -q`, then `.venv\Scripts\python.exe -m pytest -q`.

### Task 3: Expose the unified stream through task detail

**Files:**
- Modify: `src/agent/memory.py`
- Modify: `src/routes_agent.py`
- Modify: `tests/test_routes_agent.py`

- [ ] **Step 1: Write a failing route test**

Assert `GET /api/agent/tasks/{task_id}` includes `activity.events` ordered by sequence while retaining `tool_runs` and `changesets` keys for compatibility.

- [ ] **Step 2: Implement response compatibility**

Extend `get_agent_task_activity` and empty activity responses to include `events: []`. Do not expose raw secret-like fields.

- [ ] **Step 3: Run route and backend tests**

Run: `.venv\Scripts\python.exe -m pytest tests\test_routes_agent.py -q` and then the full backend suite.

### Task 4: Record the phase and verify the product baseline

**Files:**
- Modify: `docs/diary/2026-08-10-thread-event-store.md`
- Modify: `项目推进记录/2026-08-10-Agent记忆系统分阶段改进记录.md`

- [ ] **Step 1: Record observed failures, root cause, exact commands, final effect, and residual risks**

- [ ] **Step 2: Run frontend tests and build**

Run: `npm test --prefix src\web` and `npm run build --prefix src\web`.

- [ ] **Step 3: Run 7788 smoke verification**

Start with `.venv\Scripts\python.exe src\main.py`, verify HTTP 200 and only `127.0.0.1:7788`, then stop the validation process.

- [ ] **Step 4: Commit implementation and diary locally**

Do not push GitHub or include ignored `tests/` and `项目推进记录/` files.
