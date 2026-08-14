# SQLite FTS Project Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add workspace-scoped long-term project memory with exact provenance, verification state, confidence, expiry, and FTS5 retrieval.

**Architecture:** Store canonical memory rows in `project_memories` and a matching FTS5 index. Runtime writes only deterministic task facts after completed tasks with changed files or verification, and retrieves relevant non-expired memories for the current workspace before the first LLM call. No embeddings or model-generated memory is introduced.

**Tech Stack:** Python, SQLite FTS5, existing `Memory`, `LocalAgentRuntime`, pytest.

---

### Task 1: Build the scoped memory store

**Files:**
- Modify: `tests/test_agent_memory.py`
- Modify: `src/agent/memory.py`

- [ ] Write RED tests for upsert by source, workspace isolation, FTS search, verified-only filtering, expiry, and secret redaction.
- [ ] Run `.venv\Scripts\python.exe -m pytest tests\test_agent_memory.py -q` and confirm missing methods fail.
- [ ] Create `project_memories` plus `project_memory_fts`; implement `remember_project_fact()` and `search_project_memories()`.
- [ ] Run the focused test and confirm GREEN.

### Task 2: Retrieve and write deterministic task memory

**Files:**
- Modify: `tests/test_local_agent_runtime.py`
- Modify: `src/agent/runtime/runtime.py`

- [ ] Add a failing test proving relevant workspace memory enters the system prompt.
- [ ] Add a failing test proving a completed changed/verified task upserts one task-sourced memory.
- [ ] Implement retrieval at task creation and one idempotent task-memory write before terminal SSE completion.
- [ ] Do not store plain chat, failed tasks, or tasks without changed/verification facts.
- [ ] Run focused and full backend tests.

### Task 3: Observe and verify

**Files:**
- Modify: `src/routes_agent.py`
- Modify: `tests/test_agent_routes.py`
- Modify: `docs/diary/2026-08-10-project-memory-fts.md`
- Modify: `项目推进记录/2026-08-10-Agent记忆系统分阶段改进记录.md`

- [ ] Add `/api/agent/memory/search` with required workspace/query and bounded limit; return provenance fields, never secrets.
- [ ] Test workspace isolation at the route.
- [ ] Run backend, frontend and build regression.
- [ ] Verify 7788, record exact results, and commit locally without pushing.
