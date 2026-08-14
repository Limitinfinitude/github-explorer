import socket
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from .models import ToolResult


class NetworkTools:
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
    ) -> ToolResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ToolResult.fail("只支持有效的 http/https URL")

        deadline = time.monotonic() + timeout
        last_error = ""
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=min(2, timeout)) as response:
                    status = response.status
                    body = response.read(1_000_000).decode("utf-8", errors="replace")
                if expected_text and expected_text not in body:
                    last_error = f"响应中未出现期望文本: {expected_text}"
                    time.sleep(0.1)
                    continue
                return ToolResult.ok(
                    data={
                        "url": url,
                        "status": status,
                        "expected_text": expected_text,
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
