# P3 Session Requirement Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist and enforce unfinished requirements across multiple Agent turns in one session.

**Architecture:** Add a focused SQLite ledger API to `Memory`, then let `LocalAgentRuntime` merge explicit requirements at task start and settle them from the existing evidence-based acceptance report. Preserve existing behavior when no persistent task store is configured.

**Tech Stack:** Python 3.12, SQLite, FastAPI runtime, pytest

---

### Task 1: Persistent session requirement ledger

**Files:**
- Modify: `src/agent/memory.py`
- Test: `tests/test_agent_memory.py`

- [ ] Add a failing test that creates two requirements, repeats one, completes one, and verifies stable positions plus one remaining pending item.
- [ ] Run `\.venv\Scripts\python.exe -m pytest -q tests/test_agent_memory.py -k session_requirement` and confirm failure because the ledger API is absent.
- [ ] Add the `session_requirements` table and minimal `merge_session_requirements`, `list_session_requirements`, and `settle_session_requirements` methods.
- [ ] Re-run the focused test and confirm it passes.

### Task 2: Runtime multi-turn enforcement

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Test: `tests/test_local_agent_runtime.py`

- [ ] Add a failing two-turn test: turn one completes requirement 1 but marks requirement 2 unfinished; turn two says “继续”, receives requirement 2 in its system prompt, supplies real evidence, and closes it.
- [ ] Run the focused runtime test and confirm failure because backlog is not loaded or settled.
- [ ] At task start, merge explicit material requirements into the store and replace per-turn criteria with all pending session requirements.
- [ ] After evaluation, settle passed items and retain unverified/failed items; emit backlog facts in task state and events.
- [ ] Re-run the focused runtime test and related acceptance tests.

### Task 3: Regression, live state, and Chinese records

**Files:**
- Modify: `项目推进记录/2026-08-14-P3会话需求账本推进日志.md`
- Modify: `项目推进记录/2026-08-14-P3会话需求账本验收报告.md`

- [ ] Run the full backend suite with `\.venv\Scripts\python.exe -m pytest -q`.
- [ ] Run frontend tests and production build from `src/web`.
- [ ] Restart only the process listening on 7788 and verify local-only binding, active deepseek model, HTTP 200, and a healthy requirement-ledger schema.
- [ ] Record RED/GREEN evidence, discovered limitations, and the next P3 batch in Chinese.
