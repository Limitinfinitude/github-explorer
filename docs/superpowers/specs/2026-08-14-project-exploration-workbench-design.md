# 开源项目探索工作台设计规格

> 日期：2026-08-14（Asia/Shanghai）
> 状态：已基于产品定位确认，待实现前评审

## 目标

把现有以 Chat、Explore、Activity 分散承载的能力，重组为一个以项目旅程为主线的工作台，同时完整保留开发者需要的运行流水账和证据。初学者可以只看摘要，开发者可以展开并筛选全链路数据。

## 用户与场景

- 初学者导入 GitHub URL，查看项目体检，准备环境，启动项目并得到下一步学习任务。
- 开发者在同一项目中建立基线、修改文件、运行测试、启动服务，展开工具调用和进程证据排查问题。
- 体验者只使用准备、启动、打开、停止和清理入口。

## 信息架构

### 项目页

项目页替代单一 Explore 结果页，包含：

1. 项目头部：名称、来源 URL、本地路径、技术栈、当前会话、运行状态。
2. 旅程阶段：体检、跑起来、看懂、实验室、记录。
3. 阶段摘要：阶段状态、完成条件、下一步动作、关键风险。
4. 开发者证据抽屉：按任务/工具/错误/恢复/进程/文件/验证筛选，展示完整事件和原始诊断。
5. 实验卡片：基线、目标、变更集、测试、服务、结果和报告导出。

Chat 仍作为右侧或底部的贯穿助手，不承担项目状态的唯一来源。Activity 的现有能力并入“记录”阶段，同时保留独立入口作为跨项目审计视图。

### 默认显示与展开规则

- 默认折叠工具参数、原始命令输出和协议细节。
- 默认显示状态、耗时、变更文件数、验证结果、服务地址、失败原因和下一步。
- 开发者展开后可复制命令、查看 cwd/workspace root、打开证据文件和 LangSmith Trace。
- 空任务、失败任务和未验证任务必须保留，不用“完成结果”覆盖。

## 数据流

```text
GitHub URL / Local path
        -> Project intake + health check
        -> LocalAgentRuntime
        -> Session Event Store
        -> Project projection / Chat projection / Activity projection
        -> User summary + Developer evidence
```

事件事实至少包括 session、task、turn、step、tool call、workspace、cwd、process、change、verification、approval、error、trace_id。LangSmith 只能消费事件投影，不能决定项目阶段是否完成。

## 前端组件边界

- `ProjectWorkspaceView`：项目头部、旅程阶段和当前摘要。
- `ProjectStageRail`：阶段切换与完成条件。
- `ProjectEvidenceDrawer`：开发者流水账、事件筛选和详情。
- `ExperimentCard`：二改任务、基线、变更和验证。
- `ActivityView`：保留跨项目全局运行记录，复用事件投影组件。
- `ChatPanel`：仅负责对话和助手建议，接收当前项目/阶段上下文。

首轮只重组现有数据和组件，不新增云端协作、IDE 编辑器或项目自动修复能力。

## LangGraph 边界

`LocalAgentRuntime` 是新主链；LangGraph 旧入口只允许读取或转换到统一事件。新增功能不得 import `agent.graph` 或 `agent.swarm_graph`。迁移顺序：

1. 只读项目分析、学习路径、解释接口改为 Runtime read-only task。
2. setup/confirm 改为 Runtime 的 approval/resume。
3. Swarm 保持实验性路由，记录调用量，不接入项目主界面。
4. 旧入口零调用且回归通过后，删除默认依赖或拆成独立实验包。

## 验收标准

1. 现有 Activity、任务详情、工具明细、变更、验证和 LangSmith 状态仍可访问。
2. 项目页可以在一个页面查看阶段摘要，并展开同一任务的完整流水账。
3. 失败、恢复、等待确认、取消和未验证状态不会被渲染成成功。
4. 过滤和展开不改变事件事实，不触发额外工具调用。
5. 初学者路径不需要阅读原始命令；开发者路径可以定位工作目录、进程、端口和证据文件。
6. 前端测试覆盖摘要、展开、筛选、空状态、失败状态和移动端布局。
7. 后端测试覆盖事件投影和 legacy LangGraph 路由标记。

## 风险与取舍

- 一个项目页会承载较多信息，必须默认折叠和阶段化，否则会重现当前“流水账压过结果”的问题。
- 保留 LangGraph 会增加依赖和维护成本，但直接删除会破坏现有 API；采用兼容窗口换取可回滚迁移。
- 不把所有模型原始输出放入默认 UI，避免协议泄露和认知负担；原始输出仍保留在受控开发者证据层。
