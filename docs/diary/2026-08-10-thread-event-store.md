# Diary: Thread Event Store

本日记记录第二阶段将 Agent 执行事实统一为 SQLite 事件流的实现过程。

## Step 1: 计划与边界

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 继续执行已批准的记忆系统路线，直到当前阶段达到可验证标准。

**Inferred intent:** Agent 的对话、工具、审批、验证和压缩必须能按一次任务完整回放，而不是分散在多个事实源中。

### What I did

检查了 `/src/agent/memory.py`、`/src/agent/runtime/runtime.py`、Agent 路由和 Activity 前端。确认现有 `agent_tasks`、`agent_tool_runs`、`agent_changesets` 是分散投影，尚无统一顺序事件表，因此本阶段建立 `agent_events`，保持旧字段兼容。

### Why

先统一事件事实源，后续才能让 Activity、resume 和 LangSmith trace 使用相同的 task/session/sequence 标识。此阶段不引入长期检索、向量库或新的前端状态协议。

### What worked

已有 SSE 事件都包含 `session_id`、`task_id` 和 `type`，可以在运行时边界统一持久化，不必改变前端现有事件格式。

### What didn't work

本步骤没有生产代码失败；事件表尚未实现，后续以 TDD 测试取得预期 RED。

### What I learned

旧的工具和变更表可以继续作为兼容投影，事件表不应删除它们，否则会破坏现有任务明细接口。

### What was tricky

工具参数和错误文本可能含有 API Key、Token 或 Authorization，事件落库前必须递归脱敏；事件序号必须在 SQLite 写入边界内递增，不能依赖前端收到事件的顺序。

### What warrants review

检查事件写入是否发生在 SSE 输出之前、审批暂停和 resume 是否保持同一 task_id，以及空任务/旧任务的兼容返回结构。

### Future work

完成事件流后，再把 Activity UI 从旧投影逐步迁移到 `activity.events`，并设计前后端 session 事实统一。

## Step 2: SQLite 事件表与脱敏

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 按第二阶段计划实现统一事件存储，并用测试证明顺序和安全边界。

**Inferred intent:** 同一 task 的事实必须有稳定顺序，且事件落库不能把模型密钥、Token 或 Authorization 写入长期存储。

### What I did

新增 `/src/agent/memory.py` 的 `agent_events` 表、`record_agent_event()` 和 `get_agent_events()`。事件按 task 局部 `sequence` 递增，payload 使用递归键和值脱敏。新增 `/tests/test_agent_memory.py` 覆盖顺序、task 隔离和 `api_key` 脱敏。

### Why

旧的 `agent_tool_runs` 和 `agent_changesets` 只能分别表达工具和文件事实，无法回放计划、审批、验证、错误和压缩的完整顺序。事件表作为事实源，旧表继续保留为兼容投影。

### What worked

新增存储测试在实现后通过，包含不同 task 隔离、序号 `1, 2` 和敏感字段 `[REDACTED]`。

### What didn't work

初始 RED 命令 `.venv\Scripts\python.exe -m pytest tests\test_agent_memory.py tests\test_local_agent_runtime.py tests\test_agent_routes.py -q` 返回 `3 failed, 27 passed`，失败原因是 `Memory` 尚无 `record_agent_event`，以及测试误用了不存在的 `ToolRisk.SAFE`；后者改为项目已有的 `ToolRisk.WRITE_SAFE`。

### What I learned

SQLite 的 `BEGIN IMMEDIATE` 能在取最大序号和插入之间保持同一连接事务边界，避免并发 task 写入产生重复序号。

### What was tricky

事件 payload 既要保留嵌套结构，又不能把 secret-like 键值写入数据库，因此脱敏必须递归处理 dict、list、tuple 和字符串。

### What warrants review

当前事件保留完整工具输出，未来需要增加按事件类型和任务生命周期的保留策略，避免长期数据库无限增长。

### Future work

把运行时事件写入统一边界，并将任务明细/API/Activity 接到事件流。

## Step 3: 运行时、API 与 Activity 接入

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 继续完成事件流的生产者和展示读取链路。

**Inferred intent:** SSE 行为不能回归，但同一事件必须在本地存储中可回看。

### What I did

修改 `/src/agent/runtime/runtime.py`：任务开始记录 `task_started`，所有 SSE 事件在输出前写入事件表，`done` 映射为 `task_completed`、`task_failed` 或 `task_waiting_approval`，压缩时记录 `context_compacted`。修改 `/src/routes_agent.py` 和 `/src/agent/memory.py`，任务明细包含 `activity.events`，并保留旧 `tool_runs`/`changesets` 字段。修改 `/src/web/src/types.ts`、`/src/web/src/lib/api.ts`、`/src/web/src/components/activity/ActivityView.tsx` 和 `/src/web/src/index.css`，在运行记录展开面板显示有序事件时间线。

### Why

运行时边界记录事件可以覆盖正常执行和审批 resume，而不需要让每个工具重复实现事件写入；旧字段继续返回，避免已有前端和恢复逻辑中断。

### What worked

定向后端测试返回 `30 passed`。生命周期回归验证了 `task_started`、`tool_call`、`tool_result`、`file_changed` 和最后的 `task_completed`，序号连续递增。

### What didn't work

真实服务第一次验收脚本使用 `$home`，PowerShell 报 `Cannot overwrite variable HOME because it is read-only or constant.`；内联 SQLite 查询同时因引号转义错误报 `SyntaxError: unterminated string literal`。两者都只影响检查脚本，没有修改服务代码或数据。改用任务专用变量和 PowerShell here-string 后通过。

### What I learned

PowerShell 变量名大小写不敏感，不能使用 `$home` 等系统保留变量名；复杂内联 Python 查询在 PowerShell 中应使用 here-string 传 stdin，避免多层引号破坏 SQL。

### What was tricky

SSE 的 `done` 是协议事件，而事件存储需要生命周期语义，因此只在存储层映射类型，保持发给浏览器的 `done` 不变。

### What warrants review

Activity 当前同时显示事件时间线和旧投影，后续可以在确认历史数据迁移后删除重复展示；本阶段保留重复字段是兼容策略。

### Future work

设计前后端 session 统一和事件保留/归档策略，再进入 SQLite FTS5 项目长期记忆。

## Step 4: 阶段验证

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 运行完整回归和真实服务验收。

**Inferred intent:** 只有代码测试、前端构建和 7788 实机边界都通过，第二阶段才算达标。

### What I did

执行 `.venv\Scripts\python.exe -m pytest -q`、`npm test --prefix src\web`、`npm run build --prefix src\web`。启动 `.venv\Scripts\python.exe src\main.py`，请求首页和不存在任务明细，查询 SQLite 表，再停止监听 PID。

### Why

覆盖后端行为、前端编译、数据库迁移和仅本地监听四个不同风险面。

### What worked

后端 `92 passed, 1 warning`；前端 `12 passed`；Vite 成功转换 `1612 modules`；真实首页 `HTTP 200`；不存在任务返回 `activity.events` 数量 `0`；SQLite 查询返回 `('agent_events',)`；`netstat` 仅显示 `127.0.0.1:7788`。

### What didn't work

唯一 warning 是既有 Starlette/httpx 弃用提醒，不是本阶段失败。验收进程已停止，端口已释放。

### What I learned

数据库表迁移在现有 SQLite 启动路径中自动完成，无需手工迁移脚本。

### What was tricky

Windows 启动器 PID 与最终 Python 监听 PID 可能不同，必须按 netstat 的监听 PID 停止验收进程。

### What warrants review

事件流暂未做归档和容量上限，长期运行后需要设计 retention；LangSmith 仍是可选外部观测，SQLite 事件流是本地事实源。

### Future work

下一阶段进入前后端 session 统一，再以事件事实为来源实现 SQLite FTS5 项目记忆。
