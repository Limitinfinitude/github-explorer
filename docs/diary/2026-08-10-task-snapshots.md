# Diary: Task Model and Git Snapshots

本日记记录记忆系统最后一项事实锚定：模型、工作区和 Git 基线快照。

## Step 1: 范围与计划

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 补齐长期记忆的模型和 Git 来源后做总体验收。

**Inferred intent:** 后续回看任务时能准确知道执行环境，避免同一文件名在不同分支或模型配置下被误认为同一事实。

### What I did

确定任务启动时捕获一次不可变快照：workspace root/current path、项目 profile、Git branch/HEAD、model id/protocol/base URL。API Key 不进入参数和状态。

### Why

记忆内容没有版本基线就无法判断是否过期；模型配置没有快照则难以复现行为。

### What worked

路由已有不含 Key 的 `model_context`，runtime 已有 workspace root/current path，只需补充显式传递和 Git HEAD 读取。

### What didn't work

本步骤尚未改生产代码，后续以 TDD 证明缺少快照。

### What I learned

Git 基线应记录任务开始时 HEAD，而不是任务结束时猜测；工具可能修改工作树但不提交。

### What was tricky

不能为了获取 Git 信息执行任意 shell；读取 `.git/HEAD` 和 refs 足以覆盖当前普通仓库，worktree/packed refs 作为后续兼容项记录。

### What warrants review

检查模型快照绝不包含 Key，Git 快照失败必须降级为空而不阻断任务。

### Future work

完成后进行总体验收，并以真实使用指标决定 retention、UI 管理和 embeddings。

## Step 2: 快照实现与验证

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 完成模型、工作区和 Git 基线锚定。

**Inferred intent:** 任务和长期记忆必须可复现、可判断版本归属，且不存储密钥。

### What I did

扩展 `/src/agent/runtime/runtime.py` 的 `run()` 接收 `model_context`，仅保留 id/protocol/base_url。任务创建时生成 `workspace_snapshot`，包含 root/current path/profile/branch/head；读取普通 `.git` 目录和 `gitdir` 文件，失败时降级为空。`task_started` 事件和 `agent_tasks.state_json` 保存快照，派生项目记忆追加 Git 分支与基线。`/src/routes_agent.py` 将已有无 Key 模型上下文传入 runtime。

### Why

任务记忆必须知道产生时的版本和模型配置，否则后续分支切换或模型切换会让旧事实无法解释。

### What worked

定向测试实现后返回 `28 passed`，验证模型字段白名单、Git branch/HEAD、任务状态持久化和项目记忆中的 Git 基线。

### What didn't work

RED 返回 `3 failed, 25 passed`：`run()` 不接受 `model_context`，路由传入值为 None，项目记忆缺少 `Git分支`，均符合预期。

### What I learned

模型快照应由路由在选择模型后注入 runtime，而不是 runtime 再读取全局环境；这样一次任务内配置固定且测试可控。

### What was tricky

`.git` 既可能是目录也可能是指向真实 gitdir 的文本文件；读取失败必须不阻断 Agent。

### What warrants review

packed-refs 中的 HEAD 尚未解析；普通分支 ref 和 detached HEAD 已覆盖。未来遇到 packed-only 仓库时应增加解析测试。

### Future work

做总体验收并记录当前路线的达标边界。

## Step 3: 总体验收

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 验证完整产品基线并保持最新服务可测试。

**Inferred intent:** 记忆系统的所有核心层必须一起工作，不能只验证单个模块。

### What I did

执行后端、前端、构建和 7788 实机验证，检查观测端点和监听地址。

### Why

快照改动经过路由、runtime、SQLite 和发布包，必须覆盖端到端依赖。

### What worked

后端 `96 passed, 1 warning`；前端 `13 passed`；Vite 转换 `1613 modules`；真实服务 `HTTP 200`；observability retention 返回 `agent_tasks + agent_events + project_memories`；监听仅为 `127.0.0.1:7788`，PID `47832`。

### What didn't work

唯一 warning 仍为既有 Starlette/httpx 弃用提醒，没有新增失败。

### What I learned

当前核心链路已经覆盖指令、工作上下文、事件、会话、项目事实和环境快照，可以停止继续堆叠未经指标证明的记忆机制。

### What was tricky

“达标”不等于功能无限增加。embeddings、记忆管理 UI 和 retention 都需要真实使用数据或独立产品需求，不应在核心链路稳定前盲目加入。

### What warrants review

真实长时间运行后的数据库增长、FTS 命中率和跨标签页 UI 缓存仍需观测。

### Future work

先使用当前版本收集命中/误命中和数据库体积；达到阈值后再设计 retention、UI 管理或 embeddings。
