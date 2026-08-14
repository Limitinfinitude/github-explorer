# Diary: Agent 记忆系统第一阶段

本日记记录 GitHub Explorer 借鉴 Claude Code、Codex 和 Aider 后，对记忆系统进行分阶段改进的全过程。

## Step 1: 研究现状并确定第一阶段设计

### Prompt Context

**Verbatim prompt:** `可以，取长补短，按你这一套来做。重要的是每次改进我都需要你记录成文本，我们遇到了什么/发现了什么，分析了什么，得到一个什么样的改进计划，最终效果如何。`

**Interpretation:** 按已提出的分层记忆架构开始实施，并将每一轮发现、分析、计划、失败与最终效果形成可追溯文本。

**Inferred intent:** 不再以零散修补方式演进 Agent，而是建立接近 Claude Code/Codex 的工程化记忆基础，并保证未来可以审查为什么这样设计、改动是否真的有效。

### What I did

核对了 `/src/agent/runtime/runtime.py` 的 128K 上下文预算、`_fit_context` 裁剪逻辑、`/src/agent/memory.py` 的 `agent_tasks.state_json` 持久化，以及现有上下文、任务和记忆测试。将大目标拆为多个可独立验收阶段，并在 `/docs/superpowers/specs/2026-08-10-agent-memory-foundation-design.md` 固化第一阶段设计：层级 `AGENTS.md` 与结构化上下文压缩。

### Why

层级指令和上下文压缩是 Thread Store 与长期记忆的上游协议。如果先引入向量数据库或重写会话表，会在指令、历史和记忆边界尚不清楚时增加第二套临时状态，扩大迁移成本。

### What worked

现有架构已经提供 128K 预算、任务状态、工具事实、ChangeSet、verification 和 WorkspaceManager，第一阶段可以新增两个独立组件并局部接入运行时，不需要修改工具系统、审批协议或前端 SSE。

### What didn't work

研究阶段访问 `r.jina.ai` 两次失败，分别返回 `curl: (28) Failed to connect` 和 `curl: (7) Failed to connect`；OpenAI 官方文档直连也返回 `Forbidden`。随后使用 Anthropic/OpenAI 官方 GitHub 仓库和 Codex 开源实现核对 `AGENTS.md`、rollout lineage 与 compaction，未把无法从公开实现确认的闭源细节写成事实。

### What I learned

当前 `_fit_context` 的行为是从发送副本中逐条移除最旧消息，虽然 SQLite 原始状态仍在，但模型会失去早期决策和失败原因。Codex 的 compaction prompt 明确把摘要当作给下一模型的任务交接；这比普通聊天摘要更符合本项目长工具链任务。

### What was tricky

“记忆系统”实际包含指令、工作上下文、事件历史、长期项目知识和前端展示状态。若一次性实施，会同时改变多个事实源。本阶段必须严格限制为指令快照和压缩摘要，且暂不新增数据库表。

### What warrants review

重点审查设计中的加载优先级、32 KiB 指令预算、压缩后 75% 目标、确定性回退和第一阶段不新增数据库表的决定。

### Future work

设计确认后编写精确 TDD 实施计划；第一阶段完成并验证后，再分别设计 Thread Event Store、前后端会话统一和 SQLite FTS5 项目长期记忆。

## Step 2: 层级指令加载组件

### Prompt Context

**Verbatim prompt:** `可以，我同意这个方案`

**Interpretation:** 用户批准第一阶段设计，允许进入实施计划和 TDD 实现。

**Inferred intent:** 先完成并验证 `AGENTS.md` 指令基础，再继续结构化上下文压缩。

### What I did

新增 `/tests/test_runtime_instructions.py`，覆盖用户级、项目根、子目录顺序，同目录 `AGENTS.override.md` 覆盖，工作区外文件隔离，以及预算不足时优先保留更具体规则。新增 `/src/agent/runtime/instructions.py`，实现 `InstructionSource`、`InstructionContext` 和 `InstructionLoader`。

### Why

项目规则必须与聊天历史分离，并且必须受 workspace root 约束。独立纯组件可在不接入 LLM、数据库或 SSE 的情况下验证发现规则和预算行为。

### What worked

使用项目 `.venv` 取得真实 RED：`ModuleNotFoundError: No module named 'agent.runtime.instructions'`。最小实现完成后，命令 `.venv\Scripts\python.exe -m pytest tests/test_runtime_instructions.py -q` 返回 `4 passed in 1.41s`。

### What didn't work

首次误用裸 `python -m pytest`，Anaconda 标准库与用户目录 Python 3.13 pytest 混用，返回 `ImportError: Error importing plugin "unraisableexception": No module named '_pytest.tracemalloc'`。随后确认项目 `.venv` 没有 pytest，返回 `No module named pytest`；仅向 `.venv` 安装 `pytest 9.1.1` 后重新执行，才取得功能 RED。该问题说明后续验证命令必须始终显式使用 `.venv\Scripts\python.exe`。

### What I learned

Windows 上 `python` 命令不能代表项目解释器；即便显示可执行，也可能混合其他安装的用户 site-packages。指令预算应从最具体来源向通用来源反向选择，再恢复渲染顺序，才能保证小预算下仍保留当前目录规则。

### What was tricky

override 的语义是替代同目录普通文件，而不是在其后叠加；否则用户以为已覆盖的规则仍会同时进入模型。用户级文件不属于 workspace，但它是显式的受控例外，其他父目录文件一律不读取。

### What warrants review

审查 `/src/agent/runtime/instructions.py` 的 32 KiB 预算和 skip 策略。当前大文件会整体跳过而非截断，以避免规则在语句中间被切断。

### Future work

实现 `ContextHandoff` 确定性摘要和预算内消息组装，然后将两个组件接入 `LocalAgentRuntime` 的任务快照。

## Step 3: ContextHandoff 与运行时集成

### Prompt Context

**Verbatim prompt:** `可以，我同意这个方案`

**Interpretation:** 继续执行已批准的第一阶段记忆基础改造。

**Inferred intent:** 将结构化摘要真正接入模型上下文，而不是只保留一个独立工具。

### What I did

新增 `/src/agent/runtime/compaction.py`，实现 `ContextHandoff`、确定性事实提取、敏感值过滤、模型 JSON 回退解析和预算内消息组装。修改 `/src/agent/runtime/runtime.py`：任务创建时加载 `AGENTS.md` 快照，system prompt 注入指令，模型输入超预算时生成 handoff，并将 `context_handoff`、`compaction_count` 和 `compacted_message_count` 保存到任务状态。更新 runtime 导出和 `.gitignore`。

### Why

直接删除旧消息会让模型遗忘早期决策、失败原因和验证事实。结构化 handoff 让模型看到可验证的任务交接内容，同时不删除 SQLite 中的原始消息。

### What worked

先取得 compaction RED：`ModuleNotFoundError: No module named 'agent.runtime.compaction'`；实现后定向 compaction 测试 `3 passed`。运行时集成测试先出现两个预期失败，接入后定向测试达到 `22 passed`，后端全量达到 `87 passed`。一次检查发现新增 `__all__` 覆盖了原 runtime 导出，立即合并回原列表后全量测试仍通过。

### What didn't work

真实服务验证使用隐藏 `Start-Process` 的 PowerShell 组合被执行策略拦截，未能在本步骤重复启动 7788；该命令没有启动或修改后台服务。此前桌面启动修复已实际验证过 7788 和 WebView，本次变更只影响上下文组装。前端第一次使用 `npm test --prefix src/web` 失败，错误为 `npm error Missing script: "test"`；随后确认 12 个 TS 测试可用 `node --experimental-strip-types --test tests/*.test.ts` 执行，并把该命令补入 package.json。

### What I learned

测试命令必须是项目显式脚本，不能依赖历史口头约定；组合命令的后续 build 成功还可能掩盖前一个测试失败。runtime 导出文件是公共 API，新增导出必须合并而不能重新赋值 `__all__`。

### What was tricky

压缩发生在模型输入副本上，状态中的完整消息仍需保存。为避免改变现有工具轮、审批和续写逻辑，集成只在 `_fit_context` 超预算分支触发；未超预算的请求保持原行为。

### What warrants review

复核 `/src/agent/runtime/runtime.py` 的多次模型调用是否需要复用同一 handoff，以及未来 Thread Event Store 接入时如何把 `context_compacted` 从 task state 迁移为事件。当前没有引入模型生成摘要网络调用，只有确定性回退。

### Future work

进入下一阶段前先将本阶段实现和日记提交；后续设计 Thread Event Store，统一 Chat、Activity 和 resume 的事实来源。

## Step 4: 验收并修复压缩器注入缺口

### Prompt Context

**Verbatim prompt:** `可以，我同意这个方案`

**Interpretation:** 完成已批准方案的剩余验收，并在进入下一阶段前处理审查发现的问题。

**Inferred intent:** 第一阶段不仅要有代码和单元测试，还要能在真实 7788 服务中运行，并保留完整、可复核的改进记录。

### What I did

使用 `.venv` 启动 `/src/main.py`，请求 `http://127.0.0.1:7788/` 并通过 `netstat -ano` 核对监听边界，随后停止验收进程。代码审查发现 `/src/agent/runtime/runtime.py` 虽保存了注入的 `compaction_engine`，实际压缩却重新创建实例。先在 `/tests/test_local_agent_runtime.py` 添加回归测试取得 RED，再让 `/src/agent/runtime/compaction.py` 的 `compact()` 接受单次预算，并由运行时调用已注入实例。另在 `/tests/test_runtime_instructions.py` 添加无效 UTF-8 指令文件测试。

### Why

无效的依赖注入会让测试替身、自定义摘要策略和后续模型摘要实现全部失效，而且表面上不会报错。单次预算属于本轮模型调用条件，应作为调用参数传入，而不是丢弃注入实例后临时构造新对象。

### What worked

真实服务返回 `HTTP 200`，`netstat` 只显示 `TCP 127.0.0.1:7788 ... LISTENING`，没有 `0.0.0.0:7788`。验收后进程已停止，端口已释放。回归测试修复后命令 `.venv\Scripts\python.exe -m pytest tests\test_runtime_instructions.py tests\test_runtime_compaction.py tests\test_local_agent_runtime.py -q` 返回 `24 passed in 1.26s`。最终全量后端命令 `.venv\Scripts\python.exe -m pytest -q` 返回 `89 passed, 1 warning in 12.12s`；前端 `npm test --prefix src\web` 返回 `12 passed`；`npm run build --prefix src\web` 成功并转换 `1612 modules`。

### What didn't work

新增回归测试首次执行命令 `.venv\Scripts\python.exe -m pytest tests\test_runtime_instructions.py tests\test_local_agent_runtime.py -q` 返回 `1 failed, 20 passed`。准确失败为 `assert engine.called is True`，实际值是 `False`，证明运行时绕过了注入实例。首次服务状态采集使用 `Get-NetTCPConnection` 未返回监听项，而 HTTP 已返回 200；改用 `netstat -ano` 后确认实际监听 PID 和地址。Windows 虚拟环境启动器退出后由基础 Python 进程承载服务，因此最终按监听 PID 停止。

### What I learned

构造函数出现依赖参数不代表依赖真的参与运行；必须用行为测试验证调用链。Windows 上进程包装器 PID 不一定等于最终监听 PID，端口验收应以 `netstat` 的监听记录为准，而不是只观察最初的 `Start-Process` 返回值。

### What was tricky

压缩预算由 `max_context_tokens - max_output_tokens` 动态计算，而注入的 `CompactionEngine` 有自己的默认预算。修复需要同时保留可注入性和每次调用的动态预算，不能简单改用实例默认值，否则 128K 配置与自定义上下文窗口会失配。

### What warrants review

复核 `/src/agent/runtime/compaction.py` 新增的 `max_tokens` 调用参数，以及 `/src/agent/runtime/runtime.py` 是否始终使用 `self.compaction_engine`。超大单条最新用户消息仍可能独自超过预算；本阶段选择保留完整最新请求，没有静默截断。全量测试中的唯一 warning 是 Starlette `TestClient` 对当前 `httpx` 用法的既有弃用提醒，不是本次改动引入的失败。

### Future work

完成全量后端、前端测试和构建后提交本阶段。下一阶段单独设计 Thread Event Store，不把数据库迁移混入本次基础改动。
