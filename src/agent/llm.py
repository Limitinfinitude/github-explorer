"""
LLM 客户端封装 — 基于原生 Anthropic SDK

不使用 langchain-anthropic，直接调用 Anthropic API。
调用证据由 LocalAgentRuntime 写入本地事件存储；外部观测不参与调用控制。
"""
import os
import json
import re
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


def _get_client_kwargs() -> dict:
    """构建客户端参数"""
    kwargs = {}
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL") or os.environ.get("ANTHROPIC_BASE_URL")
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    return kwargs


def get_model() -> str:
    """获取当前模型名称"""
    return os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")


def get_protocol() -> str:
    return os.environ.get("LLM_PROTOCOL", "anthropic").lower()


def _usage_metadata(input_tokens, output_tokens, total_tokens=None) -> dict | None:
    if input_tokens is None and output_tokens is None:
        return None
    input_count = int(input_tokens or 0)
    output_count = int(output_tokens or 0)
    return {
        "input_tokens": input_count,
        "output_tokens": output_count,
        "total_tokens": int(total_tokens if total_tokens is not None else input_count + output_count),
    }


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
) -> dict:
    api_key = os.environ.get("LLM_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": get_model(),
        "messages": _to_openai_messages(system, messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        payload["tools"] = _to_openai_tools(tools)

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            _openai_endpoint(os.environ.get("LLM_BASE_URL", "")),
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
    usage = data.get("usage") or {}
    metadata = _usage_metadata(
        usage.get("prompt_tokens") or usage.get("input_tokens"),
        usage.get("completion_tokens") or usage.get("output_tokens"),
        usage.get("total_tokens"),
    )
    if metadata:
        result["usage_metadata"] = metadata
    return result


async def call_llm(
    system: str,
    messages: list[dict],
    max_tokens: int = 2000,
    temperature: float = 0.7,
) -> str:
    """
    调用 Claude API，返回文本响应。

    直接使用 Anthropic SDK，不经过任何上层封装。
    每一个 token 都在精确控制之下。
    """
    if get_protocol() == "openai":
        result = await _call_openai_with_tools(
            system, messages, [], max_tokens, temperature,
        )
        return result["text"]

    client = anthropic.AsyncAnthropic(**_get_client_kwargs())
    try:
        response = await client.messages.create(
            model=get_model(),
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
):
    """
    流式调用 Claude API，逐 token yield 文本。

    用于 SSE 流式输出，每个 yield 是一个文本片段。
    """
    if get_protocol() == "openai":
        yield await call_llm(system, messages, max_tokens, temperature)
        return

    client = anthropic.AsyncAnthropic(**_get_client_kwargs())
    try:
        async with client.messages.stream(
            model=get_model(),
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
) -> dict:
    """
    调用 Claude API，支持结构化工具调用。

    返回 {"stop_reason": "tool_use"|"end_turn", "content": [...blocks], "text": "..."}
    """
    if get_protocol() == "openai":
        return await _call_openai_with_tools(
            system, messages, tools, max_tokens, temperature,
        )

    client = anthropic.AsyncAnthropic(**_get_client_kwargs())
    try:
        params = {
            "model": get_model(),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": messages,
            "tools": tools,
        }
        response = await client.messages.create(**params)
        text_parts = []
        tool_uses = []
        for block in response.content:
            if hasattr(block, "text") and block.text:
                text_parts.append(block.text)
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
        usage = getattr(response, "usage", None)
        metadata = _usage_metadata(
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
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
) -> dict:
    """
    调用 Claude API，解析 JSON 响应。

    用于意图识别、项目分析等需要结构化输出的场景。
    """
    raw = await call_llm(system, messages, max_tokens, temperature)
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
