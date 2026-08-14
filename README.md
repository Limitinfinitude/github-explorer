# GitHub Explorer

GitHub Explorer 是一个 Windows 本地优先的开源项目探索工作台。它帮助用户从 GitHub 项目发现开始，完成可运行性体检、环境搭建、代码理解和隔离二改，并把工具调用、文件变更、验证结果和进程状态保存成可复读的本地记录。

它不是云端 IDE，也不是另一个只会生成代码的聊天框。当前产品重点是把陌生仓库变成一个可运行、可解释、可实验和可复盘的本地对象。

## 当前版本

- 服务地址：`http://127.0.0.1:7788/`
- 监听地址：仅 `127.0.0.1`，不会监听 `0.0.0.0`
- 主源码：`src/`
- Python 环境：`.venv/`（本地开发时创建，不提交）
- 前端：React + TypeScript + Vite，构建产物位于 `src/web_dist/`

## 核心能力

- GitHub 搜索、趋势探索和仓库信息查看
- 会话级工作区与全局默认工作区
- 目录浏览、文件读取、文本搜索和 Repo Map
- 结构化文件编辑、diff、ChangeSet 和最近一次撤销
- 项目识别、`.venv` 准备、依赖安装、测试与构建验证
- 前台命令和后台进程管理
- 端口检查与 HTTP 就绪检查
- 分级自动执行：普通操作自动执行，删除、管理员操作和外部发布前确认
- Anthropic 原生协议与 OpenAI Compatible 协议
- SSE 执行轨迹、SQLite 任务记录和本地事件回放
- 工具调用账本、失败恢复、验收证据与上下文压缩
- 后端任务取消、诊断预算与一次性重新规划
- 后台进程状态对账：运行中、已停止、已退出和已失联
- 项目工作台：按项目聚合阶段、任务、变更、验证和开发者证据
- SQLite 本地观测总线：模型、工具、审批、文件、验证、进程和终态
- 确定性终态仲裁：没有有效证据或明确标记未完成时不会误报完成

## 启动

Windows 推荐使用项目根目录的 `.venv`：

```powershell
cd E:\github探索者\github-explorer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src\main.py
```

然后打开 [http://127.0.0.1:7788/](http://127.0.0.1:7788/)。也可以使用 `启动.bat` 或 `GitHub Explorer.bat`。

## 配置模型

复制 `.env.example` 为 `.env`，再填写本机配置。`.env` 已被 `.gitignore` 排除，真实 API Key 不应提交到 Git 或 README；共享代码时只提交 `.env.example`。

模型也可以在 Web 界面的“设置”中配置 Base URL、协议和模型 ID，并使用 URL 测速、模型发现和最小推理连接测试。模型配置保存在本机 `data/` 目录，该目录不会提交到 Git。

## 架构

```text
React / TypeScript UI
        |
 FastAPI + SSE API
        |
 LocalAgentRuntime
   |      |       |
 Context  Tool    Acceptance
 Engine   Pipeline Evaluator
        |
 SQLite Event Store
```

生产环境只有一条 Agent 编排链：`LocalAgentRuntime`。模型负责提出行动和解释结果，Runtime 负责工作区边界、权限、工具执行、失败恢复、证据仲裁和终态。SQLite 是任务、模型调用、工具、审批、变更、验证、进程与记忆的唯一事实源。

运行记录页使用同一事实源展示本地全链路，不依赖远程观测服务。API Key、Authorization、密码和完整 Prompt 不写入 Agent 事件。

## 开发与验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q

cd src\web
npm test -- --run
npm run build
```

当前验收基线：内部 Python 回归测试 `200 passed`，前端 Node 测试 `26 passed`，TypeScript 与 Vite 生产构建成功。服务只监听 `127.0.0.1:7788`。

## 目录说明

```text
src/                 最新 FastAPI、Agent Runtime 和 React 源码
docs/                使用说明和公开技术文档
desktop/             Windows 桌面启动封装
mcp-servers/         MCP 配置与服务
config/              非敏感配置模板
data/                本地模型配置与 SQLite 数据，不提交
```

运行数据库、克隆仓库、`.venv`、缓存和日志不属于源码仓库内容，统一放在本地或 `_explorer-cleanup/` 归档目录。

## 安全边界

本项目默认只允许本机访问。删除、覆盖、管理员操作、系统级安装和外部发布需要确认。工作区 root 是文件和命令操作的边界。

项目仍处于持续开发阶段，尚未宣称达到企业级安全、审计和部署标准。提交前仍应确认源码、日志、构建产物和提交历史中不存在真实凭据。

## 当前路线

- P4：进程与端口所有权、证据路径规范化、工具参数恢复、结构化最终化
- P5：事件重放与上下文恢复、阶段预算、结构化 HTTP、质量指标
- 前端：项目旅程作为用户主线，开发者证据作为同页可展开的第二层
