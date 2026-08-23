import asyncio
import json


class _TextBlock:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _ThinkingBlock:
    type = "thinking"

    def __init__(self, thinking: str):
        self.thinking = thinking


class _Response:
    stop_reason = "end_turn"
    usage = type("Usage", (), {"input_tokens": 12, "output_tokens": 4})()

    def __init__(self):
        self.content = [_ThinkingBlock("内部推理不应显示"), _TextBlock("你好，有什么可以帮你？")]


class _Messages:
    async def create(self, **kwargs):
        return _Response()


class _Client:
    def __init__(self, **kwargs):
        self.messages = _Messages()

    async def close(self):
        pass


def test_call_llm_with_tools_excludes_thinking_from_user_text(monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", _Client)

    result = asyncio.run(llm.call_llm_with_tools(
        system="system", messages=[{"role": "user", "content": "你好"}], tools=[]
    ))

    assert result["text"] == "你好，有什么可以帮你？"
    assert "内部推理" not in result["text"]
    assert result["usage_metadata"] == {
        "input_tokens": 12,
        "output_tokens": 4,
        "total_tokens": 16,
    }


def test_call_llm_with_tools_parses_text_encoded_tool_call(monkeypatch):
    import agent.llm as llm

    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")

    class TextToolResponse:
        stop_reason = "end_turn"
        content = [_TextBlock(
            '<tool_call>\n<function=edit_files>\n<parameter=edits>[{"path":"cat.html","operation":"write","content":"<html>"}]</parameter>\n</tool_call>'
        )]

    class TextToolMessages:
        async def create(self, **kwargs):
            return TextToolResponse()

    class TextToolClient(_Client):
        def __init__(self, **kwargs):
            self.messages = TextToolMessages()

    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", TextToolClient)

    result = asyncio.run(llm.call_llm_with_tools(
        system="system", messages=[{"role": "user", "content": "创建 cat.html"}], tools=[]
    ))

    assert result["tool_uses"][0]["name"] == "edit_files"
    assert result["tool_uses"][0]["input"]["edits"][0]["path"] == "cat.html"
    assert "<tool_call>" not in result["text"]


def test_call_llm_with_tools_parses_dsml_tool_call(monkeypatch):
    import agent.llm as llm
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")

    class DsmlResponse:
        stop_reason = "end_turn"
        content = [_TextBlock(
            '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="edit_files">\n'
            '<｜｜DSML｜｜parameter name="edits" string="false">'
            '[{"path":"app.py","operation":"write","content":"print(1)"}]'
            '</｜｜DSML｜｜parameter>\n</｜｜DSML｜｜invoke>\n</｜｜DSML｜｜tool_calls>'
        )]

    class DsmlMessages:
        async def create(self, **kwargs):
            return DsmlResponse()

    class DsmlClient(_Client):
        def __init__(self, **kwargs):
            self.messages = DsmlMessages()

    monkeypatch.setattr(llm.anthropic, "AsyncAnthropic", DsmlClient)
    result = asyncio.run(llm.call_llm_with_tools(
        system="system", messages=[{"role": "user", "content": "fix app.py"}], tools=[]
    ))
    assert result["stop_reason"] == "tool_use"
    assert result["tool_uses"][0]["name"] == "edit_files"
    assert result["tool_uses"][0]["input"]["edits"][0]["path"] == "app.py"
    assert "DSML" not in result["text"]


def test_openai_compatible_tool_call_is_normalized(monkeypatch):
    import agent.llm as llm

    captured = {}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": "I will inspect it.",
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": json.dumps({"path": "README.md"}),
                            },
                        }],
                    },
                }],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6, "total_tokens": 26},
            }

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return Response()

    monkeypatch.setenv("LLM_PROTOCOL", "openai")
    monkeypatch.setenv("LLM_MODEL", "vendor-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setattr(llm.httpx, "AsyncClient", Client)

    result = asyncio.run(llm.call_llm_with_tools(
        system="system",
        messages=[
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "old-call", "name": "list_directory", "input": {"path": "."},
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "old-call", "content": "README.md",
            }]},
        ],
        tools=[{
            "name": "read_file",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }],
    ))

    assert captured["url"] == "https://gateway.example/v1/chat/completions"
    assert captured["request"]["headers"]["Authorization"] == "Bearer test-key"
    assert captured["request"]["json"]["model"] == "vendor-model"
    assert captured["request"]["json"]["tools"][0]["function"]["parameters"]["type"] == "object"
    assert captured["request"]["json"]["messages"][-1] == {
        "role": "tool", "tool_call_id": "old-call", "content": "README.md",
    }
    assert result == {
        "stop_reason": "tool_use",
        "text": "I will inspect it.",
        "tool_uses": [{"id": "call-1", "name": "read_file", "input": {"path": "README.md"}}],
        "usage_metadata": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
    }
