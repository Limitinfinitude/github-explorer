# Runtime 独立性与双链基准记录

> 时间：2026-08-14（Asia/Shanghai）

## 遇到了什么

项目既有 `LocalAgentRuntime` 和 LangGraph 两条编排路径。虽然主 SSE 入口已使用 Runtime，但导入 `agent.runtime` 会先执行 `agent/__init__.py`，继而加载 LangGraph；`agent/llm.py` 与运行时追踪也在模块导入时直接依赖 LangSmith。这样一旦外部观测 SDK 缺失，主服务可能在启动前失败，与“本地优先、SQLite 事实源”的架构目标矛盾。

## 发现了什么

1. LangGraph 与 LangSmith 不是同一件事：前者是旧编排兼容层，后者是外部观测镜像。
2. 不能只靠 `try/except` 包住一次远端调用。顶层导入、创建 Trace、结束 Trace 和 `run.set()` 都可能把外部失败带进主链。
3. 旧图、Swarm、旧工具和子图仍保留许多 `@traceable`，但它们不属于 `LocalAgentRuntime` 的生产依赖闭包，应整体作为 legacy 处理。
4. 有两条链并存时，必须用同一任务样本和硬性不变量作比较，不能凭主观感觉或一次 Demo 判定去留。

## 改进计划

1. 让 `agent` 的 LangGraph 导出按需加载，生产 Runtime 导入不再触发旧图。
2. 将 LangSmith 改为延迟加载的可选适配器；SDK/网络/认证/写入失败时 no-op，不中断本地任务。
3. 将默认依赖缩减为核心 Harness；legacy 环境显式安装 `requirements-legacy.txt`。
4. 用 12 项固定任务、两轮基准比较 Legacy LangGraph 与 Runtime，并依照硬失败和量化指标选择唯一生产链。

## 最终效果

- 新增回归测试：屏蔽 `langsmith` 后可导入 `agent.runtime` 和 FastAPI `main`；LangSmith SDK 不可用时状态明确为 `sdk_unavailable`；远端 Trace 写入失败不向任务抛出异常。
- `LocalAgentRuntime + SQLite Event Store` 成为唯一生产事实源；LangSmith 是尽力外部镜像。
- 默认 `requirements.txt` 不再安装 LangGraph 或 LangSmith，`requirements-legacy.txt` 用于旧路由和研究环境。
- LangGraph 仍保留为有调用计数的兼容链路，等待对照基准与入口迁移完成后退出。
