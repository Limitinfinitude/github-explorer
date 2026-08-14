# P3 Session Requirement Ledger Design

## 目标

为本地 Agent 增加会话级累计需求账本，保证多轮对话中未完成要求不会因后续轮次、测试全绿或模型自述而丢失。

## 范围

本批只实现后端闭环：持久化、运行时注入、证据结算和事件记录。结构化 HTTP、阶段预算、进程事实、artifact 生命周期和前端专用视图不进入本批。

## 数据模型

SQLite 新增 `session_requirements`：

- `session_id + position` 提供会话内稳定编号。
- `normalized_text` 用于同会话精确去重。
- 状态只使用 `pending` 和 `completed`；`failed/unverified` 是单轮验收结果，写入 evidence 后仍保持 `pending`。
- 保存来源 task、完成 task、最近证据和时间戳，不保存模型密钥或完整工具输出。

## 数据流

1. 本轮是明确本地实现请求时，提取编号验收项；没有编号且没有既有 backlog 时，把整条实现请求作为一项，但首轮沿用现有完成门禁以兼容简单任务。
2. “继续、接着做、继续推进”等续作请求不创建空泛新项，直接加载该会话未完成要求。
3. 显式编号请求、续作请求或已有 backlog 的会话把所有 pending 要求以稳定编号注入系统提示；首次单句任务只保存 snapshot，不强制模型改变回复格式。
4. WorkProductEvaluator 继续用 changeset、verification 和 process 证据判断每项结果。
5. 只有 `passed` 更新为 `completed`；`failed/unverified` 保持 pending，并记录本轮证据与原因。
6. `done=completed` 要求本轮注入的所有 backlog 项均通过，否则为 `incomplete`。

## 边界

- 问候和普通知识回答不创建、不强制处理 backlog。
- 已完成项不重复注入；同文本重复提交不创建重复记录。
- 当前没有 task store 时保持现有单轮 acceptance 行为，便于测试和兼容旧调用者。
- 本批不使用模型自动拆需求，避免引入第二次 LLM 调用和不可重复分类。

## 验收

- SQLite 可跨 Runtime 实例恢复 backlog，编号稳定且去重。
- 第一轮只完成部分要求时，未完成项仍为 pending。
- 第二轮“继续”会在 system prompt 收到旧 pending 项，并可凭新证据关闭。
- 测试成功但需求没有有效证据时，需求不会关闭，任务不能完成。
- 既有后端和前端回归保持通过。
