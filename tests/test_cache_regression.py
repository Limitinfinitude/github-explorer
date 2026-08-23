"""缓存命中回归测试（key-gated，仿 DSH request-cache.e2e）。

DeepSeek prefix caching 是自动的：按公共前缀落盘，长请求第二次起命中。
守护点：任何改动导致 system/消息前缀漂移，第二轮 cache_hit_tokens 为 0，
本测试立即变红（防缓存优化回归，直接关系评测成本）。

默认跳过：设置环境变量 GE_CACHE_TEST_KEY 为真实 key 才运行。
"""
import asyncio
import os

import pytest

from src.agent import llm

pytestmark = pytest.mark.skipif(
    not os.environ.get("GE_CACHE_TEST_KEY"),
    reason="需要真实 API key（GE_CACHE_TEST_KEY）验证缓存命中",
)

_BINDING = llm.ModelBinding(
    model="deepseek-v4-flash",
    protocol="openai",
    base_url="https://opencode.ai/zen/go/v1",
    api_key=os.environ.get("GE_CACHE_TEST_KEY", ""),
    thinking_effort="off",
)

# 长前缀构造：短请求（几十 token）不达 DeepSeek 缓存落盘阈值
_LONG_DOC = "背景资料：" + ("这是一段用于构造缓存前缀的说明文本，包含大量上下文信息。" * 120)


def _call(system: str, messages: list[dict]) -> dict:
    return asyncio.run(llm.call_llm_with_tools(
        system=system, messages=messages, tools=[],
        max_tokens=50, temperature=0, binding=_BINDING,
    ))


def test_prefix_cache_hits_from_second_request():
    system = "你是缓存测试助手，保持本 system 逐字节不变。"
    first_prompt = _LONG_DOC + "\n第一次，回复 OK"
    first = _call(system, [{"role": "user", "content": first_prompt}])
    second = _call(system, [
        {"role": "user", "content": first_prompt},
        {"role": "assistant", "content": first.get("text", "")},
        {"role": "user", "content": "第二次，回复 OK"},
    ])
    usage2 = second.get("usage_metadata") or {}
    hit2 = int(usage2.get("cache_hit_tokens") or 0)
    assert hit2 > 0, (
        f"第二轮缓存未命中（hit={hit2}）——system 或消息前缀发生了漂移，"
        "会直接推高评测成本。请检查动态内容是否又进了 system prompt。"
    )


def test_system_change_breaks_cache_prefix():
    first = _call("稳定 system A", [{"role": "user", "content": _LONG_DOC + "\nOK"}])
    second = _call("稳定 system B", [
        {"role": "user", "content": _LONG_DOC + "\nOK"},
        {"role": "assistant", "content": first.get("text", "")},
        {"role": "user", "content": "OK"},
    ])
    usage2 = second.get("usage_metadata") or {}
    hit2 = int(usage2.get("cache_hit_tokens") or 0)
    # system 变化后，历史前缀全部失效（DeepSeek 按公共前缀缓存）
    assert hit2 == 0 or hit2 < 100
