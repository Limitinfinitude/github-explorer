# GitHub Explorer Agent Harness PRD v2.0

> 日期：2026-08-14（Asia/Shanghai）
> 状态：研究后待评审
> 当前服务：`http://127.0.0.1:7788/`
> 默认模型：`custom-deepseek-v4-flash`

## 1. 产品定位

GitHub Explorer 是 Windows 本地优先的通用操作 Agent：将自然语言任务转换为可审计的文件、命令、依赖、测试、HTTP 服务和进程操作，并在会话工作区中持续推进。成功标准不是“模型说完成”，而是用户能复核 Agent 做过什么、没做什么、为何停止、在哪个目录和进程上做的。

## 2. 目标

1. 不把旧服务、模型自述或单次 HTTP 200 当成当前任务成功。
2. 多轮任务保留 pending、completed、blocked、unverified，并可恢复。
3. schema 错误、审批、取消、重启、进程退出不留下悬空状态。
4. PowerShell、CMD、`.venv`、中文路径、端口和进程状态有明确契约。
5. Chat、Activity、验收证据分层展示，不泄露供应商协议。
6. 事件日志成为 Chat、Activity、恢复、压缩和外部观测镜像的唯一事实源。

## 3. 非目标

不实现 ISSUE 自动修复、自动发布、多 Agent 并行、远程执行、云沙箱、完整 Cordis/插件市场、公网多用户权限系统。安全只保持当前本地审批和边界约束所需范围。

## 4. 核心概念

### Workspace

全局默认目录供新会话使用；session workspace 优先；current path 只能在 workspace 边界内变化。所有工具、证据和进程带 `workspace_root` 与 `cwd`。

### Session / Turn / Step / Tool call

Session 是持久事实容器；Turn 是一次用户输入的连续工作单元；Step 是一次模型请求及其工具集合；Tool call 带稳定 `call_id` 且必须唯一终态。

### 状态

`queued`、`running`、`waiting_approval`、`completed`、`incomplete`、`failed`、`cancelled`、`interrupted`。实现类任务至少需要匹配 changeset 或明确只读结论及验证证据；验收有 pending/failed/invalid 时不得 completed；重启后进程用 `orphaned/exited`，不使用 `unknown`。

## 5. 目标架构

```text
UI / ACP / HTTP
        |
Projection API（Chat、Activity、验收、可选外部观测）
        |
Session Event Store（append-only facts）
        |
Agent Orchestrator（Turn -> Step -> Tool Pipeline）
        |
LLM | Tools | Workspace | Process | Evidence | Approval | Telemetry
        |
Windows local providers（PowerShell/CMD、SQLite、127.0.0.1）
```

### Session Event Store

事件至少包括：`turn_start/end`、`user_message`、`assistant_message`、`model_request`、`tool_call`、`approval`、`tool_result`、`file_changed`、`verification`、`process_started/stopped`、`acceptance`、`context_compacted`、`error`。事件 append-only，带 session/task/sequence/time/workspace；模型请求、UI 与本地观测均从事件重建。

### Agent Orchestrator

解析需求 -> 建立阶段计划和预算 -> 组装模型上下文/schema -> 校验调用 -> 审批与 pipeline -> 记录结果和账本 -> 判断继续 -> 结构化最终化。编排器不直接实现所有能力。

### Capability seams

- `LLMProvider`：流式响应、tool call、usage、错误分类。
- `ToolSpec/ToolExecutor`：schema、风险、执行、统一结果。
- `WorkspaceBinding`：默认/session/current path 和边界。
- `ManagedProcess`：启动器、进程树、监听 PID、端口、停止、orphan。
- `EvidenceNormalizer`：file/unit/http/process/browser 证据及有效性。
- `SessionEventStore`：append、replay、snapshot、compaction boundary。

## 6. P4 可靠性阶段

### P4-A 进程所有权

`start_process` 返回稳定 process_id、launcher PID、子进程 PID、cwd、解释器、命令指纹和端口；`wait_http(process_id=...)` 校验监听 PID 属于进程树；端口冲突返回 `port_conflict`；旧页面 200 但版本/PID 不匹配时失败；退出、重启、断开进入确定状态；只允许 `127.0.0.1` 或显式 `::1`。

### P4-B 证据规范化

changed file、cwd、process cwd 和验证路径进入账本前统一为工作区相对 POSIX 路径，原始字符串只作诊断；绝对路径、反斜杠、大小写、中文路径、目录变更和越界路径均有测试。

### P4-C 工具参数恢复

schema 缺字段、类型错误、CMD/PowerShell 混用返回结构化错误码和字段级建议；同一 call lineage 最多自动修复一次；无效输入不执行副作用；二次失败明确 incomplete；原始失败和恢复可见。

### P4-D 结构化最终化

账本、变更、验证、进程事实先生成确定性骨架，模型只补解释和限制。空终答、协议残片、错误 Markdown、模型自称完成但证据不足时，UI 仍显示正确结构化状态。

## 7. P5 规模化阶段

### P5-A 事件与上下文

Chat、Activity、恢复、压缩和可选外部观测从 Event Store 投影；压缩记录来源 sequence、摘要版本、未完成需求、开放调用；重启可恢复或明确 interrupted/orphaned。

### P5-B 阶段预算与 HTTP

需求解析、实现、测试、运行验收各有预算和完成条件；读取超阈值必须行动或 incomplete；增加结构化 `http_request(method,url,headers,json)`；持久终端按 session 隔离。

### P5-C 指标

| 指标 | 定义 | 目标 |
|---|---|---:|
| False completion | 证据不足却 completed | 0 |
| False incomplete | 证据充分却非 completed | <= 2% |
| Post-success crash | 成功后最终化崩溃 | 0 |
| Approval mismatch | 审批成功但状态未归并 | 0 |
| Orphan process | 结束后无归属进程 | 0 |
| Protocol leak | DSML/供应商协议进入回复 | 0 |
| Evidence invalid rate | 可归一化证据被判 invalid | < 1% |
| Backlog coverage | 完成项有有效证据 | 100% |

首版固定 12 个评测任务：纯聊天、只读审计、创建项目、连续修改、测试修复、启动服务、端口冲突、审批恢复、取消恢复、中文路径、上下文压缩、空终答/协议噪声。

## 8. UI、协议和观测

Chat 只显示可读回复；Activity 显示阶段、工具、耗时、恢复和证据；Acceptance 显示需求状态/证据/原因；Settings 管理模型、URL、Key、默认工作区和观测配置，密钥只掩码。ACP/HTTP DTO 不含内部 Context、私有字段、原始 Key 或未提交 delta。

本地 Trace 由 task/session/event sequence 关联，属于 SQLite 事实层。模型、工具、审批、文件、验证、进程和最终化全部写入同一事件总线，并由本地投影生成 Chat、Activity 和验收视图。生产链路不依赖 LangSmith 或其他远程观测 SDK。

## 9. 测试策略

单元覆盖 schema、错误、终态、路径、进程归属、端口冲突、事件 replay、压缩和最终化；集成覆盖 SSE、approval、cancel、restart、SQLite、本地事件投影、前端同步和移动端；真实验收固定使用 `custom-deepseek-v4-flash`，余额/网络阻塞明确记录，不切换 Mimo 伪造通过。报告必须给文件、cwd、解释器、退出码、URL、PID、证据和限制。

## 10. 里程碑

### M0：PRD 评审

研究记录、PRD、P4/P5 backlog、指标基线完成；用户确认范围和非目标。

### M1：P4

P4-A/B/C/D 测试通过；主项目全量回归不下降；至少 3 个真实故障任务无 false completion。

### M2：P5

事件可重放、压缩可恢复、阶段预算生效、结构化 HTTP 通过 Windows 回归；12 项评测达到指标目标。

### M3：稳定版候选

连续两轮无 P0；指标达标；用户可从 UI 追溯一次任务从输入到最终证据的完整链路。

## 11. 当前基线与决策

P3 新鲜基线为后端 189 passed、前端 24 passed、作品 31 passed、生产构建成功，服务仅监听 `127.0.0.1:7788`，默认模型 `custom-deepseek-v4-flash`。当前综合质量约 7.7/10，主要扣分来自进程所有权、证据规范化、工具自修复和结构化最终化。

本 PRD 吸收 DeepSeek Harness 的事件、能力、不变量和协议分层；吸收 Claude Code 的本地工作流、Aider 的 repo map/诊断、OpenHands 的事件/终端隔离、OpenHarness 的恢复/权限优先级、opencode 的上下文/Windows 经验；不照搬任何单一框架。P4 -> P5 逐步收敛，未经评审不改核心代码。

## 12. 已确认的产品定位与证据层

GitHub Explorer 的产品主线是：`发现项目 -> 项目体检 -> 跑起来 -> 看懂 -> 隔离二改 -> 测试验证 -> 复盘记录`。它保留通用本地操作 Agent 作为执行内核，但产品承诺是帮助用户把陌生 GitHub 仓库变成可运行、可解释、可实验和可复盘的本地对象。

前端采用双层信息：普通用户默认看到阶段、成功/失败、下一步、访问地址、变更摘要和验证结论；开发者可在同一项目页展开完整工具流水账、参数、cwd、workspace root、命令退出码、失败恢复、文件变更、测试、HTTP、受管进程、本地事件序列和本地 Trace。真实进程树、端口归属和外部镜像仅在采集到事实后显示。运行记录不删除，只默认折叠、可筛选、可检索和可导出。

## 13. 编排与观测裁决

`LocalAgentRuntime` 是唯一生产编排链，SQLite Session Event Store 是唯一事实源。LangGraph、LangSmith、旧 Swarm、状态图、节点和子图已退出生产源码与默认依赖；旧 JSON/SSE 入口已迁移到 Local Runtime，无法保持任务身份和审批语义的旧接口明确返回 `410`，不保留隐式兼容链。

后续评测只衡量 Local Runtime 自身的可靠性，不再维护双链生产实现。固定 12 项任务继续覆盖纯聊天、只读审计、创建项目、连续修改、测试修复、启动服务、端口冲突、审批恢复、取消恢复、中文路径、上下文压缩、空终答/协议噪声。硬失败仍为：工作区越界写入、审批绕过、协议泄露、证据不足却 `completed`，以及本地观测失败改变任务结果。
