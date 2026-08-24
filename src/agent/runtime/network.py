import json
import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .models import ToolResult


_LOOPBACK_HOSTS = {"127.0.0.1", "::1"}


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
