# GitHub Explorer — 本地 Agent Harness

一个 Windows 本地优先的 GitHub 项目探索/理解/运行/修改 Agent Harness。
个人学习与面试项目：核心亮点是**全链路可观测**（AgentTaskSupervisor → LocalAgentRuntime → Tool pipeline，SQLite 为唯一事实源）。

## 核心能力

- **单一编排链**：任务监督 → 运行时 → 工具管线；SQLite 持久化全部状态与事件，任务可恢复、可复盘。
- **真实流式输出**：OpenAI SSE / Anthropic stream 逐 token 推送（思考 + 文本 + 工具调用）。
- **思考强度可配置**：off / high / max，思考过程实时可视化。
- **三级权限模式**：confirm（人工审批）/ auto（自动批准）/ open（完全开放）。
- **结果优先仲裁 v2**：基于结果证据（验证通过/验收通过）判终态，过程失败痕迹只作诊断（Terminal-Bench 哲学）。
- **上下文压缩**：确定性 ContextHandoff + 保留最近消息 + usage 校准 token 估算 + overflow 强制压缩重试。
- **防失控预算**：阶段预算、诊断预算、重复失败硬停、循环"退一步"提醒、turn-budget 收敛标记。

## 评测成绩单（真实应用项目集，三轮众数）

| 轮次 | 项目集 | 完成率 | 说明 |
|---|---|---|---|
| r7 | 库/框架 | 5/10 | 环境型任务为主 |
| r8 | 真实应用 | 0/10 | 修复型任务难度分层暴露 |
| r9 | 真实应用 | **5/10** | 三类修复 + 提示词重构生效 |

方法论：失败点逆向溯源 → 修复 → 沉淀 → 多轮众数（Terminal-Bench 六规则：可验证/定义清晰/可解/困难/真实/基于结果验证）。

## 学习库

D:\agent-learning\ —— 逐个深度研究主流 agent 架构（Aider 已完成：源码完整历史 + 官方文档存档 + 1924 行教学式研究文档；OpenHands/pi/Goose/SWE-agent/Cline/Claude Code 待研究）。

横向对比 11 个 harness 的轮数限制/消息流/提示词设计：docs/对比研究-2026-08-21.md

## 快速开始

```bash
.venv/Scripts/python.exe run_full.py   # 服务 http://127.0.0.1:7788
.venv/Scripts/python.exe -m pytest tests/  # 308 个测试
```
