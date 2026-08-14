# Session Fact Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SQLite conversation history the canonical source for chat messages while preserving localStorage only as a resilient UI cache.

**Architecture:** Keep the existing numeric UI chat id and stable backend `sessionId`. Add a small pure history-to-message adapter, expose the existing history endpoint through the frontend API, and hydrate each browser chat once after startup. Server history replaces stale cached messages when available; a failed request leaves the cache untouched.

**Tech Stack:** Existing React hooks, TypeScript, Fetch API, existing FastAPI history endpoint, Node test runner.

---

### Task 1: Define history conversion behavior

**Files:**
- Create: `src/web/src/lib/chatHistory.ts`
- Create: `src/web/tests/chatHistory.test.ts`

- [ ] **Step 1: Write a failing test**

Assert role/content/timestamp mapping, stable ids based on session and index, and omission of malformed roles.

- [ ] **Step 2: Run RED**

Run: `npm test --prefix src\web`

Expected: FAIL because `chatHistory.ts` does not exist.

- [ ] **Step 3: Implement the pure adapter**

Convert only `user` and `assistant` rows to `Message`, use the server timestamp when valid, and fall back to the current ISO time for missing timestamps.

- [ ] **Step 4: Run GREEN**

Run the same command and expect all tests to pass.

### Task 2: Hydrate cached chats from SQLite history

**Files:**
- Modify: `src/web/src/lib/api.ts`
- Modify: `src/web/src/hooks/useChats.ts`
- Modify: `src/web/src/App.tsx`

- [ ] **Step 1: Add API and hook tests through the adapter contract**

The adapter test remains the deterministic regression; UI integration is verified by TypeScript build and a manual network-failure fallback.

- [ ] **Step 2: Implement `api.getHistory(sessionId)`**

Fetch `/api/agent/history/{sessionId}` and return the typed history array, raising a readable error for non-2xx responses.

- [ ] **Step 3: Add `hydrateChat(chatId)` to `useChats`**

Fetch the canonical history and replace only that chat's messages. Do not clear cached messages on failure. Preserve the existing title unless it is still `新对话`.

- [ ] **Step 4: Hydrate each chat once in `App`**

Use a ref-backed set of session ids so state updates do not refetch the same chat. New chats are marked hydrated after an empty response.

- [ ] **Step 5: Run tests and build**

Run: `npm test --prefix src\web` and `npm run build --prefix src\web`.

### Task 3: Record and verify

**Files:**
- Modify: `docs/diary/2026-08-10-session-fact-unification.md`
- Modify: `项目推进记录/2026-08-10-Agent记忆系统分阶段改进记录.md`

- [ ] **Step 1: Record exact findings, failures, commands, effects, and residual risks**
- [ ] **Step 2: Run backend regression and 7788 smoke test**
- [ ] **Step 3: Commit locally without pushing**
