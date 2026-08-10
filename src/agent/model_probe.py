import time
from urllib.parse import urlparse

import httpx


PROBE_TIMEOUT_SECONDS = 10.0
ANTHROPIC_VERSION = "2023-06-01"


def _base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("请输入有效的 HTTP 或 HTTPS Base URL")
    return base


def _api_url(base_url: str, protocol: str, resource: str) -> str:
    base = _base_url(base_url)
    if protocol == "openai":
        if base.endswith("/chat/completions"):
            base = base.removesuffix("/chat/completions")
        return f"{base}/{resource}"
    if base.endswith("/v1"):
        return f"{base}/{resource}"
    return f"{base}/v1/{resource}"


def _headers(protocol: str, api_key: str) -> dict[str, str]:
    if protocol == "openai":
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}
    headers = {"anthropic-version": ANTHROPIC_VERSION}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _safe_error(response, api_key: str) -> str:
    message = ""
    try:
        payload = response.json()
        error = payload.get("error", payload) if isinstance(payload, dict) else payload
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("detail") or "")
        elif isinstance(error, str):
            message = error
    except Exception:
        message = ""
    if not message:
        message = f"服务返回 HTTP {response.status_code}"
    if api_key:
        message = message.replace(api_key, "***")
    return message[:300]


def _network_error(error: Exception, api_key: str) -> str:
    message = str(error)
    if api_key:
        message = message.replace(api_key, "***")
    return message[:300] or "无法连接到服务"


async def measure_latency(base_url: str) -> dict:
    try:
        url = _base_url(base_url)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            timeout=PROBE_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
        return {
            "ok": True,
            "latency_ms": _elapsed_ms(started),
            "status_code": response.status_code,
        }
    except httpx.HTTPError as error:
        return {"ok": False, "latency_ms": _elapsed_ms(started), "error": _network_error(error, "")}


async def discover_models(protocol: str, base_url: str, api_key: str) -> dict:
    try:
        url = _api_url(base_url, protocol, "models")
    except ValueError as error:
        return {"ok": False, "models": [], "error": str(error)}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.get(url, headers=_headers(protocol, api_key))
        latency = _elapsed_ms(started)
        if not 200 <= response.status_code < 300:
            return {
                "ok": False,
                "models": [],
                "latency_ms": latency,
                "status_code": response.status_code,
                "error": _safe_error(response, api_key),
            }
        payload = response.json()
        entries = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else []
        model_ids = []
        for entry in entries:
            model_id = entry.get("id") if isinstance(entry, dict) else entry
            if model_id:
                model_ids.append(str(model_id))
        return {
            "ok": True,
            "models": sorted(set(model_ids), key=str.casefold),
            "latency_ms": latency,
            "status_code": response.status_code,
        }
    except (httpx.HTTPError, ValueError) as error:
        return {
            "ok": False,
            "models": [],
            "latency_ms": _elapsed_ms(started),
            "error": _network_error(error, api_key),
        }


async def test_connection(
    protocol: str,
    base_url: str,
    api_key: str,
    model: str,
) -> dict:
    resource = "chat/completions" if protocol == "openai" else "messages"
    try:
        url = _api_url(base_url, protocol, resource)
    except ValueError as error:
        return {"ok": False, "error": str(error)}
    if protocol == "openai":
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply OK"}],
            "max_tokens": 1,
            "temperature": 0,
        }
    else:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply OK"}],
            "max_tokens": 1,
            "temperature": 0,
        }
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            response = await client.post(
                url,
                headers=_headers(protocol, api_key),
                json=payload,
            )
        latency = _elapsed_ms(started)
        if not 200 <= response.status_code < 300:
            return {
                "ok": False,
                "latency_ms": latency,
                "status_code": response.status_code,
                "error": _safe_error(response, api_key),
            }
        try:
            response_payload = response.json()
        except Exception:
            response_payload = None
        expected_field = "choices" if protocol == "openai" else "content"
        if not isinstance(response_payload, dict) or not isinstance(response_payload.get(expected_field), list):
            return {
                "ok": False,
                "latency_ms": latency,
                "status_code": response.status_code,
                "error": "服务已响应，但响应格式与所选协议不符",
            }
        return {"ok": True, "latency_ms": latency, "status_code": response.status_code}
    except httpx.HTTPError as error:
        return {
            "ok": False,
            "latency_ms": _elapsed_ms(started),
            "error": _network_error(error, api_key),
        }
