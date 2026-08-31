"""运行时小组件测试：命令中文编码、web_search 解析（均为零外网依赖的确定性测试）。"""
import sys
import urllib.request
from pathlib import Path

import pytest

from agent.runtime.commands import CommandRunner, plan_shell_command
from agent.runtime.network import NetworkTools, _clean_ddg_url
from agent.runtime.workspace import WorkspaceManager


# ---------- 命令中文编码（PowerShell GBK → UTF-8 修复） ----------

def test_powershell_chinese_output_is_utf8(tmp_path: Path):
    if sys.platform != "win32":
        pytest.skip("Windows 专有编码链路")
    root = tmp_path / "project"
    root.mkdir()
    manager = WorkspaceManager()
    manager.bind("session", root)
    runner = CommandRunner(manager)

    result = runner.run("session", "Write-Output '主机名测试：中文OK-磁盘剩余'")

    assert result.success is True
    assert "主机名测试：中文OK-磁盘剩余" in result.output
    assert "?" * 4 not in result.output


def test_plan_wraps_powershell_with_utf8_preamble():
    if sys.platform != "win32":
        pytest.skip("Windows 专有编码链路")
    plan = plan_shell_command("Write-Output '中文'")
    assert "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8" in plan.args[-1]


def test_plan_wraps_cmd_with_chcp():
    if sys.platform != "win32":
        pytest.skip("Windows 专有编码链路")
    # && 链接触发 cmd 分支：args 里应带 chcp 65001 前缀
    plan = plan_shell_command("echo one && echo two")
    assert plan.shell == "cmd"
    assert "chcp 65001" in plan.args[-1]


# ---------- web_search 解析（mock urlopen，不打外网） ----------

BING_RSS = (
    '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
    "<title>必应：测试</title>"
    "<item><title>结果一 &amp; 说明</title><link>https://example.com/one</link>"
    "<description>第一个 &lt;b&gt;结果&lt;/b&gt; 摘要</description></item>"
    "<item><title>结果二</title><link>https://example.com/two</link>"
    "<description>第二个摘要</description></item>"
    "</channel></rss>"
)

DDG_HTML = (
    "<html><body><table>"
    "<tr><td>1.</td><td><a rel=\"nofollow\" class='result-link' href=\"//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa\">结果 A</a></td></tr>"
    "<tr><td class='result-snippet'>A 的摘要</td></tr>"
    "<tr><td>2.</td><td><a class=\"result-link\" href=\"https://example.org/b\">结果 B</a></td></tr>"
    "<tr><td class='result-snippet'>B 的摘要</td></tr>"
    "</table></body></html>"
)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, amount=-1):
        return self._body


def test_search_parses_bing_rss(monkeypatch):
    nt = NetworkTools()
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=8: _FakeResponse(BING_RSS.encode("utf-8")))
    result = nt.search("测试查询", 5)
    assert result.success is True
    assert (result.data or {})["engine"] == "bing-rss"
    results = result.data["results"]
    assert [r["title"] for r in results] == ["结果一 & 说明", "结果二"]
    assert results[0]["url"] == "https://example.com/one"
    assert "<b>" not in results[0]["snippet"]


def test_search_falls_back_to_ddg(monkeypatch):
    nt = NetworkTools()

    def fake_urlopen(req, timeout=8):
        url = req.fullurl if hasattr(req, "fullurl") else str(req)
        if "bing.com" in url:
            raise urllib.error.URLError("blocked")
        return _FakeResponse(DDG_HTML.encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = nt.search("测试", 5)
    assert result.success is True
    assert (result.data or {})["engine"] == "duckduckgo"
    results = result.data["results"]
    # uddg 跳转链接解出真实 URL
    assert results[0]["url"] == "https://example.org/a"
    assert results[0]["snippet"] == "A 的摘要"


def test_clean_ddg_url_passthrough():
    assert _clean_ddg_url("https://example.org/x") == "https://example.org/x"
    assert _clean_ddg_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa&rut=x") == "https://example.org/a"
