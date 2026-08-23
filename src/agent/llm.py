"""
LLM 客户端封装 — 基于原生 Anthropic SDK

不使用 langchain-anthropic，直接调用 Anthropic API。
调用证据由 LocalAgentRuntime 写入本地事件存储；外部观测不参与调用控制。
"""
import os
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 修复 SSL_CERT_FILE
_ssl_cert = os.environ.get("SSL_CERT_FILE")
if _ssl_cert and not Path(_ssl_cert).is_file():
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()

import anthropic
import httpx


_TEXT_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*<function=([A-Za-z_][\w.-]*)>\s*(.*?)</tool_call>",
    re.DOTALL,
)
_TEXT_TOOL_PARAMETER_RE = re.compile(
    r"<parameter=([A-Za-z_][\w.-]*)>\s*(.*?)</parameter>",
    re.DOTALL,
)
_DSML_TOOL_CALL_RE = re.compile(
    r'<｜｜DSML｜｜invoke\s+name="([A-Za-z_][\w.-]*)">\s*(.*?)</｜｜DSML｜｜invoke>',
    re.DOTALL,
)
_DSML_TOOL_PARAMETER_RE = re.compile(
    r'<｜｜DSML｜｜parameter\s+name="([A-Za-z_][\w.-]*)"[^>]*>\s*(.*?)</｜｜DSML｜｜parameter>',
    re.DOTALL,
)


def _parse_text_tool_calls(text: str) -> tuple[str, list[dict]]:
    tool_uses: list[dict] = []
    consumed: list[tuple[int, int]] = []
    for index, match in enumerate(_TEXT_TOOL_CALL_RE.finditer(text), start=1):
        name, body = match.groups()
        params = {}
        valid = True
        for parameter in _TEXT_TOOL_PARAMETER_RE.finditer(body):
            key, raw_value = parameter.groups()
            try:
                params[key] = json.loads(raw_value.strip())
            except json.JSONDecodeError:
                valid = False
                break
        if not valid or not params:
            continue
        tool_uses.append({
            "id": f"text-tool-{index}",
            "name": name,
            "input": params,
        })
        consumed.append(match.span())

    for index, match in enumerate(_DSML_TOOL_CALL_RE.finditer(text), start=len(tool_uses) + 1):
        name, body = match.groups()
        params = {}
        valid = True
        for parameter in _DSML_TOOL_PARAMETER_RE.finditer(body):
            key, raw_value = parameter.groups()
            try:
                params[key] = json.loads(raw_value.strip())
            except json.JSONDecodeError:
                valid = False
                break
        if not valid or not params:
            continue
        tool_uses.append({"id": f"text-tool-{index}", "name": name, "input": params})
        consumed.append(match.span())

    if not consumed:
        return text, []
    pieces = []
    cursor = 0
    for start, end in sorted(consumed):
        pieces.append(text[cursor:start])
        cursor = end
    pieces.append(text[cursor:])
    cleaned = "".join(pieces)
    cleaned = re.sub(r"</?｜｜DSML｜｜tool_calls>", "", cleaned)
    return cleaned.strip(), tool_uses


def _get_client_kwargs(binding: "ModelBinding | None" = None) -> dict:
    """构建客户端参数。显式传入 binding 时以绑定为准，否则读取当前环境变量。"""
    kwargs = {}
    api_key = binding.api_key if binding else (
        os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    )
    base_url = binding.base_url if binding else (
        os.environ.get("LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    )
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return kwargs


@dataclass(frozen=True)
class ModelBinding:
    """一次模型调用的固定绑定，避免运行中任务被全局配置切换改道。"""

    model: str
    protocol: str
    base_url: str = ""
    api_key: str = ""
    thinking_effort: str = "off"
    thinking_budget_tokens: int = 0


def capture_model_binding() -> ModelBinding:
    """从当前进程环境生成一个绑定快照。"""
    return ModelBinding(
        model=get_model(),
        protocol=get_protocol(),
        base_url=os.environ.get("LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL", ""),
        api_key=os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", ""),
        thinking_effort=os.environ.get("LLM_THINKING_EFFORT", "off"),
        thinking_budget_tokens=int(os.environ.get("LLM_THINKING_BUDGET_TOKENS", "0") or 0),
    )


_THINKING_BUDGET_DEFAULT = {"high": 4096, "max": 16384}
_REASONING_EFFORT_MAP = {"high": "high", "max": "max"}


def _thinking_budget(binding: ModelBinding) -> int:
    if binding.thinking_budget_tokens > 0:
        return binding.thinking_budget_tokens
    return _THINKING_BUDGET_DEFAULT.get(binding.thinking_effort, 4096)


def _openai_reasoning_effort(binding: ModelBinding) -> str | None:
    return _REASONING_EFFORT_MAP.get(binding.thinking_effort)


def get_model() -> str:
    """获取当前模型名称"""
    return os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")


def get_protocol() -> str:
    return os.environ.get("LLM_PROTOCOL", "anthropic").lower()


def _extract_cache_tokens(usage) -> int | None:
    """提取 prompt 缓存命中 token 数（DeepSeek/OpenAI/Anthropic 各有字段）。"""
    if not isinstance(usage, dict):
        return None
    for key in ("prompt_cache_hit_tokens",):
        value = usage.get(key)
        if isinstance(value, int):
            return value
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens")
        if isinstance(cached, int):
            return cached
    return None


def _usage_metadata(input_tokens, output_tokens, total_tokens=None, cache_hit=None) -> dict | None:
    if input_tokens is None and output_tokens is None:
        return None
    input_count = int(input_tokens or 0)
    output_count = int(output_tokens or 0)
    metadata = {
        "input_tokens": input_count,
        "output_tokens": output_count,
        "total_tokens": int(total_tokens if total_tokens is not None else input_count + output_count),
    }
    if cache_hit is not None:
        metadata["cache_hit_tokens"] = int(cache_hit)
    return metadata


def _openai_endpoint(base_url: str) -> str:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    converted: list[dict] = [{"role": "system", "content": system}]
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue

        text_parts = [
            block.get("text", "") for block in content
            if block.get("type") == "text" and block.get("text")
        ]
        if role == "assistant":
            tool_calls = []
            for block in content:
                if block.get("type") != "tool_use":
                    continue
                tool_calls.append({
                    "id": block["id"],
                    "type": "function",
                    "function": {
                        "name": block["name"],
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    },
                })
            item = {"role": "assistant", "content": "\n".join(text_parts) or None}
            if tool_calls:
                item["tool_calls"] = tool_calls
            converted.append(item)
            continue

        if text_parts:
            converted.append({"role": role, "content": "\n".join(text_parts)})
        for block in content:
            if block.get("type") == "tool_result":
                converted.append({
                    "role": "tool",
                    "tool_call_id": block["tool_use_id"],
                    "content": str(block.get("content", "")),
                })
    return converted


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    } for tool in tools]


async def _call_openai_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    temperature: float,
    binding: "ModelBinding | None" = None,
) -> dict:
    resolved = binding or capture_model_binding()
    api_key = resolved.api_key
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": resolved.model,
        "messages": _to_openai_messages(system, messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    reasoning_effort = _openai_reasoning_effort(resolved)
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if tools:
        payload["tools"] = _to_openai_tools(tools)

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            _openai_endpoint(resolved.base_url or "https://api.openai.com/v1"),
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()

    choice = data["choices"][0]
    message = choice.get("message", {})
    content = message.get("content") or ""
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    text, text_tool_uses = _parse_text_tool_calls(str(content))
    tool_uses = []
    for call in message.get("tool_calls") or []:
        function = call.get("function", {})
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        tool_uses.append({
            "id": call.get("id") or f"tool-{len(tool_uses) + 1}",
            "name": function.get("name", ""),
            "input": arguments,
        })
    tool_uses.extend(text_tool_uses)
    result = {
        "stop_reason": "tool_use" if tool_uses else choice.get("finish_reason", "end_turn"),
        "text": text,
        "tool_uses": tool_uses,
    }
    reasoning = message.get("reasoning") or message.get("reasoning_content") or ""
    if reasoning:
        result["thinking"] = str(reasoning)
    usage = data.get("usage") or {}
    metadata = _usage_metadata(
        usage.get("prompt_tokens") or usage.get("input_tokens"),
        usage.get("completion_tokens") or usage.get("output_tokens"),
        usage.get("total_tokens"),
        cache_hit=_extract_cache_tokens(usage),
    )
    if metadata:
        result["usage_metadata"] = metadata
    return result


async def call_llm(
    system: str,
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.7,
    *,
    binding: "ModelBinding | None" = None,
) -> str:
    """
    调用 Claude API，返回文本响应。

    直接使用 Anthropic SDK，不经过任何上层封装。
    每一个 token 都在精确控制之下。
    """
    resolved = binding or capture_model_binding()
    if resolved.protocol == "openai":
        result = await _call_openai_with_tools(
            system, messages, [], max_tokens, temperature, binding=resolved,
        )
        return result["text"]

    client = anthropic.AsyncAnthropic(**_get_client_kwargs(resolved))
    try:
        response = await client.messages.create(
            model=resolved.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        )
        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text
        return result.strip()
    finally:
        await client.close()


async def call_llm_stream(
    system: str,
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.7,
    *,
    binding: "ModelBinding | None" = None,
):
    """
    流式调用 Claude API，逐 token yield 文本。

    用于 SSE 流式输出，每个 yield 是一个文本片段。
    """
    resolved = binding or capture_model_binding()
    if resolved.protocol == "openai":
        yield await call_llm(system, messages, max_tokens, temperature, binding=resolved)
        return

    client = anthropic.AsyncAnthropic(**_get_client_kwargs(resolved))
    try:
        async with client.messages.stream(
            model=resolved.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
    finally:
        await client.close()


async def call_llm_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4000,
    temperature: float = 0.7,
    *,
    binding: "ModelBinding | None" = None,
) -> dict:
    """
    调用 Claude API，支持结构化工具调用。

    返回 {"stop_reason": "tool_use"|"end_turn", "content": [...blocks], "text": "..."}
    """
    resolved = binding or capture_model_binding()
    if resolved.protocol == "openai":
        return await _call_openai_with_tools(
            system, messages, tools, max_tokens, temperature, binding=resolved,
        )

    client = anthropic.AsyncAnthropic(**_get_client_kwargs(resolved))
    try:
        params = {
            "model": resolved.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if resolved.thinking_effort != "off":
            # Anthropic 开启思考时 temperature 必须为 1，budget 从 max_tokens 中扣除
            params["temperature"] = 1
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": _thinking_budget(resolved),
            }
        response = await client.messages.create(**params)
        text_parts = []
        thinking_parts = []
        tool_uses = []
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text_parts.append(block.text)
            elif block.type == "thinking":
                thinking_parts.append(getattr(block, "thinking", ""))
            elif block.type == "tool_use":
                tool_uses.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        text = "\n".join(text_parts)
        text, text_tool_uses = _parse_text_tool_calls(text)
        tool_uses.extend(text_tool_uses)
        result = {
            "stop_reason": "tool_use" if text_tool_uses else response.stop_reason,
            "text": text,
            "tool_uses": tool_uses,
        }
        thinking = "\n".join(part for part in thinking_parts if part)
        if thinking:
            result["thinking"] = thinking
        usage = getattr(response, "usage", None)
        metadata = _usage_metadata(
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            cache_hit=getattr(usage, "cache_read_input_tokens", None),
        )
        if metadata:
            result["usage_metadata"] = metadata
        return result
    finally:
        await client.close()


async def call_llm_json(
    system: str,
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.3,
    *,
    binding: "ModelBinding | None" = None,
) -> dict:
    """
    调用 Claude API，解析 JSON 响应。

    用于意图识别、项目分析等需要结构化输出的场景。
    """
    raw = await call_llm(system, messages, max_tokens, temperature, binding=binding)
    # 尝试提取 JSON
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:  # 取代码块内容
            if part.startswith("json"):
                part = part[4:]
            try:
                return json.loads(part.strip())
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


async def _stream_openai_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    temperature: float,
    resolved: ModelBinding,
) -> AsyncIterator[dict]:
    """OpenAI 兼容流式：逐块 yield token/thinking，结束 yield done 携带完整响应。"""
    headers = {"Content-Type": "application/json"}
    if resolved.api_key:
        headers["Authorization"] = f"Bearer {resolved.api_key}"
    payload = {
        "model": resolved.model,
        "messages": _to_openai_messages(system, messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    reasoning_effort = _openai_reasoning_effort(resolved)
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if tools:
        payload["tools"] = _to_openai_tools(tools)

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_builds: dict[int, dict] = {}
    stop_reason = "end_turn"
    usage: dict | None = None

    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream(
            "POST",
            _openai_endpoint(resolved.base_url or "https://api.openai.com/v1"),
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if chunk.get("usage"):
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                finish = choice.get("finish_reason")
                if finish and finish != "stop":
                    stop_reason = finish
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    text_parts.append(content)
                    yield {"type": "token", "content": content}
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    thinking_parts.append(reasoning)
                    yield {"type": "thinking", "content": reasoning}
                for call in delta.get("tool_calls") or []:
                    index = int(call.get("index", 0))
                    builder = tool_builds.setdefault(index, {"id": None, "name": None, "arguments": ""})
                    if call.get("id"):
                        builder["id"] = call["id"]
                    function = call.get("function") or {}
                    if function.get("name"):
                        builder["name"] = function["name"]
                    if function.get("arguments"):
                        builder["arguments"] += function["arguments"]

    tool_uses = []
    for index in sorted(tool_builds):
        builder = tool_builds[index]
        name = builder["name"] or ""
        try:
            arguments = json.loads(builder["arguments"] or "{}")
        except json.JSONDecodeError:
            arguments = {}
        tool_uses.append({
            "id": builder["id"] or f"tool-{index + 1}",
            "name": name,
            "input": arguments,
        })
    text = "".join(text_parts)
    text, text_tool_uses = _parse_text_tool_calls(text)
    tool_uses.extend(text_tool_uses)
    thinking = "".join(thinking_parts)
    result = {
        "stop_reason": "tool_use" if tool_uses else stop_reason,
        "text": text,
        "tool_uses": tool_uses,
    }
    if thinking:
        result["thinking"] = thinking
    if usage:
        metadata = _usage_metadata(
            usage.get("prompt_tokens") or usage.get("input_tokens"),
            usage.get("completion_tokens") or usage.get("output_tokens"),
            usage.get("total_tokens"),
            cache_hit=_extract_cache_tokens(usage),
        )
        if metadata:
            result["usage_metadata"] = metadata
    yield {"type": "done", "response": result}


async def _stream_anthropic_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int,
    temperature: float,
    resolved: ModelBinding,
) -> AsyncIterator[dict]:
    """Anthropic 原生流式：逐块 yield token/thinking，结束 yield done 携带完整响应。"""
    client = anthropic.AsyncAnthropic(**_get_client_kwargs(resolved))
    try:
        params = {
            "model": resolved.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        if resolved.thinking_effort != "off":
            params["temperature"] = 1
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": _thinking_budget(resolved),
            }
        async with client.messages.stream(**params) as stream:
            async for event in stream:
                if event.type != "content_block_delta":
                    continue
                delta = event.delta
                if delta.type == "text_delta" and getattr(delta, "text", None):
                    yield {"type": "token", "content": delta.text}
                elif delta.type == "thinking_delta" and getattr(delta, "thinking", None):
                    yield {"type": "thinking", "content": delta.thinking}
            final = await stream.get_final_message()
    finally:
        await client.close()

    text_parts: list[str] = []
    tool_uses = []
    thinking_parts: list[str] = []
    for block in final.content:
        if hasattr(block, "text") and block.text:
            text_parts.append(block.text)
        elif block.type == "thinking":
            thinking_parts.append(getattr(block, "thinking", ""))
        elif block.type == "tool_use":
            tool_uses.append({"id": block.id, "name": block.name, "input": block.input})
    text = "".join(text_parts)
    text, text_tool_uses = _parse_text_tool_calls(text)
    tool_uses.extend(text_tool_uses)
    result = {
        "stop_reason": "tool_use" if tool_uses else final.stop_reason,
        "text": text,
        "tool_uses": tool_uses,
    }
    thinking = "".join(part for part in thinking_parts if part)
    if thinking:
        result["thinking"] = thinking
    usage = getattr(final, "usage", None)
    metadata = _usage_metadata(
        getattr(usage, "input_tokens", None),
        getattr(usage, "output_tokens", None),
        cache_hit=getattr(usage, "cache_read_input_tokens", None),
    )
    if metadata:
        result["usage_metadata"] = metadata
    yield {"type": "done", "response": result}


async def stream_llm_with_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4000,
    temperature: float = 0.2,
    *,
    binding: "ModelBinding | None" = None,
) -> AsyncIterator[dict]:
    """流式调用支持工具的模型，逐块 yield：

    - {"type": "token", "content": str}    文本增量
    - {"type": "thinking", "content": str} 思考增量
    - {"type": "done", "response": {...}}  完整响应（text/tool_uses/thinking/usage）

    供 LocalAgentRuntime 的 SSE 逐字流式输出使用。
    """
    resolved = binding or capture_model_binding()
    if resolved.protocol == "openai":
        async for chunk in _stream_openai_with_tools(
            system, messages, tools, max_tokens, temperature, resolved,
        ):
            yield chunk
        return
    async for chunk in _stream_anthropic_with_tools(
        system, messages, tools, max_tokens, temperature, resolved,
    ):
        yield chunk
