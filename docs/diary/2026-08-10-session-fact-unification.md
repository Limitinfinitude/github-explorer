# Diary: Session Fact Unification

本日记记录将浏览器会话缓存与 SQLite 对话事实统一的实现过程。

## Step 1: 现状与范围

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 在事件流完成后继续记忆系统路线，优先解决会话历史事实源不统一。

**Inferred intent:** Agent 在刷新、恢复或更换前端环境后仍应看到后端保存的真实对话，而不是只依赖当前浏览器 localStorage。

### What I did

检查 `/src/web/src/hooks/useChats.ts`、`/src/web/src/App.tsx`、`/src/web/src/lib/api.ts` 和 `/src/routes_agent.py`。确认前端会话消息完全从 `localStorage` 加载，后端已经有 `/api/agent/history/{session_id}`，但启动时没有被调用。

### Why

同一 session 同时存在浏览器缓存和 SQLite 两个事实源会造成旧回复、跨设备缺失和 resume 上下文不一致。本阶段只统一读取路径，不改后端消息表结构。

### What worked

后端历史接口已经存在，且 Agent 路由在完成任务后会写入 `conversations`，因此无需新增 API 或数据库表。

### What didn't work

本步骤尚未修改生产代码；后续先用纯适配器测试锁定前端消息映射行为。

### What I learned

UI 的数值 chat id 与后端 session id 是两个不同标识，不能互相替代；适配器必须使用 session id 和消息索引生成稳定前端 id。

### What was tricky

历史接口可能暂时不可用，hydration 失败不能清空已有缓存，否则短暂网络故障会直接让用户看不到本地对话。

### What warrants review

检查多标签页同时写入时 localStorage 的最后写入覆盖问题；本阶段不引入跨标签页同步，后续可用事件流或 BroadcastChannel 处理。

### Future work

完成 hydration 后再把 FTS5 项目记忆建立在 SQLite 事实源上。

## Step 2: 历史适配器与会话 hydration

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 实现并验证 SQLite 历史到前端聊天状态的单向同步。

**Inferred intent:** 页面刷新后应恢复后端真实历史，且短暂 API 故障不能删除本地缓存。

### What I did

新增 `/src/web/src/lib/chatHistory.ts`，把后端 `role/content/timestamp` 转换为稳定 `Message`。新增 `/src/web/src/lib/api.ts` 的 `getHistory()`，在 `/src/web/src/hooks/useChats.ts` 增加 `hydrateChat()`，并由 `/src/web/src/App.tsx` 对每个 session 只同步一次。后端返回真实消息时替换缓存；空历史或请求失败时保留现有 localStorage。

### Why

后端 SQLite 是任务完成后写入的事实源，但旧版本用户可能只有本地历史。只在返回非空历史时替换，可以统一新数据且不丢失旧版本尚未迁移的缓存。

### What worked

适配器测试验证了 user/assistant 映射、过滤 system 角色、稳定 id 和服务端时间。首次实现后 `npm test --prefix src\web` 返回 `13 passed`。

### What didn't work

RED 命令返回 `12 passed, 1 failed`，准确错误为 `ERR_MODULE_NOT_FOUND`，缺少 `/src/web/src/lib/chatHistory.ts`，符合测试预期。当前环境未暴露浏览器控制接口，因此未宣称完成自动点击级 UI 验收。

### What I learned

session hydration 必须用 ref 记录已请求 session，否则聊天 state 更新会触发 effect 重复请求。转换函数独立于 React 后可用 Node 测试稳定验证。

### What was tricky

完全以空后端历史覆盖旧 localStorage 会造成迁移期数据丢失，因此空历史被视为“无可替换事实”，而不是删除指令。

### What warrants review

当前后端没有聊天删除 API，所以删除浏览器 chat 只删除 UI 项；未来若需要真正删除会话，必须增加显式后端删除/归档语义，不能把空历史当删除。

### Future work

增加 SQLite FTS5 项目记忆，并从已验证事件和用户显式记录中生成可检索事实。

## Step 3: 验证与运行

### Prompt Context

**Verbatim prompt:** `继续进行吧，直至达标`

**Interpretation:** 完成前后端回归和真实服务验收。

**Inferred intent:** 会话同步不能破坏现有 Agent、Activity 或桌面发布包。

### What I did

执行前端测试、Vite 构建和后端全量测试，重新启动 7788 并请求历史接口。

### Why

会话改动跨越 Hook、API、bundle 和 FastAPI，需要同时验证前端类型、发布产物和后端兼容。

### What worked

前端 `13 passed`；后端 `92 passed, 1 warning`；Vite 成功转换 `1613 modules`；首页 `HTTP 200`；空 session 历史返回 `0` 条；`netstat` 仅显示 `127.0.0.1:7788`。最终服务保留运行，PID `32988`。

### What didn't work

唯一 warning 仍是既有 Starlette/httpx 弃用提醒。自动浏览器控制工具不可用，因此可见 UI 由用户在已经打开的内置浏览器刷新验收。

### What I learned

后端历史接口无需改动即可作为前端事实源，关键缺口是前端启动路径没有 hydration。

### What was tricky

构建后必须提交更新的 `/src/web_dist`，否则桌面启动继续加载旧 bundle，即使源码和开发构建都正确。

### What warrants review

多浏览器和多标签页并发编辑仍可能产生 localStorage 缓存覆盖；SQLite 事实不会丢失，但当前标签页不会实时收到其他标签页更新。

### Future work

进入项目长期记忆阶段，先使用 FTS5 和来源/置信度/验证状态，不引入 embeddings。
