# Diary: 修复环境模型认证丢失

本次任务修复 7788 服务在发送普通对话时丢失模型凭证、直接暴露 Anthropic SDK 认证异常的问题。

## Step 1: 复现并追踪配置链路

### Prompt Context

**Verbatim prompt:** 刚刚\n你好\n\n**E**\n**Explorer**\n刚刚\n错误：Agent 运行失败: "Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set. Or for one of the X-Api-Key or Authorization headers to be explicitly omitted"

**Interpretation:** 用户在 7788 页面发送“你好”时，Agent 在首次 LLM 调用前因缺少认证参数失败。

**Inferred intent:** 恢复当前模型配置的正常对话能力，并找出凭证为何没有进入 SDK 客户端。

### What I did

使用 `POST /api/agent/chat/stream` 稳定复现错误；读取 `/api/settings` 的非敏感状态；检查 `/src/main.py` 中 `.env` 加载、模型解析和 `_apply_model()`，以及 `/src/agent/llm.py` 中 Anthropic 客户端初始化。检查仅输出凭证是否存在，未读取或记录凭证值。

### Why

认证信息会经过 `.env -> MODEL_CONFIGS -> _apply_model() -> 进程环境 -> _get_client_kwargs() -> Anthropic SDK` 多个边界，必须确定信息在哪个边界消失，不能直接在 SDK 层掩盖异常。

### What worked

请求 `POST /api/agent/chat/stream` 稳定返回相同 SSE 错误，因此问题可复现。证据表明 `.env` 的模型、URL 和凭证均已设置，但环境模型不在内置列表中，`data/model_configs.json` 也不存在。启动代码因此回退到第一个内置模型，再由 `_apply_model()` 删除 `LLM_API_KEY` 和 `ANTHROPIC_API_KEY`。

### What didn't work

首次并行诊断命令包含不正确的 PowerShell 引号，原样错误为：`The string is missing the terminator: ".`，错误 ID 为 `TerminatorExpectedAtEndOfString`。拆分命令后完成取证。首次绿色测试仍失败，因为导入 `main` 时残留的 `LLM_MODEL` 覆盖了测试设置；补齐测试环境隔离后解决。

### What I learned

连接测试直接使用表单中的 URL 和 Key，所以可以成功；聊天链路依赖全局活动模型和进程环境，因此两条路径并不共享同一份最终配置。环境模型未注册时的静默回退是凭证丢失的源头。

### What was tricky

必须区分“凭证意外丢失”和“用户明确配置无 Key 的本地服务”。修复不能简单保留上一个模型的 Key，否则切换到无鉴权服务时可能串用旧凭证。

### What warrants review

重点复查 `/src/main.py` 的 `_load_model_configs()` 与初始模型选择逻辑，确认环境模型只在尚未注册时注入，且不会改变普通自定义模型的切换语义。

### Future work

设置数据目前仍以全局进程环境作为运行时传递方式。未来如支持并发会话使用不同模型，应改为每个任务携带不可变模型快照，而不是继续扩展全局环境变量。

## Step 2: 回归测试与最小修复

### Prompt Context

**Verbatim prompt:** 同 Step 1。

**Interpretation:** 为当前故障建立可重复验证的修复边界。

**Inferred intent:** 修复后重启仍能自动使用 `.env` 中的真实模型配置。

### What I did

先在 `/tests/test_model_settings.py` 增加“未注册环境模型应带着协议、URL 和凭证进入模型列表”的测试，并确认它以 `StopIteration` 失败。随后在 `/src/main.py` 中注入唯一的 `environment-model` 运行时条目，并让无效 `MODEL_CONFIG_ID` 回退到 `LLM_MODEL/ANTHROPIC_MODEL`。复查时又先写测试证明环境条目会被保存，再通过 `source=environment` 标记让它不参与配置文件持久化。

### Why

修复配置源头后，既不需要在 SDK 层伪造认证参数，也不需要让无 Key 模型继承其他模型的凭证。

### What worked

RED 命令：`.\\.venv\\Scripts\\python.exe -m pytest tests\\test_model_settings.py::test_unregistered_environment_model_is_loaded_with_its_credentials -q`，结果为 `1 failed`，失败点为预期的 `StopIteration`。补齐实现与测试隔离后，首个定向测试为 `1 passed`，模型设置与 LLM 响应相关测试为 `14 passed`。新增的“不持久化环境凭证”测试先稳定失败，再随最小过滤实现转绿；模型设置测试最终为 `12 passed`。

### What didn't work

第一次 GREEN 仍显示同一测试失败。原因不是实现无效，而是测试进程已由模块导入写入 `LLM_MODEL=mimo-v2.5`；测试随后显式清理 `LLM_MODEL`、`LLM_API_KEY` 和 `LLM_BASE_URL`，确保只验证预期环境输入。首次安全扫描脚本还因 PowerShell `$Matches` 被第二次正则覆盖而报空值错误；拆开变量赋值后重新检查了 3 个本地敏感值，均未出现在改动文件中。

### What I learned

模块导入期间修改进程环境会让测试顺序影响结果。涉及模型启动配置的测试必须完整隔离优先级更高的 `LLM_*` 变量。

### What was tricky

环境模型配置包含密钥，但 API 只通过 `_public_model()` 暴露掩码和 `has_key`，不能在测试、日志或 Trace 中打印原值。

### What warrants review

验证设置 API 中活动模型应为 `environment-model` 且 `has_key=true`；验证真实聊天请求不再返回认证方法解析错误。

### Future work

完成全量后端测试和真实 7788 服务验收后补充最终结果。

## Step 3: 全量回归与 7788 真实验收

### Prompt Context

**Verbatim prompt:** 同 Step 1。

**Interpretation:** 修复不仅要通过单元测试，还必须在用户当前使用的 7788 服务中恢复真实对话。

**Inferred intent:** 用户刷新页面后可直接继续使用，无需手动重新填写或切换模型。

### What I did

使用项目 `.venv` 跑完整后端测试；停止旧的 7788 父子进程；用 `.venv\\Scripts\\python.exe src\\main.py` 隐藏启动新服务；检查 TCP 监听、设置接口和真实 `POST /api/agent/chat/stream` SSE 响应。

### Why

模块测试只能证明配置解析，真实请求还会经过 FastAPI、运行时上下文、LLM 客户端和外部模型网关，必须覆盖完整链路。

### What worked

全量命令 `.\\.venv\\Scripts\\python.exe -m pytest -q` 返回 `98 passed, 1 warning`。最终服务实际监听 PID 为 `46292`，`netstat -ano` 只显示 `127.0.0.1:7788`。设置接口返回活动模型 `environment-model`、`has_key=true`。真实“你好”请求返回 HTTP 200、7 个 SSE 事件、0 个错误、`done.status=completed`，且响应不含 `Could not resolve authentication method`。

### What didn't work

本步骤没有新的功能失败。Start-Process 返回的启动器 PID 不是最终监听 PID，这是 Windows 虚拟环境启动器的既有行为，因此验收继续以 `netstat` 的监听 PID 为准。

### What I learned

服务健康检查必须同时确认 HTTP/SSE 行为、活动模型非敏感状态和真实 TCP 监听，单看启动器 PID 不足以证明服务可用。

### What was tricky

验收输出不能包含模型回复中的潜在敏感内容，因此只统计事件数量、错误数量、完成状态和响应是否非空。

### What warrants review

复查 `/src/main.py` 和 `/tests/test_model_settings.py`；在页面发送普通问候并确认可收到完整回复。服务日志位于被 Git 忽略的 `/data/server.stdout.log` 与 `/data/server.stderr.log`。

### Future work

当前没有阻断项。若后续实现单会话模型选择，应把模型凭证解析从进程全局状态迁移到任务级配置对象。
