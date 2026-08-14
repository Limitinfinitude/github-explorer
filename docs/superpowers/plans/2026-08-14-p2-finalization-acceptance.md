# P2 Finalization and Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent false completion after diagnostic-only work, preserve approval-resume facts, and always emit a useful terminal error when finalization fails.

**Architecture:** Keep the existing `LocalAgentRuntime` state machine. Add one deterministic task-intent fact at task creation, use it as a terminal completion gate, broaden acceptance section parsing without weakening evidence requirements, and build terminal errors from exception type plus the persisted execution summary.

**Tech Stack:** Python 3.11+, pytest, existing runtime event ledger and `WorkProductEvaluator`.

---

### Task 1: Completion evidence gate

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Test: `tests/test_local_agent_runtime.py`

- [ ] Add a failing test proving an implementation request with only diagnostic reads cannot finish as `completed`.
- [ ] Add a control test proving a read-only inspection can complete without file changes.
- [ ] Persist `requires_material_change` when the task starts and include missing material evidence in terminal status calculation.
- [ ] Run the focused runtime tests.

### Task 2: Acceptance parser compatibility

**Files:**
- Modify: `src/agent/runtime/acceptance.py`
- Test: `tests/test_work_product_evaluator.py`

- [ ] Add a failing test for the common `**[1] ...**` numbered Markdown response shape.
- [ ] Expand section parsing to accept bracketed and bold numbering while retaining explicit evidence validation.
- [ ] Run evaluator tests.

### Task 3: Non-empty terminal failure

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Test: `tests/test_local_agent_runtime.py`

- [ ] Add a failing test where the LLM throws `RuntimeError()` after a successful tool result.
- [ ] Build a fallback terminal message containing exception type and persisted file/verification/process facts.
- [ ] Verify both SSE `error` and `done` contain the fallback and the task remains `failed`.

### Task 4: Regression and live acceptance

**Files:**
- Modify: `项目推进记录/2026-08-14-双会话多轮验收推进日志.md`
- Modify: `项目推进记录/2026-08-14-P2后双会话多轮验收报告.md`

- [ ] Run focused tests and the full backend suite in the project `.venv`.
- [ ] Restart Explorer on `127.0.0.1:7788` if required and verify settings plus a deterministic SSE smoke task.
- [ ] Record RED/GREEN evidence, remaining limitations, and the resulting P2 acceptance status in Chinese.
