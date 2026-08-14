# Task Model and Git Snapshot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Anchor every task and derived project memory to its workspace, model configuration, Git branch, and starting HEAD without storing credentials.

**Architecture:** The route passes its already-sanitized model context into `LocalAgentRuntime.run`. Runtime captures a read-only workspace snapshot at task creation by reading `.git/HEAD` and the referenced branch file, stores it in task state/events, and includes branch/HEAD in deterministic project memory.

**Tech Stack:** pathlib, existing runtime state, SQLite JSON task persistence, pytest.

---

### Task 1: Add snapshot RED tests

- Modify `tests/test_local_agent_runtime.py` to assert `model_context` and `workspace_snapshot` persistence and Git baseline in derived memory.
- Modify `tests/test_agent_routes.py` fake runtime signature to assert the route forwards model context without API keys.
- Run focused tests and confirm failures.

### Task 2: Implement minimal snapshots

- Extend `LocalAgentRuntime.run(..., model_context=None)`.
- Capture root/current path, profile, branch and HEAD at task start without invoking shell commands.
- Persist the snapshot in `state_json` and `task_started` event.
- Add branch/HEAD to deterministic task memory content.
- Pass the existing sanitized model context from `routes_agent.py`.

### Task 3: Verify and record

- Run focused and full backend tests, frontend tests and build.
- Restart 7788 and verify only loopback listening.
- Update diary and local progress record.
- Commit locally; do not push.
