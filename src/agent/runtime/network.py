import html as html_module
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

from .models import ToolResult


_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}

# 无 key 搜索：DuckDuckGo Lite（匿名 HTML，主）+ Bing RSS（回退），零依赖解析
_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/?q={query}"
_BING_RSS_URL = "https://www.bing.com/search?q={query}&format=rss&count={limit}"
_DDG_LINK_RE = re.compile(r"<a[^>]+href=\"([^\"]+)\"[^>]*class=[\"']result-link[\"'][^>]*>(.*?)</a>|<a[^>]+class=[\"']result-link[\"'][^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.S)
_DDG_SNIPPET_RE = re.compile(r"class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BING_RSS_NS = "{http://bing.com/search}"


def _strip_tags(value: str) -> str:
    return html_module.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _clean_ddg_url(href: str) -> str:
    """DDG 结果链接是 //duckduckgo.com/l/?uddg=<encoded> 跳转，解出真实 URL。"""
    if "uddg=" in href:
        parsed = urllib.parse.urlparse(href if href.startswith("http") else f"https:{href}")
        for key, value in urllib.parse.parse_qsl(parsed.query):
            if key == "uddg":
                return unquote(value)
    if href.startswith("//"):
        href = f"https:{href}"
    return href


class NetworkTools:
    def __init__(self, processes=None) -> None:
        self.processes = processes

    def check_port(self, host: str, port: int, timeout: float = 1) -> ToolResult:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return ToolResult.ok(data={"host": host, "port": port, "open": True}, output="端口已开放")
        except OSError as exc:
            return ToolResult.fail(
                f"端口未开放: {host}:{port}",
                data={"host": host, "port": port, "open": False, "detail": str(exc)},
            )

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
        timeout: float = 15,
    ) -> tuple[int, str, dict[str, str]]:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError(
                "HTTP 请求只允许本机回环地址（http_request 用于验收本机启动的服务）；"
                "访问外网/第三方 API 请改用 web_fetch 工具"
            )
        body = None if json_body is None else json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers = dict(headers or {})
        if body is not None:
            request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (
                    response.status,
                    response.read(1_000_000).decode("utf-8", errors="replace"),
                    dict(response.headers.items()),
                )
        except urllib.error.HTTPError as exc:
            return (
                exc.code,
                exc.read(1_000_000).decode("utf-8", errors="replace"),
                dict(exc.headers.items()),
            )

    def search(self, query: str, limit: int = 8) -> ToolResult:
        """无 key 网页搜索：Bing RSS（主，国内可达）+ DuckDuckGo Lite 匿名 HTML（回退）。

        零依赖（urllib + 正则），返回 [{title, url, snippet}]，供 web_search 工具使用。
        两个引擎都无官方 key；顺序按部署网络可达性选择（DDG 在国内被墙故作回退）。
        """
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 8), 15))
        if not query:
            return ToolResult.fail("搜索词不能为空", error_kind="invalid_input")
        encoded = urllib.parse.quote_plus(query)
        try:
            return self._search_bing_rss(encoded, query, limit, "")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            try:
                return self._search_ddg(encoded, query, limit, f"Bing 不可用（{exc}），已回退 DuckDuckGo")
            except (urllib.error.URLError, OSError, ValueError) as exc2:
                return ToolResult.fail(f"搜索失败：{exc2}", data={"query": query})

    def _search_bing_rss(self, encoded: str, query: str, limit: int, note: str) -> ToolResult:
        request = urllib.request.Request(
            _BING_RSS_URL.format(query=encoded, limit=limit),
            headers={"User-Agent": "github-explorer-agent/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = response.read(300_000)
        root = ET.fromstring(payload)
        # Bing RSS 实测无命名空间（裸 <item>），但按 local-name 匹配以兼容带 ns 的变体
        def local(el) -> str:
            return el.tag.rsplit("}", 1)[-1]

        results = []
        for item in (el for el in root.iter() if local(el) == "item"):
            def text_of(tag: str) -> str:
                found = next((el for el in item if local(el) == tag), None)
                return _strip_tags(found.text or "") if found is not None else ""
            title = text_of("title")
            link = text_of("link")
            snippet = text_of("description")
            if title and link:
                results.append({"title": title, "url": link, "snippet": snippet[:200]})
            if len(results) >= limit:
                break
        if not results:
            raise ValueError("Bing RSS 未解析到结果")
        return ToolResult.ok(
            data={"query": query, "engine": "bing-rss", "results": results, "note": note},
            output=(note + "\n" if note else "") + "\n".join(
                f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:160]}" for i, r in enumerate(results)
            ),
        )

    def _search_ddg(self, encoded: str, query: str, limit: int, note: str) -> ToolResult:
        request = urllib.request.Request(
            _DDG_LITE_URL.format(query=encoded),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) github-explorer-agent"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            page = response.read(300_000).decode("utf-8", errors="replace")
        links = []
        for match in _DDG_LINK_RE.finditer(page):
            href = match.group(1) or match.group(3) or ""
            title = _strip_tags(match.group(2) or match.group(4) or "")
            url = _clean_ddg_url(href)
            if not url or not title:
                continue
            links.append((title, url))
        snippets = [_strip_tags(s) for s in _DDG_SNIPPET_RE.findall(page)]
        results = []
        for index, (title, url) in enumerate(links[:limit]):
            results.append({
                "title": title,
                "url": url,
                "snippet": snippets[index] if index < len(snippets) else "",
            })
        if not results:
            raise ValueError("DDG 未解析到结果")
        return ToolResult.ok(
            data={"query": query, "engine": "duckduckgo", "results": results, "note": note},
            output=(note + "\n" if note else "") + "\n".join(
                f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet'][:160]}" for i, r in enumerate(results)
            ),
        )

    def web_fetch(self, url: str, timeout: float = 20) -> ToolResult:
        """只读抓取外网页面/API（GET），用于获取仓库信息、文档等。

        与 request() 的区别：request 限本机回环（评测验收用），web_fetch 允许外网
        但只读 GET、限响应大小，不携带本机凭据、不访问内网地址。
        """
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ToolResult.fail(f"web_fetch 只支持 http/https URL: {url}")
        hostname = parsed.hostname.casefold()
        if hostname in _LOOPBACK_HOSTS or hostname.endswith(".local"):
            return ToolResult.fail(f"web_fetch 不允许本机/内网地址: {url}")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "github-explorer-agent/1.0"}, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(500_000).decode("utf-8", errors="replace")
                return ToolResult.ok(
                    data={
                        "url": url,
                        "status": response.status,
                        "content_type": response.headers.get("Content-Type", ""),
                        "bytes": len(body.encode("utf-8")),
                    },
                    output=body[:200_000],
                )
        except urllib.error.HTTPError as exc:
            body = exc.read(100_000).decode("utf-8", errors="replace")
            return ToolResult.fail(
                f"HTTP {exc.code}: {exc.reason}",
                data={"url": url, "status": exc.code, "body": body[:20_000]},
            )
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return ToolResult.fail(f"网络请求失败: {exc}", data={"url": url})

    def wait_http(
        self,
        url: str,
        timeout: float = 15,
        expected_text: str | None = None,
        *,
        session_id: str | None = None,
        process_id: str | None = None,
    ) -> ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ToolResult.fail("只支持有效的 http/https URL")
        if parsed.hostname not in _LOOPBACK_HOSTS:
            return ToolResult.fail(
                f"HTTP 验收只允许本机回环地址: {parsed.hostname}",
                error_kind="invalid_host",
            )

        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                status, body = self._request(url, min(2, timeout))
                if expected_text and expected_text not in body:
                    last_error = f"响应中未出现期望文本: {expected_text}"
                    time.sleep(0.1)
                    continue
                ownership = None
                if process_id:
                    if self.processes is None or session_id is None:
                        return ToolResult.fail(
                            "无法核验 HTTP 服务的进程归属",
                            error_kind="process_ownership_unavailable",
                        )
                    ownership = self.processes.listener_ownership(
                        session_id,
                        process_id,
                        parsed.hostname,
                        parsed.port or (443 if parsed.scheme == "https" else 80),
                    )
                    if not ownership.get("owned"):
                        return ToolResult.fail(
                            "HTTP 已响应，但监听端口不属于指定受管进程",
                            error_kind="process_mismatch",
                            data={"url": url, "status": status, **ownership},
                        )
                return ToolResult.ok(
                    data={
                        "url": url,
                        "status": status,
                        "expected_text": expected_text,
                        "process_id": process_id,
                        **(ownership or {}),
                    },
                    output=f"HTTP 服务已就绪: {status}",
                )
            except urllib.error.HTTPError as exc:
                if exc.code < 500:
                    return ToolResult.ok(
                        data={"url": url, "status": exc.code},
                        output=f"HTTP 服务已响应: {exc.code}",
                    )
                last_error = str(exc)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = str(exc)
            time.sleep(0.1)
        return ToolResult.fail(
            f"等待 HTTP 服务超时: {url}（{last_error}）",
            data={"detail": last_error},
        )

    @staticmethod
    def _request(url: str, timeout: float) -> tuple[int, str]:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read(1_000_000).decode("utf-8", errors="replace")
