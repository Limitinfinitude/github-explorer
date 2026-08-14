# 项目探索工作台与 LangGraph 迁移实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 在保留完整开发者流水账的前提下，把前端重组为“项目旅程 + 可展开证据层”，并逐步让新 Runtime 接管 LangGraph 旧入口。

**Architecture:** `Session Event Store` 继续作为唯一事实源；后端新增项目/阶段/证据投影，前端使用同一投影渲染用户摘要和开发者详情。`LocalAgentRuntime` 是新主链，LangGraph 仅保留 legacy 适配，不再新增业务能力。

**Tech Stack:** FastAPI、SQLite、Python `.venv`、React、TypeScript、Vite、现有 `lucide-react`、pytest、Vitest。

---

## 阶段 0：建立基线与 legacy 观测

**Files:**
- Modify: `src/routes_agent.py`
- Modify: `src/agent/graph.py`
- Modify: `src/agent/swarm_graph.py`
- Test: `tests/test_agent_routes.py`

- [ ] 为每个 LangGraph 入口增加统一 `legacy` 标记、调用计数和结构化日志字段：`route`, `session_id`, `legacy: true`, `replacement`。
- [ ] 验证 `/api/chat`、`/api/analyze`、`/api/learning-path`、`/api/usage-example`、`/api/explain`、`/api/agent/setup`、`/api/agent/confirm`、`/api/agent/swarm` 的现有响应不变。
- [ ] 运行：`.venv\Scripts\python.exe -m pytest tests/test_agent_routes.py -q`。
- [ ] 只有基线通过后，才开始改投影和前端。

## 阶段 1：统一项目页后端投影

**Files:**
- Modify: `src/routes_agent.py`
- Modify: `src/agent/memory.py`
- Modify: `src/agent/runtime/schema.py`
- Create: `src/agent/runtime/project_projection.py`
- Test: `tests/test_project_projection.py`

- [ ] 从现有 `get_agent_task_activity()`、`list_agent_traces()`、workspace state 和 project memories 组合 `ProjectOverview`，字段固定为：`project_id`, `workspace_root`, `stage`, `stage_status`, `next_action`, `summary`, `evidence_counts`, `active_processes`, `latest_verification`, `trace_id`。
- [ ] 将用户摘要与开发者证据引用分离：摘要只能读取确定性状态和短文本；证据返回事件、工具调用、参数、cwd、退出码、文件、验证、进程和 trace 元数据。
- [ ] 增加只读接口：`GET /api/projects/{project_id}/overview` 和 `GET /api/projects/{project_id}/evidence`，不触发 Agent 工具调用。
- [ ] 对不存在项目、无任务、失败任务、等待确认和未验证任务返回确定状态，不返回 500 或伪造 completed。
- [ ] 运行后端单测和既有 runtime 测试。

## 阶段 2：前端项目旅程与证据层

**Files:**
- Create: `src/web/src/components/project/ProjectWorkspaceView.tsx`
- Create: `src/web/src/components/project/ProjectStageRail.tsx`
- Create: `src/web/src/components/project/ProjectEvidenceDrawer.tsx`
- Create: `src/web/src/components/project/ExperimentCard.tsx`
- Modify: `src/web/src/App.tsx`
- Modify: `src/web/src/lib/api.ts`
- Modify: `src/web/src/types.ts`
- Modify: `src/web/src/index.css`
- Test: `src/web/src/components/project/ProjectWorkspaceView.test.tsx`

- [ ] 将 Explore 入口改为项目工作台入口，保留现有搜索、趋势、克隆和 Repo Map 功能。
- [ ] 项目头部显示来源、工作区、技术栈、当前阶段和运行身份；缺数据时显示明确空状态。
- [ ] 阶段轨道固定为：体检、跑起来、看懂、实验室、记录；阶段点击只切换投影，不触发隐式工具调用。
- [ ] 默认展示摘要；“查看开发者证据”展开事件、工具、命令、cwd、进程、端口、变更、测试、恢复和 LangSmith 状态。
- [ ] 保留独立 Activity 页面，改为跨项目审计视图，并复用证据行组件，避免删除现有流水账。
- [ ] 运行前端测试和生产构建；检查 390px、768px、1440px 三种视口无横向溢出。

## 阶段 3：Runtime 接管只读分析入口

**Files:**
- Create: `src/agent/runtime/read_only_tasks.py`
- Modify: `src/main.py`
- Modify: `src/routes_search.py`
- Test: `tests/test_read_only_tasks.py`

- [ ] 将项目分析、学习路径、使用示例和仓库解释转换为 Runtime 只读任务，统一写入 Session Event Store。
- [ ] 保留原端点路径作为兼容入口，但响应增加 `runtime: "local"`, `legacy: false`, `task_id` 和统一状态字段。
- [ ] 保证只读任务不会修改文件、启动进程或改变工作区 current path。
- [ ] 为错误、空模型输出和上下文超限返回 `incomplete` 与原因，不返回模型自称完成的字符串。
- [ ] 运行相关路由回归测试。

## 阶段 4：Runtime 接管 setup/confirm

**Files:**
- Modify: `src/routes_agent.py`
- Modify: `src/agent/runtime/runtime.py`
- Modify: `src/agent/runtime/schema.py`
- Test: `tests/test_agent_routes.py`
- Test: `tests/test_local_agent_runtime.py`

- [ ] 将 `/api/agent/setup` 映射为 Runtime 任务创建和计划阶段。
- [ ] 将 `/api/agent/confirm` 映射为统一 approval/resume，保持原调用方可用。
- [ ] 写入 `approval_requested`, `approval_resolved`, `task_completed` 或 `task_cancelled` 事件。
- [ ] 验证审批成功、拒绝、取消、重启恢复和重复确认的状态一致性。

## 阶段 5：LangGraph 退出判定

**Files:**
- Modify: `requirements.txt`
- Modify: `src/agent/__init__.py`
- Modify: `README.md`
- Create: `docs/diary/YYYY-MM-DD-langgraph-migration-acceptance.md`
- Test: `tests/test_legacy_routes.py`

- [ ] 从日志/计数确认旧入口调用量为零，或明确仍有用户依赖并保留兼容层。
- [ ] 若调用量为零，移除默认 `langgraph` 和 `langgraph-checkpoint-sqlite` 依赖，删除旧图仅在完整回归通过后执行。
- [ ] 若 Swarm 仍有研究用途，把它移动到独立实验包，不让默认安装和项目主 UI 依赖它。
- [ ] 运行后端全量、前端全量、生产构建和 3 类用户旅程验收。

## 阶段 6：用户旅程验收

**Files:**
- Create: `项目推进记录/YYYY-MM-DD-项目工作台验收报告.md`
- Create: `项目推进记录/YYYY-MM-DD-项目工作台原始记录/`

- [ ] 初学者任务：导入 Flask/FastAPI 小项目，完成体检、启动、入口解释和一个文案二改。
- [ ] 开发者任务：导入带测试项目，建立基线、修复小缺陷、运行测试、启动服务并导出证据。
- [ ] 体验者任务：准备、启动、打开、停止和清理一个前端 Demo。
- [ ] 记录首次下一步时间、失败次数、端口/工作区可见性、证据完整度、二改变更与验证关联。
- [ ] 汇总产品指标和 Harness 指标，决定是否进入 P4/P5 后续可靠性工作。

## 验证总表

- 后端：`.venv\Scripts\python.exe -m pytest -q`
- 前端：`cd src\web; npm test -- --run`
- 构建：`cd src\web; npm run build`
- 协议扫描：事件、摘要和 SSE 不含 DSML、`<tool_call>`、`<function=`。
- 监听边界：Explorer 仅监听 `127.0.0.1:7788`。
- 关键回归：Activity、任务详情、变更、验证、进程状态、LangSmith 状态和旧兼容端点均可访问。
