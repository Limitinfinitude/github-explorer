# GitHub Explorer

GitHub Explorer 是一个仅供本机访问的通用本地操作 Agent。它结合 GitHub 项目探索和本地开发工作流，可以在选定工作区中读取、搜索、编辑文件，安装依赖，运行测试，启动服务并检查运行状态。

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
- SSE 执行轨迹、SQLite 任务记录和 LangSmith Trace

## 启动

Windows 推荐使用项目根目录的 `.venv`：

```powershell
cd E:\github探索者\github-explorer
\.venv\Scripts\python.exe -m pip install -r requirements.txt
\.venv\Scripts\python.exe src\main.py
```

然后打开 [http://127.0.0.1:7788/](http://127.0.0.1:7788/)。也可以使用 `启动.bat` 或 `GitHub Explorer.bat`。

## 配置模型

复制 `.env.example` 为 `.env`，再填写本机配置。`.env` 已被 `.gitignore` 排除，真实 API Key 不应提交到 Git 或 README；共享代码时只提交 `.env.example`。

模型也可以在 Web 界面的 Settings 中配置 Base URL、协议和模型 ID，并使用 URL 测速、模型发现和最小推理连接测试。

## 开发与验证

```powershell
\.venv\Scripts\python.exe -m pytest
cd src\web
npm test
npm run build
```

当前基线：Python 78 个测试通过，前端 Node 12 个测试通过，Vite 构建成功。

## 目录说明

```text
src/                 最新 FastAPI、Agent Runtime 和 React 源码
tests/               Python 测试
docs/                实施日记
项目推进记录/        设计、实施计划和项目报告
desktop/             Windows 桌面启动封装
mcp-servers/         MCP 配置与服务
config/              非敏感配置模板
```

运行数据库、克隆仓库、`.venv`、缓存和日志不属于源码仓库内容，统一放在本地或 `_explorer-cleanup/` 归档目录。

## 安全边界

本项目默认只允许本机访问。删除、覆盖、管理员操作、系统级安装和外部发布需要确认。工作区 root 是文件和命令操作的边界。发布前必须轮换历史密钥，并确认源码、日志、构建产物和提交历史中不存在真实凭据。

## 项目报告

完整架构和实现报告位于：

`项目推进记录/2026-08-10-GitHub-Explorer最新版完整项目报告.md`
