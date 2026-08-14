# Diary: SQLite FTS Project Memory

本日记记录第三阶段项目长期记忆的设计、实现、失败和验证。

## Step 1: 边界与计划

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 在事件和会话事实统一后继续实现可检索项目记忆。

**Inferred intent:** Agent 应记住同一工作区已经修改和验证过的事实，但不能跨项目串线，也不能把模型猜测或密钥当记忆。

### What I did

确定使用 SQLite FTS5，规范字段为 workspace、content、source type/ref、confidence、verification status、expiry 和时间戳。只允许完成任务的确定性文件/验证事实自动入库。

### Why

当前已有 SQLite 事实源和 ContextHandoff，FTS5 足以验证真实检索价值；现在引入 embeddings 会增加模型、维度、迁移和成本问题。

### What worked

现有 `Memory` 和 runtime task state 已包含工作区、目标、changed files 和 verification，可直接生成有来源的事实。

### What didn't work

本步骤尚未修改生产代码，后续以 TDD 取得缺少记忆 API 的 RED。

### What I learned

项目记忆必须与聊天历史分开：聊天保存用户和助手原文，项目记忆只保存可复用事实及来源。

### What was tricky

自动记忆最容易污染，因此必须限制写入条件，并以 source_ref 唯一约束实现幂等。

### What warrants review

检查过期过滤、workspace 隔离和 FTS 查询转义；任何一个错误都可能造成跨项目串线或查询异常。

### Future work

实现存储、runtime 检索/写入和只读搜索 API，再根据真实命中率决定是否需要 embeddings。

## Step 2: FTS5 存储与运行时集成

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 实现工作区隔离的可检索长期记忆，并接入 Agent 上下文。

**Inferred intent:** 已验证的项目事实应在后续相关任务中自动出现，同时保持可追溯和不跨项目。

### What I did

在 `/src/agent/memory.py` 新增 `project_memories` 规范表与 `project_memory_fts` FTS5 索引，实现同 source 幂等更新、递归脱敏、过期过滤和 verified-only 检索。在 `/src/agent/runtime/runtime.py` 中，任务开始时检索当前 workspace 的 verified 记忆并注入 system prompt；任务完成前仅将 changed files/verification 的确定性事实写入，普通聊天和失败任务不写。

### Why

规范表保存来源和状态，FTS 表只负责检索；分离后可以安全更新同一 task 的事实并保留审计字段。自动写入条件限制可以防止模型自由文本污染长期记忆。

### What worked

定向测试实现后返回 `33 passed`，覆盖 workspace 隔离、同源 upsert、过期记忆、verified-only、敏感字段脱敏、runtime 注入和完成任务自动记忆。

### What didn't work

RED 命令 `.venv\Scripts\python.exe -m pytest tests\test_agent_memory.py tests\test_local_agent_runtime.py tests\test_agent_routes.py -q` 返回 `4 failed, 29 passed`，所有失败均为缺少 `remember_project_fact` 或 `search_project_memories`，符合预期。

### What I learned

FTS5 的 `bm25()` 可以在 SQLite 内完成相关性排序，workspace、expiry 和 verification 则由规范表 SQL 条件控制，不需要向量数据库。

### What was tricky

FTS5 不会自动理解规范表 upsert，因此更新同一来源时必须在一个事务内删除旧索引行并插入新内容，否则搜索会返回陈旧事实。

### What warrants review

changed-only 任务写入 `partial`，运行时默认只检索 `verified`；这意味着未验证改动可审计但不会自动影响后续模型。

### Future work

积累真实命中与误命中数据，设置 retention，再决定是否需要 embeddings 或同义词扩展。

## Step 3: API 与完整验收

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 暴露只读检索并完成产品级回归。

**Inferred intent:** 项目记忆必须可观察、可验证，而不是隐藏在 prompt 内。

### What I did

在 `/src/routes_agent.py` 新增 `/api/agent/memory/search`，支持 workspace、query、limit 和 verified-only；更新本地 observability retention 描述。执行全量后端、前端测试与构建，重启 7788，查询实际 SQLite 表和空工作区搜索。

### Why

只读 API 便于 Activity 和未来设置页检查记忆来源，也为检索质量评估提供稳定入口。

### What worked

后端 `95 passed, 1 warning`；前端 `13 passed`；Vite 成功转换 `1613 modules`。真实服务 `HTTP 200`，空 workspace 搜索返回 0，SQLite 存在 `project_memories` 和 `project_memory_fts`，监听仅为 `127.0.0.1:7788`，当前 PID `42828`。

### What didn't work

唯一 warning 是既有 Starlette/httpx 弃用提醒。本步骤没有新增失败。

### What I learned

当前 Python SQLite 构建已启用 FTS5，不需要额外 native 依赖，适合本项目 Windows 本地部署。

### What was tricky

检索 API 必须要求 workspace 参数，不能仅按 query 搜索；否则再好的全文检索也会发生跨项目记忆串线。

### What warrants review

当前没有删除/归档和容量上限，也没有 UI 管理记忆。API 已提供来源和状态，下一步应优先做 retention 与观察，而不是立即引入 embeddings。

### Future work

进行当前路线的总体验收，补齐 session 模型/工作区快照和 Git 变更来源后即可认为核心记忆链路达标。
