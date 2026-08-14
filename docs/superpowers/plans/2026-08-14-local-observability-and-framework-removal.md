# 本地观测与框架退场实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `LocalAgentRuntime + SQLite Event Store` 成为唯一生产编排与观测链路，彻底移除 LangGraph/LangSmith，并修复基准测试暴露的状态、证据和审批事件缺陷。

**Architecture:** Agent 的任务状态、模型调用、工具调用、审批、文件、验证、进程和终态全部追加到 SQLite 事件表；前端只读取本地投影。旧图端点迁移到 Runtime 或明确退役，不再保留第二套图编排或外部 Trace SDK。

**Tech Stack:** FastAPI、SQLite、Python `.venv`、React、TypeScript、pytest、Vitest。

---

### Task 1: 锁定基准测试缺陷

**Files:**
- Modify: `tests/test_local_agent_runtime.py`
- Create: `tests/test_framework_removal.py`

- [ ] 增加最终回复含 `[未完成]` 时任务不得标记 `completed` 的回归测试。
- [ ] 增加 Windows 绝对路径证据匹配工作区相对变更文件的回归测试。
- [ ] 增加审批恢复后持久化 `approval_resolved` 的回归测试。
- [ ] 增加源码和默认依赖中无 LangGraph/LangSmith 的门禁测试。
- [ ] 运行聚焦测试并确认失败原因分别对应缺失行为。

### Task 2: 修复 Runtime 状态与证据判断

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Modify: `src/agent/runtime/acceptance.py`

- [ ] 用统一终态函数扫描明确未完成声明，并结合失败工具、验证和验收账本决定状态。
- [ ] 将任务 `workspace_root` 注入证据评估，规范化绝对/相对 Windows 路径。
- [ ] 在审批恢复入口追加包含决定、工具和调用 ID 的 `approval_resolved` 事件。
- [ ] 运行聚焦测试，确认红灯转绿且既有 Runtime 测试不回归。

### Task 3: 建立本地模型调用观测

**Files:**
- Modify: `src/agent/runtime/runtime.py`
- Modify: `src/agent/memory.py`
- Modify: `tests/test_local_agent_runtime.py`

- [ ] 在每次模型请求前后追加 `model_request_started/completed/failed`。
- [ ] 记录模型标识、协议、延迟、stop reason 和可用 usage，不记录 Key 或完整敏感提示词。
- [ ] 确认事件顺序和失败场景都可由 SQLite 回放。

### Task 4: 移除 LangGraph 与 LangSmith

**Files:**
- Delete: `src/agent/graph.py`
- Delete: `src/agent/state.py`
- Delete: `src/agent/nodes.py`
- Delete: `src/agent/swarm_graph.py`
- Delete: `src/agent/swarm_state.py`
- Delete: `src/agent/subgraphs/`
- Delete: `src/agent/runtime/legacy.py`
- Delete: `requirements-legacy.txt`
- Modify: `src/main.py`
- Modify: `src/routes_agent.py`
- Modify: `src/routes_search.py`
- Modify: `src/agent/__init__.py`
- Modify: `src/agent/runtime/tracing.py`
- Modify: `src/agent/tools/*.py`

- [ ] 删除图、Swarm、装饰器和外部 Trace SDK 代码。
- [ ] 将仍有兼容价值的入口映射到 Runtime；实验性 Swarm 返回明确退役响应。
- [ ] 删除默认和 legacy 依赖文件，确保应用在两套包均未安装时启动。
- [ ] 运行后端路由和源码门禁测试。

### Task 5: 前端切换为唯一的本地观测

**Files:**
- Modify: `src/web/src/components/activity/ActivityView.tsx`
- Modify: `src/web/src/types.ts`
- Delete: `src/web/src/lib/observability.ts`
- Modify: `src/web/src/index.css`

- [ ] 移除 LangSmith 状态卡和类型。
- [ ] 展示 SQLite 事件存储覆盖的模型、工具、审批、文件、验证、进程与终态。
- [ ] 保留现有运行记录、项目证据和筛选能力。
- [ ] 运行 Vitest 和生产构建。

### Task 6: 全量验收和记录

**Files:**
- Create: `项目推进记录/2026-08-14-LangGraph-LangSmith退场与本地观测验收报告.md`

- [ ] 运行全部 pytest、Vitest 和 Vite build。
- [ ] 扫描生产源码和依赖，确认无 `langgraph|langsmith`。
- [ ] 用同规格中型项目复测明确未完成、路径证据和审批事件。
- [ ] 重启 7788 并验证仅监听 `127.0.0.1`。
- [ ] 按“遇到/发现/分析/计划/效果”写中文验收记录。
