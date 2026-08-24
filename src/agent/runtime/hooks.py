"""生命周期钩子：用户配置的命令在 agent 生命周期事件点触发。

语义对齐 Claude Code hooks：
- pre_tool 钩子退出码 2 = 阻断该工具调用（stderr 作为失败原因反馈给模型）；
- 其余事件钩子（session_start / post_tool / session_end）失败不阻断任务，
  后台执行并丢弃异常（避免用户脚本 bug 拖垮 agent 循环）；
- matcher 是正则：pre/post_tool 匹配工具名，其余事件匹配用户消息文本；
- 载荷通过 stdin 以 JSON 传入；命令经系统 shell 执行（Windows 用 cmd）。
"""

import asyncio
import json
import re
import subprocess
from dataclasses import dataclass, field

HOOK_EVENTS = ("session_start", "pre_tool", "post_tool", "session_end")
_BLOCK_EXIT_CODE = 2


@dataclass
class HookConfig:
    event: str
    command: str
    matcher: str = ""
    enabled: bool = True
    timeout: float = 20.0


def parse_hook_configs(raw) -> list[HookConfig]:
    """从存储的 JSON 结构解析钩子配置，非法项丢弃。"""
    configs: list[HookConfig] = []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return configs
    if not isinstance(raw, list):
        return configs
    for item in raw:
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "")
        command = str(item.get("command") or "").strip()
        if event not in HOOK_EVENTS or not command:
            continue
        configs.append(HookConfig(
            event=event,
            command=command,
            matcher=str(item.get("matcher") or ""),
            enabled=bool(item.get("enabled", True)),
            timeout=min(60.0, max(0.5, float(item.get("timeout") or 20.0))),
        ))
    return configs


def serialize_hook_configs(configs: list[HookConfig]) -> str:
    return json.dumps(
        [
            {
                "event": config.event,
                "command": config.command,
                "matcher": config.matcher,
                "enabled": config.enabled,
                "timeout": config.timeout,
            }
            for config in configs
        ],
        ensure_ascii=False,
    )


@dataclass
class HookOutcome:
    blocked_reason: str | None = None
    failures: list[str] = field(default_factory=list)


class HookRunner:
    """执行匹配事件的钩子命令。config_provider 每次触发时重新读取配置。"""

    def __init__(self, config_provider):
        self._config_provider = config_provider
        self._background: set[asyncio.Task] = set()

    def _matching(self, event: str, match_text: str) -> list[HookConfig]:
        try:
            configs = self._config_provider() or []
        except Exception:
            return []
        result = []
        for config in configs:
            if not config.enabled or config.event != event:
                continue
            if config.matcher:
                try:
                    if not re.search(config.matcher, match_text or ""):
                        continue
                except re.error:
                    continue
            result.append(config)
        return result

    async def fire(
        self,
        event: str,
        payload: dict,
        *,
        block_on: bool = False,
        match_text: str = "",
    ) -> HookOutcome | None:
        """触发事件钩子。block_on 时等待结果（pre_tool 阻断语义），
        返回 blocked_reason；其余事件后台执行。无匹配钩子返回 None（快路径）。"""
        hooks = self._matching(event, match_text)
        if not hooks:
            return None
        outcome = HookOutcome()
        for hook in hooks:
            if block_on:
                reason = await self._run_one(hook, payload)
                if reason:
                    outcome.blocked_reason = reason
                    break
            else:
                task = asyncio.create_task(self._run_one(hook, payload))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
        return outcome

    async def drain(self) -> None:
        """等待所有后台钩子完成（测试与关停用）。"""
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    @staticmethod
    async def _run_one(config: HookConfig, payload: dict) -> str | None:
        """运行单个钩子。pre_tool 阻断时返回原因，其余情况返回 None。"""
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                config.command,
                input=json.dumps(payload, ensure_ascii=False, default=str),
                capture_output=True,
                text=True,
                timeout=config.timeout,
                shell=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return None
        if config.event == "pre_tool" and completed.returncode == _BLOCK_EXIT_CODE:
            reason = (completed.stderr or completed.stdout or "").strip()
            return reason or "钩子已阻断该操作"
        return None
