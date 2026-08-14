# Agent Memory Foundation Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with test-first checkpoints.

**Goal:** Add scoped `AGENTS.md` instruction loading and structured context handoff compaction to the current LocalAgentRuntime without changing the SSE protocol or adding a new database table.

**Architecture:** Add a pure `InstructionLoader` that discovers user/project/override instruction files within the resolved workspace boundary. Add a pure `CompactionEngine` that extracts deterministic task facts, optionally validates a model-produced JSON handoff, and assembles a bounded context containing the handoff plus recent messages. `LocalAgentRuntime` captures instruction and compaction state in `agent_tasks.state_json` while preserving the complete original message list.

**Tech Stack:** Python 3.10+, pathlib, dataclasses, existing `WorkspaceManager`, existing pytest suite, existing async LLM callback.

---

### Task 1: Add instruction loader tests

**Files:**
- Create: `tests/test_runtime_instructions.py`

- [ ] **Step 1: Write failing tests for hierarchy and override behavior**

```python
def test_loader_collects_user_root_and_nested_instructions_in_order(tmp_path, monkeypatch):
    user_dir = tmp_path / "user"
    root = tmp_path / "project"
    nested = root / "src"
    nested.mkdir(parents=True)
    (user_dir / "AGENTS.md").parent.mkdir()
    (user_dir / "AGENTS.md").write_text("user rule", encoding="utf-8")
    (root / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (nested / "AGENTS.md").write_text("nested rule", encoding="utf-8")

    result = InstructionLoader(workspace_root=root, current_path=nested, user_home=user_dir).load()

    assert [item.scope for item in result.sources] == ["user", "project", "directory"]
    assert result.rendered.index("user rule") < result.rendered.index("root rule")
    assert result.rendered.index("root rule") < result.rendered.index("nested rule")


def test_loader_override_replaces_same_directory_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENTS.md").write_text("normal", encoding="utf-8")
    (root / "AGENTS.override.md").write_text("override", encoding="utf-8")

    result = InstructionLoader(root, root, user_home=tmp_path / "missing").load()

    assert "override" in result.rendered
    assert "normal" not in result.rendered


def test_loader_does_not_walk_above_workspace_root(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "project"
    root.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("outside", encoding="utf-8")

    result = InstructionLoader(root, root, user_home=tmp_path / "missing").load()

    assert result.sources == []
```

- [ ] **Step 2: Run the focused tests and verify the expected import failure**

Run: `python -m pytest tests/test_runtime_instructions.py -q`

Expected: FAIL because `agent.runtime.instructions` and `InstructionLoader` do not exist.

### Task 2: Implement bounded instruction loading

**Files:**
- Create: `src/agent/runtime/instructions.py`
- Modify: `.gitignore`
- Test: `tests/test_runtime_instructions.py`

- [ ] **Step 1: Implement the minimal public data model and loader**

Implement `InstructionSource`, `InstructionContext`, and `InstructionLoader.load()` with these exact rules:

- `user_home / AGENTS.md` is loaded first when present.
- Directories from workspace root to current path are traversed only while current path is inside root.
- `AGENTS.override.md` wins over `AGENTS.md` in the same directory.
- Total source content is bounded by `max_bytes=32768`; reserve remaining bytes for more-specific directories.
- Unreadable or invalid UTF-8 files are skipped and returned in `warnings`.
- `rendered` includes source labels and content but no executable interpretation.

- [ ] **Step 2: Run focused tests and verify GREEN**

Run: `python -m pytest tests/test_runtime_instructions.py -q`

Expected: all instruction tests pass.

- [ ] **Step 3: Add tests for budget and malformed files**

```python
def test_loader_preserves_more_specific_rule_when_budget_is_small(tmp_path):
    root = tmp_path / "project"
    nested = root / "src"
    nested.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rule " * 1000, encoding="utf-8")
    (nested / "AGENTS.md").write_text("specific rule", encoding="utf-8")

    result = InstructionLoader(root, nested, user_home=tmp_path / "missing", max_bytes=100).load()

    assert "specific rule" in result.rendered
    assert result.warnings
```

- [ ] **Step 4: Run instruction tests again**

Run: `python -m pytest tests/test_runtime_instructions.py -q`

Expected: all tests pass.

### Task 3: Add compaction tests

**Files:**
- Create: `tests/test_runtime_compaction.py`

- [ ] **Step 1: Write failing tests for deterministic handoff and bounded assembly**

```python
def test_compaction_extracts_workspace_changes_verification_and_failures():
    state = {
        "user_message": "启动服务并验证",
        "summary": {
            "changed_files": ["app.py"],
            "verification": [{"name": "pytest", "success": True}],
            "processes": [{"process_id": 42, "status": "running"}],
        },
        "workspace_root": "C:/project",
        "current_path": "C:/project/src",
        "messages": [
            {"role": "assistant", "content": "工具失败：端口被占用"},
        ],
    }

    handoff = CompactionEngine().deterministic_handoff(state)

    assert handoff.goal == "启动服务并验证"
    assert handoff.changed_files == ["app.py"]
    assert handoff.verification[0]["success"] is True
    assert "端口被占用" in handoff.failures


def test_compaction_keeps_latest_user_request_and_fits_budget():
    messages = [
        {"role": "user", "content": "old" * 10000},
        {"role": "assistant", "content": "old result" * 10000},
        {"role": "user", "content": "latest request"},
    ]

    fitted, handoff = CompactionEngine(max_tokens=1000).compact(
        system="system", messages=messages, state={"user_message": "latest request", "summary": {}},
    )

    assert fitted[-1]["content"] == "latest request"
    assert any("ContextHandoff" in str(item["content"]) for item in fitted)
    assert CompactionEngine.estimate_tokens("system", fitted) <= 1000
    assert handoff.source_message_count == 3
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m pytest tests/test_runtime_compaction.py -q`

Expected: FAIL because `agent.runtime.compaction` does not exist.

### Task 4: Implement structured compaction

**Files:**
- Create: `src/agent/runtime/compaction.py`
- Test: `tests/test_runtime_compaction.py`

- [ ] **Step 1: Implement `ContextHandoff` and deterministic facts**

Use dataclasses and JSON-safe dictionaries. Extract goal, progress, decisions, constraints, workspace paths, changed files, verification, process facts, failures, pending items, references, and source message count. Filter secret-like key/value pairs before rendering.

- [ ] **Step 2: Implement bounded message assembly**

`compact(system, messages, state)` must preserve the latest user message, prepend one `ContextHandoff` message, then retain the newest prior messages until the configured budget. It must not mutate the input list.

- [ ] **Step 3: Implement optional model handoff validation**

Add a pure `parse_model_handoff(payload, fallback)` helper that accepts a JSON object only when required string/list fields have safe types; otherwise return the deterministic fallback. The runtime will use this helper only after the first integration pass.

- [ ] **Step 4: Run compaction tests and verify GREEN**

Run: `python -m pytest tests/test_runtime_compaction.py -q`

Expected: all compaction tests pass.

### Task 5: Integrate instruction snapshots and compaction into runtime

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Modify: `src/agent/runtime/__init__.py`
- Test: `tests/test_local_agent_runtime.py`

- [ ] **Step 1: Add failing integration tests**

Add tests that assert:

- `LocalAgentRuntime` accepts an optional `instruction_loader`.
- The first LLM call system prompt contains the loaded instruction text.
- Task state contains `instruction_sources` and `context_handoff` after an oversized history is sent.
- Full `state["messages"]` remains available through the task store while the captured model messages are compacted.

- [ ] **Step 2: Run the focused integration tests and verify RED**

Run: `python -m pytest tests/test_local_agent_runtime.py -q`

Expected: FAIL on the new constructor/state assertions.

- [ ] **Step 3: Integrate with minimal runtime changes**

Add optional `instruction_loader` and `compaction_engine` dependencies with defaults. At task creation, load and snapshot instructions. Render instruction context into `_system_prompt`. Replace `_fit_context` with the compaction engine; save `context_handoff`, `compaction_count`, and `compacted_message_count` into state without deleting messages. Preserve existing continuation, approval, tool-round and response-format behavior.

- [ ] **Step 4: Run focused and full backend tests**

Run: `python -m pytest tests/test_runtime_instructions.py tests/test_runtime_compaction.py tests/test_local_agent_runtime.py -q`

Expected: all focused tests pass.

Run: `python -m pytest -q`

Expected: all existing backend tests pass.

### Task 6: Record results and verify the full product baseline

**Files:**
- Modify: `docs/diary/2026-08-10-agent-memory-foundation.md`
- Modify: `项目推进记录/2026-08-10-Agent记忆系统分阶段改进记录.md`

- [ ] **Step 1: Record exact commands, failures, root cause, implementation and effects**

Append a complete Step 2 entry with the actual RED error, GREEN result, changed files, fallback behavior and known residual risks. Do not claim model-based compaction if only deterministic compaction was implemented.

- [ ] **Step 2: Run frontend tests and production build**

Run: `npm test --prefix src/web`

Expected: 12 tests pass.

Run: `npm run build --prefix src/web`

Expected: Vite build succeeds.

- [ ] **Step 3: Run service verification**

Start with `.venv\Scripts\python.exe src\main.py`, verify `http://127.0.0.1:7788/` returns 200 and `netstat -ano` shows only `127.0.0.1:7788`, then stop the validation process.

- [ ] **Step 4: Commit only implementation and diary/design files**

Do not include `tests/` or `项目推进记录/` in the GitHub publish commit because this repository intentionally excludes them. Keep the local project progress record updated even though it is ignored.
