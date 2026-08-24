# GitHub Explorer

GitHub Explorer 是一个 **Windows 本地优先的项目探索与开发 Harness**：从发现 GitHub 仓库开始，在隔离的本地工作区中完成项目理解、环境搭建、依赖安装、代码修改、测试和服务验收，并把整个过程保存为可回放的事实记录。

它面向三类用户：

- 想把 GitHub 项目跑起来、学习和二次修改的学生与初学者；
- 需要快速研究陌生仓库的开发者；
- 需要观察 Agent 工具调用、失败恢复和代码产出的 Harness 研究者。

GitHub Explorer 不是云端 IDE，也不是只生成代码的聊天框。它的核心价值是把“找到项目”变成“在本地真实运行、修改、验证并能复盘的项目旅程”。

![GitHub Explorer 当前架构](docs/diagrams/2026-08-15-github-explorer-current-architecture.svg)

## 当前状态

- 主服务：`http://127.0.0.1:7788/`
- 监听范围：仅 `127.0.0.1`，不会监听 `0.0.0.0`
- 最新源码：`src/`
- Python 环境：项目根目录 `.venv/`
- 当前 Harness 评估：`6.8 / 10`
- 当前生产编排：`AgentTaskSupervisor + LocalAgentRuntime`
- LangGraph / LangSmith：不再属于生产主链路；本地 SQLite 和事件回放承担观测职责

当前仍处于快速迭代阶段，尚未宣称达到企业级安全、跨平台或部署标准。

## 主要能力

### 发现与理解项目

- GitHub 搜索、趋势探索和仓库信息查看
- 目录浏览、文件读取、文本搜索和 Repo Map
- 项目类型识别、依赖清单分析和运行入口探测
- 会话级工作区、全局默认工作区和当前路径记忆

### 本地执行与修改

- 结构化文件创建、编辑、diff、ChangeSet 和最近一次撤销
- `.venv` 准备、依赖安装、测试、构建和服务启动
- 前台命令、后台进程、端口检查和 HTTP 就绪检查
- 五档权限模式：confirm / auto / open / guardian（AI 审查，fail-closed）/ full（完全访问，需确认弹窗）
- 边界硬拦截：判分脚本目录、评测结果文件和全局工具链写入（setx / npm -g / go install）在非 full 档一律拒绝
- 工作区边界、loopback HTTP 边界、外网只读 `web_fetch`（禁内网）和工具参数 Schema 校验

### 扩展机制

- **生命周期钩子**：session_start / pre_tool / post_tool / session_end，命令经 stdin 收 JSON 载荷；pre_tool 退出码 2 阻断工具调用
- **MCP 工具**：`.mcp.json` 服务器工具自动注册进 agent 工具集，任务启动时预热连接
- **子智能体**：`spawn_subagent` 委托聚焦任务，权限范围固定不可扩大，预算强制收敛
- **技能系统**：agentskills.io 规范，描述索引常驻提示词、正文按需加载（内置仓库速览/检索/实时搜索）

### Agent 可靠性与恢复

- 单一生产编排链，避免多套状态机互相串线
- 工具调用账本、唯一 `call_id`、失败恢复和恢复耗尽终态
- 四阶段预算：项目体检、实现、测试、运行验收分别计数
- 重启后将遗留任务、开放工具调用和后台进程标记为 `interrupted/orphaned`
- 任务取消、一次性重新规划、需求账本
- 上下文压缩：首次由 LLM 生成 Claude Code 9 节摘要（安全约束逐字保留），失败回退确定性 ContextHandoff

### 观测与开发者证据

- SQLite 本地事实源：任务、事件、模型、工具、审批、变更、验证、进程和记忆
- SSE 实时轨迹与 `sequence` 回放，页面切换或重连后可以继续读取任务
- 项目工作台按项目聚合阶段、任务、变更、验证和开发者证据
- 终态仲裁：没有有效证据时不会把任务误报为完成
- 本地质量指标：误报完成、证据覆盖、工具恢复率、模型轮数、延迟和 token

## 快速开始

### 1. 准备 Python 环境

PowerShell：

```powershell
cd E:\github探索者\github-explorer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

项目已约定使用根目录 `.venv`，不要把依赖安装到系统 Python。

### 2. 配置模型

```powershell
Copy-Item .env.example .env
```

在 `.env` 或 Web 界面的“设置”中配置模型协议、Base URL、模型 ID 和 API Key。支持 Anthropic 原生协议与 OpenAI Compatible 协议，也支持获取模型、URL 测速和最小连接测试。

真实密钥只保存在本机配置中，不要写入 README、源码、日志或 Git 历史。`.env`、`data/`、数据库、克隆仓库和本地日志均已加入忽略规则。

### 3. 启动 7788 服务

推荐入口：

```powershell
\.venv\Scripts\python.exe run_full.py
```

然后打开 [http://127.0.0.1:7788/](http://127.0.0.1:7788/)。也可以使用根目录的 `启动桌面.bat` 或 `GitHub Explorer.bat` 启动桌面封装。

服务默认只绑定 loopback 地址。需要变更端口时可以设置 `PORT`，但不要把服务暴露到 `0.0.0.0`：

```powershell
$env:PORT = "7788"
\.venv\Scripts\python.exe run_full.py
```

## 系统架构

```text
React + TypeScript UI
        |
FastAPI + SSE API
        |
AgentTaskSupervisor
        |
LocalAgentRuntime
   |       |        |
Context  Tool     Acceptance
Engine   Pipeline  Evaluator
        |
SQLite Fact Store
        |
Activity / Project Workbench / Evaluation Report
```

请求链路是：

```text
用户消息
  -> 会话工作区解析
  -> 任务与需求账本
  -> 模型决策
  -> 工具 Schema / 权限 / 工作区校验
  -> 工具执行与副作用记录
  -> 文件、测试、进程、HTTP 证据
  -> 终态仲裁
  -> SSE 实时回复与 SQLite 回放
```

Runtime 是唯一生产编排入口。模型负责提出行动并解释结果，Runtime 负责边界、权限、工具执行、恢复、证据和最终状态；SQLite 是本地事实源，SSE 只是事实的实时投影。

详细架构、生命周期和评分对比见：

- [项目技术报告](docs/项目技术报告.md)
- [Harness 横向评估报告](docs/reports/2026-08-15-Harness横向评估报告.md)
- [Agent 生命周期图](docs/diagrams/2026-08-15-agent-lifecycle.svg)
- [Harness 分数对比图](docs/diagrams/2026-08-15-harness-score-comparison.svg)

## 工作区与数据边界

工作目录采用以下优先级：

```text
会话固定目录 > 全局默认目录 > 项目 fallback 目录
```

这样不同项目的对话不会串线，且同一会话可以记住当前路径。所有文件和命令操作都必须经过工作区边界检查。

本地数据位置：

```text
data/                 本地模型配置、SQLite 数据库和记忆
cloned_repos/         Agent 克隆的仓库
.venv/                Python 虚拟环境
项目推进记录/         本地测试与推进日志
```

以上目录不属于公开源码发布内容。测试文件和推进记录保留在本地，用于开发和测评，不随仓库上传。

## 开发与验证

后端：

```powershell
\.venv\Scripts\python.exe -m pytest -q
```

前端：

```powershell
cd src\web
npm ci
npm test -- --run
npm run build
```

当前已验证的基线包括后端回归测试、前端测试、Vite 生产构建、7788 健康检查、SSE 回放和结构化测评报告。Playwright 真浏览器依赖暂不接入主链路，未来作为可选 skill/插件使用。

## 发布边界

允许进入公开仓库的内容：

- `src/`、`desktop/`、启动脚本和依赖声明
- `src/web_dist/`：随仓库发布的 Vite 构建产物，保证克隆后无需构建即可运行；构建改动随源码一起提交
- `docs/` 中的公开技术文档与 SVG 图
- `.env.example` 等不含真实凭据的配置模板
- README 和产品说明

禁止进入仓库的内容：

- `.env`、API Key、Authorization、密码和完整 Prompt
- `data/`、SQLite 数据库、`cloned_repos/`、`.venv/`
- `tests/`、`项目推进记录/`、本地日志和缓存
- 任何由个人项目运行产生的构建临时文件或运行产物

提交前请检查：

```powershell
git status --short
git diff --check
git grep -n -I -E "sk-[A-Za-z0-9]|gho_[A-Za-z0-9]|Bearer [A-Za-z0-9]" -- ':!*.md'
```

## 当前成熟度与路线图

GitHub Explorer 当前评分为 `6.8 / 10`。它已经在“GitHub 项目发现 + 本地执行 + 事实观测”这个交叉场景形成差异化，但与成熟 Harness 相比仍有差距：

- 真实 OS 级沙箱和跨平台隔离还不完整；
- 插件体系（hooks / MCP / 技能）已落地，但生命周期边界仍需进一步接口化与文档化；
- 长任务恢复、多 Agent 协作和生态集成仍较薄；
- 真实浏览器 E2E、发布闭环和更大规模测试矩阵尚未完成。

后续路线：

1. 固化工具执行协议、恢复语义和能力 Seam；
2. 用多轮真实任务校准观测指标和验收阈值；
3. 完善项目学习、运行和二次开发旅程；
4. 再评估可选浏览器 skill、沙箱和发布插件。

## 许可证

项目当前处于持续开发阶段。正式发布前请补充适合项目依赖和二次分发的许可证文件，并再次审查第三方代码、模型服务条款与本地数据处理边界。
