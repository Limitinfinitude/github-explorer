# GitHub Explorer — 本地 Agent Harness

一个 Windows 本地优先的 GitHub 项目探索/理解/运行/修改 Agent Harness。
个人学习与面试项目：核心亮点是**全链路可观测 + harness 机制约束行为**（AgentTaskSupervisor → LocalAgentRuntime → Tool pipeline，SQLite 为唯一事实源，提示词只占很少一部分）。

## 核心能力

- **单一编排链**：任务监督 → 运行时 → 工具管线；SQLite 持久化全部状态与事件，任务可恢复、可复盘。
- **真实流式输出**：OpenAI SSE / Anthropic stream 逐 token 推送（思考 + 文本 + 工具调用）。
- **思考强度可配置**：off / high / max，思考过程实时可视化。
- **五档权限模式**：confirm（人工审批）/ auto（自动批准）/ open（完全开放）/ guardian（AI 审查，fail-closed + 熔断）/ full（完全访问，绕过边界拦截，选择时弹确认）。
- **边界硬拦截**：判分脚本目录、评测结果文件和全局工具链写入（setx / npm -g / go install）在非 full 档一律拒绝——对齐 SWE-bench「测试对 agent 物理隐藏」的闭卷语义。
- **结果优先仲裁 v2**：基于结果证据（验证通过/验收通过）判终态，过程失败痕迹只作诊断（Terminal-Bench 哲学）。
- **上下文压缩**：首次压缩由 LLM 生成 **Claude Code 9 节摘要**（primary request / key concepts / files / errors / problem solving / user messages / pending / completed / context，安全约束逐字保留），失败自动回退确定性 ContextHandoff；usage 校准 token 估算 + overflow 强制压缩重试。
- **子智能体**：`spawn_subagent` 委托聚焦任务，权限范围固定不可扩大（deepseek-harness 委托声明），默认只读工具白名单 + 预算强制收敛，结论以摘要回收。
- **生命周期钩子**：session_start / pre_tool / post_tool / session_end 四事件，命令经 stdin 收 JSON 载荷；pre_tool 钩子退出码 2 阻断该工具调用（Claude Code 语义）。
- **MCP 工具接入**：`.mcp.json` 中的外部服务器工具自动注册进 agent 工具集（默认 EXTERNAL 风险，confirm 档需确认），任务启动时预热连接。
- **技能系统**：agentskills.io 规范（SKILL.md + frontmatter），描述索引常驻提示词、正文 `use_skill` 按需加载；内置仓库速览 / 检索 / 实时搜索技能。
- **防失控预算**：阶段预算、诊断预算、重复失败硬停、循环"退一步"提醒、turn-budget 收敛标记。

## 评测成绩单

### 真实应用项目集（三轮众数）

| 轮次 | 项目集 | 完成率 | 说明 |
|---|---|---|---|
| r7 | 库/框架 | 5/10 | 环境型任务为主 |
| r8 | 真实应用 | 0/10 | 修复型任务难度分层暴露 |
| r9 | 真实应用 | **5/10** | 三类修复 + 提示词重构生效 |

### 真实 bug 修复评测（Terminal-Bench 风格外部判分）

5 个真实仓库的 bug 修复任务，判分脚本独立于 agent 运行并输出 JSON（status/checks/reason）：
jid、WatchYourLAN、gobackup、geekmarks、pdfcpu 全部通过。判分脚本双向验证（bug→fail + 修复→pass），
源码断言用位置语义（不依赖具体修复形态）。环境就绪预检：模块缓存预热 + 干净基线构建验证。

方法论：失败点逆向溯源 → 修复 → 沉淀 → 多轮众数（Terminal-Bench 六规则：可验证/定义清晰/可解/困难/真实/基于结果验证）。

## 界面与交互

- **三视图**：项目工作台（项目矩阵，SQL 批量画像 <1s）、探索（工作流入口：已导入徽章 + 带回工作区）、运行记录（按项目 × 任务 × 结论组织，时间线按语义阶段聚合）。
- **设计标准**：`DESIGN.md`——结论优先、一页一中心、噪音过滤、显式入口、性能即体验、状态一致。
- 显式主题切换、贴底跟随滚动、内部事件（thinking/token）默认不进用户视图。

## 学习库

D:\agent-learning\ —— 逐个深度研究主流 agent 架构（Aider：源码完整历史 + 官方文档存档 + 1924 行教学式研究文档；deepseek-harness：提示词架构深度研究——「成熟的体系是 harness 在限制，prompt 只占很少」）。

横向对比 11 个 harness 的轮数限制/消息流/提示词设计：docs/对比研究-2026-08-21.md

## 快速开始

```bash
.venv/Scripts/python.exe run_full.py   # 服务 http://127.0.0.1:7788
.venv/Scripts/python.exe -m pytest tests/  # 332 个测试
```
