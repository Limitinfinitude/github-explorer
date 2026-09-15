"""会话分享渲染测试：结构完整性、HTML 转义（防注入）、diff 解析与行号、时间线交错顺序。"""

from src.share_render import (
    build_view,
    parse_unified_diff,
    render_markdown_subset,
    render_share_html,
)


def _user(content: str) -> dict:
    return {"role": "user", "content": content, "steps": [], "thinking": [],
            "narrations": [], "cmdBlocks": [], "timeline": [], "agentRun": {}, "workElapsed": 0}


def _assistant(**kw) -> dict:
    base = {"role": "assistant", "content": "", "steps": [], "thinking": [],
            "narrations": [], "cmdBlocks": [], "timeline": [], "agentRun": {}, "workElapsed": 0}
    base.update(kw)
    return base


def test_render_basic_structure():
    html = render_share_html([
        _user("帮我改一个文件"),
        _assistant(content="## 完成\n已修改 demo.py", workElapsed=95),
    ], "测试会话")
    assert "GitHub Explorer · 会话分享" in html
    assert "测试会话" in html
    assert "帮我改一个文件" in html
    assert "已工作" in html or "1 分 35 秒" in html


def test_html_escape_prevents_injection():
    html = render_share_html([
        _user('<script>alert(1)</script><img src=x onerror=alert(2)>'),
        _assistant(
            content="正文 <script>bad()</script>",
            thinking=[{"content": '<script>from-think()</script>', "round": 0}],
            timeline=[{"kind": "think", "round": 0}],
        ),
    ], "注入测试")
    assert "<script>alert" not in html
    assert "<script>bad" not in html
    assert "<script>from-think" not in html
    assert "onerror=alert" not in html.replace("&", "&amp;") or "&lt;img" in html
    assert "&lt;script&gt;" in html


def test_parse_unified_diff_lines_and_numbers():
    diff = (
        "--- a/demo.py\n+++ b/demo.py\n"
        "@@ -1,3 +1,3 @@\n alpha\n-beta\n+GAMMA\n delta\n"
    )
    files = parse_unified_diff(diff)
    assert len(files) == 1
    assert files[0]["path"] == "demo.py"
    lines = files[0]["hunks"][0]["lines"]
    kinds = [l["kind"] for l in lines]
    assert kinds == ["ctx", "del", "add", "ctx"]
    assert lines[1]["old"] == "2" and lines[1]["new"] == ""
    assert lines[2]["new"] == "2" and lines[2]["old"] == ""
    assert lines[3]["old"] == "3" and lines[3]["new"] == "3"


def test_timeline_order_preserved():
    thinking = [{"content": "第一段思考", "round": 0}, {"content": "第二段思考", "round": 1}]
    steps = [
        {"callId": "c1", "toolName": "read_file", "args": {"path": "a.py"}, "done": True, "status": "succeeded"},
        {"callId": "c2", "toolName": "edit_files", "args": {"edits": [{"path": "a.py", "operation": "write", "content": "x"}]}, "done": True, "status": "succeeded"},
    ]
    timeline = [
        {"kind": "think", "round": 0},
        {"kind": "tool", "callId": "c1"},
        {"kind": "note", "text": "正在修改"},
        {"kind": "think", "round": 1},
        {"kind": "tool", "callId": "c2"},
    ]
    view = build_view([_assistant(thinking=thinking, steps=steps, timeline=timeline)], "t")
    kinds = [e["kind"] for e in view["messages"][0]["entries"]]
    assert kinds == ["think", "tool", "note", "think", "tool"]
    assert view["messages"][0]["tool_count"] == 2
    assert view["messages"][0]["think_count"] == 2


def test_tool_diff_from_data_and_args_fallback():
    step = {
        "callId": "c1", "toolName": "edit_files", "done": True, "status": "succeeded",
        "args": {"edits": [{"path": "b.py", "operation": "replace", "search": "old", "content": "new"}]},
        "data": {"diff": "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+new\n"},
    }
    view = build_view([_assistant(steps=[step], timeline=[{"kind": "tool", "callId": "c1"}])], "t")
    entry = view["messages"][0]["entries"][0]["step"]
    assert entry["diff_add"] == 1 and entry["diff_del"] == 1
    assert entry["diff_files"][0]["path"] == "b.py"

    # data 缺失 → args 回退（write=全绿）
    step2 = {**step, "data": {}}
    view2 = build_view([_assistant(steps=[step2], timeline=[{"kind": "tool", "callId": "c1"}])], "t")
    entry2 = view2["messages"][0]["entries"][0]["step"]
    assert entry2["diff_del"] >= 1  # replace 的 search 红块
    assert entry2["diff_add"] >= 1


def test_markdown_subset_escapes_before_tags():
    body = render_markdown_subset("## 标题\n- 列表项\n`code`\n**粗体**\n<div>")
    assert "<h3>标题</h3>" in body
    assert "&lt;div&gt;" in body
    assert "<strong>粗体</strong>" in body
    assert "<code>code</code>" in body
