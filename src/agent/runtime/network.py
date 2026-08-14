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
