# Resilient Agent Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep Agent tasks and replies reliable across page navigation, then make the project workbench execute real project-scoped tasks.

**Architecture:** A small in-process supervisor owns runtime coroutines while SQLite remains the event source of truth. React starts a task and subscribes separately. Project actions resolve to stable project sessions and enter the same runtime.

**Tech Stack:** FastAPI, asyncio, SQLite, React, TypeScript, Vitest, pytest.

---

### Task 1: Background task ownership and replay

**Files:**
- Create: `src/agent/runtime/supervisor.py`
- Modify: `src/routes_agent.py`
- Test: `tests/test_agent_routes.py`

- [x] Write route tests proving a disconnected event subscriber does not cancel execution and replay returns ordered events plus one terminal `done`.
- [x] Run the focused tests and confirm they fail because start/replay endpoints do not exist.
- [x] Implement `AgentTaskSupervisor`, task start, event replay, active nonterminal lookup and duplicate-session rejection.
- [x] Run the focused tests and confirm they pass.

### Task 2: Guaranteed conversational finalization

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Modify: `src/agent/runtime/response_format.py`
- Modify: `src/agent/memory.py`
- Test: `tests/test_local_agent_runtime.py`
- Test: `tests/test_agent_memory.py`

- [x] Write failing tests for empty final model text, previous-task status questions and tool-free status replies.
- [x] Add previous-task lookup excluding the current task and a deterministic facts-based fallback response.
- [x] Make status-only follow-ups load prior facts and disable tools.
- [x] Verify focused runtime and memory tests.

### Task 3: React task recovery

**Files:**
- Modify: `src/web/src/lib/api.ts`
- Modify: `src/web/src/hooks/useChatStream.ts`
- Modify: `src/web/src/hooks/useChats.ts`
- Modify: `src/web/src/App.tsx`
- Test: `src/web/src/hooks/useChatStream.test.ts`
- Test: `src/web/src/lib/chatHistory.test.ts`

- [x] Write failing tests for start-then-subscribe, event replay and history refresh after remount.
- [x] Replace direct chat streaming with start/task-events APIs.
- [x] Restore running tasks, continue through process `error` events, and refresh canonical history whenever Chat becomes active.
- [x] Verify frontend tests and TypeScript build.

### Task 4: Project-scoped executable actions

**Files:**
- Modify: `src/agent/runtime/project_projection.py`
- Modify: `src/routes_agent.py`
- Modify: `src/web/src/components/project/ProjectWorkspaceView.tsx`
- Modify: `src/web/src/lib/api.ts`
- Modify: `src/web/src/types.ts`
- Modify: `src/web/src/index.css`
- Test: `tests/test_project_projection.py`
- Test: `tests/test_agent_routes.py`
- Test: `src/web/src/components/project/ProjectWorkspaceView.test.tsx`

- [x] Write failing tests for stable project session IDs, action validation and evidence-driven stage/next action.
- [x] Add five action prompts that bind the stable project session and use the shared task starter.
- [x] Add real action controls, active-task feedback and an “open project conversation” command.
- [x] Run backend/frontend focused tests, Vite build, desktop/mobile browser checks and the Impeccable detector.

### Task 5: Regression, runtime reload and record

**Files:**
- Modify: `项目推进记录/2026-08-14-P4-P5与项目工作台推进计划.md`

- [x] Run the complete backend and frontend suites.
- [x] Restart only after confirming no user task is running, then verify `127.0.0.1:7788` and the recovery flow.
- [x] Record the observed problem, root cause, changes, test counts and remaining limits.
