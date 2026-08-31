import os

from fastapi.testclient import TestClient
import httpx

import main
import model_config


def _custom_model():
    return {
        "id": "custom-test",
        "name": "Local Test",
        "model": "provider-model-v1",
        "protocol": "openai",
        "icon": "L",
        "color": "#238636",
        "tags": ["Custom", "OpenAI"],
        "api_key": "secret-token-1234",
        "base_url": "http://127.0.0.1:1234/v1",
    }


def test_custom_model_persists_and_reloads(monkeypatch, tmp_path):
    path = tmp_path / "model_configs.json"
    monkeypatch.setattr(model_config, "_MODEL_CONFIGS_PATH", path)

    custom = _custom_model()
    model_config._save_model_configs({custom["id"]: custom})
    loaded = model_config._load_model_configs()

    # 加载时补齐默认思考程度，旧配置文件没有该字段
    assert loaded[custom["id"]] == {**custom, "thinking_effort": "off", "context_window": "128k"}


def test_unregistered_environment_model_is_loaded_with_its_credentials(monkeypatch, tmp_path):
    monkeypatch.setattr(model_config, "_MODEL_CONFIGS_PATH", tmp_path / "missing-model-configs.json")
    monkeypatch.delenv("MODEL_CONFIG_ID", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_PROTOCOL", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL", "environment-only-model")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example/anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret")

    loaded = model_config._load_model_configs()

    environment_model = next(
        config for config in loaded.values()
        if config["model"] == "environment-only-model"
    )
    assert environment_model["protocol"] == "anthropic"
    assert environment_model["base_url"] == "https://gateway.example/anthropic"
    assert environment_model["api_key"] == "environment-secret"


def test_environment_model_credentials_are_not_persisted(monkeypatch, tmp_path):
    path = tmp_path / "model_configs.json"
    monkeypatch.setattr(model_config, "_MODEL_CONFIGS_PATH", path)
    custom = _custom_model()
    environment_model = {
        **custom,
        "id": "environment-model",
        "source": "environment",
        "api_key": "environment-secret",
    }

    model_config._save_model_configs({
        custom["id"]: custom,
        environment_model["id"]: environment_model,
    })

    saved = path.read_text(encoding="utf-8")
    assert custom["id"] in saved
    assert "environment-model" not in saved
    assert "environment-secret" not in saved


def test_settings_masks_custom_model_key(monkeypatch):
    custom = _custom_model()
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {custom["id"]: custom})

    response = TestClient(main.app).get("/api/settings")

    assert response.status_code == 200
    model = response.json()["models"][0]
    assert model["api_key_masked"] == "secr*********1234"
    assert "api_key" not in model
    assert model["model"] == "provider-model-v1"
    assert model["protocol"] == "openai"


def test_create_custom_model_returns_public_config(monkeypatch, tmp_path):
    monkeypatch.setattr(model_config, "_MODEL_CONFIGS_PATH", tmp_path / "model_configs.json")
    monkeypatch.setattr(model_config, "_ACTIVE_MODEL_PATH", tmp_path / "active_model.json")
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {})
    client = TestClient(main.app)

    response = client.post("/api/settings/models", json={
        "name": "My Gateway",
        "model": "vendor-chat-large",
        "protocol": "openai",
        "base_url": "https://gateway.example/v1",
        "api_key": "example-secret",
    })

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["model"]["name"] == "My Gateway"
    assert body["model"]["api_key_masked"] == "exam******cret"
    assert body["model"]["id"] in model_config.MODEL_CONFIGS
    assert "example-secret" not in response.text
    assert model_config.get_active_model_id() == body["model"]["id"]
    assert model_config._load_active_model_id() == body["model"]["id"]


def test_update_custom_model_edits_provider_fields_and_preserves_blank_key(monkeypatch, tmp_path):
    custom = _custom_model()
    monkeypatch.setattr(model_config, "_MODEL_CONFIGS_PATH", tmp_path / "model_configs.json")
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {custom["id"]: custom})

    response = TestClient(main.app).post(f"/api/settings/models/{custom['id']}", json={
        "name": "Updated Gateway",
        "model": "provider-model-v2",
        "protocol": "anthropic",
        "base_url": "https://gateway.example/anthropic",
        "api_key": "",
    })

    assert response.status_code == 200
    assert response.json()["model"]["name"] == "Updated Gateway"
    assert model_config.MODEL_CONFIGS[custom["id"]]["model"] == "provider-model-v2"
    assert model_config.MODEL_CONFIGS[custom["id"]]["protocol"] == "anthropic"
    assert model_config.MODEL_CONFIGS[custom["id"]]["api_key"] == "secret-token-1234"


def test_update_builtin_model_is_supported(monkeypatch, tmp_path):
    builtin = dict(model_config.DEFAULT_MODEL_CONFIGS[0])
    monkeypatch.setattr(model_config, "_MODEL_CONFIGS_PATH", tmp_path / "model_configs.json")
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {builtin["id"]: builtin})

    response = TestClient(main.app).post(f"/api/settings/models/{builtin['id']}", json={
        "name": "Changed",
        "model": "changed",
        "protocol": "openai",
    })

    assert response.status_code == 200
    assert model_config.MODEL_CONFIGS[builtin["id"]]["name"] == "Changed"


def test_select_custom_model_applies_provider_configuration(monkeypatch):
    custom = _custom_model()
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {custom["id"]: custom})

    assert model_config.apply_model(custom["id"]) is True
    assert os.environ["LLM_MODEL"] == "provider-model-v1"
    assert os.environ["LLM_PROTOCOL"] == "openai"
    assert os.environ["LLM_API_KEY"] == "secret-token-1234"
    assert os.environ["LLM_BASE_URL"] == "http://127.0.0.1:1234/v1"


def test_select_endpoint_persists_active_model_for_restart(monkeypatch, tmp_path):
    custom = _custom_model()
    selection_path = tmp_path / "active_model.json"
    monkeypatch.setattr(model_config, "_ACTIVE_MODEL_PATH", selection_path)
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {custom["id"]: custom})

    response = TestClient(main.app).post("/api/settings/select", json={
        "model_id": custom["id"],
    })

    assert response.status_code == 200
    assert response.json() == {"ok": True, "active_model": custom["id"]}
    assert model_config._load_active_model_id() == custom["id"]


class _ProbeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data or {}
        self.text = ""

    def json(self):
        return self._data


class _ProbeClient:
    response = _ProbeResponse()
    requests = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.response

    async def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self.response


def _install_probe_client(monkeypatch, response):
    _ProbeClient.response = response
    _ProbeClient.requests = []
    monkeypatch.setattr(httpx, "AsyncClient", _ProbeClient)


def test_latency_probe_never_sends_key_and_accepts_http_error(monkeypatch):
    _install_probe_client(monkeypatch, _ProbeResponse(401))

    response = TestClient(main.app).post("/api/settings/models/latency", json={
        "base_url": "https://gateway.example/v1",
    })

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status_code"] == 401
    assert response.json()["latency_ms"] >= 0
    method, url, kwargs = _ProbeClient.requests[0]
    assert (method, url) == ("GET", "https://gateway.example/v1")
    assert "headers" not in kwargs


def test_openai_model_discovery_uses_bearer_and_models_endpoint(monkeypatch):
    _install_probe_client(monkeypatch, _ProbeResponse(data={
        "data": [{"id": "z-model"}, {"id": "a-model"}],
    }))

    response = TestClient(main.app).post("/api/settings/models/discover", json={
        "protocol": "openai",
        "base_url": "https://gateway.example/v1/",
        "api_key": "openai-secret",
    })

    assert response.json()["models"] == ["a-model", "z-model"]
    method, url, kwargs = _ProbeClient.requests[0]
    assert (method, url) == ("GET", "https://gateway.example/v1/models")
    assert kwargs["headers"] == {"Authorization": "Bearer openai-secret"}
    assert "openai-secret" not in response.text


def test_model_discovery_can_reuse_saved_key_while_editing(monkeypatch):
    custom = _custom_model()
    monkeypatch.setattr(model_config, "MODEL_CONFIGS", {custom["id"]: custom})
    captured = {}

    async def fake_discover(protocol, base_url, api_key):
        captured.update(protocol=protocol, base_url=base_url, api_key=api_key)
        return {"ok": True, "models": ["provider-model-v1"]}

    monkeypatch.setattr("agent.model_probe.discover_models", fake_discover)
    response = TestClient(main.app).post("/api/settings/models/discover", json={
        "protocol": "openai",
        "base_url": "http://127.0.0.1:1234/v1",
        "api_key": "",
        "model_config_id": custom["id"],
    })

    assert response.status_code == 200
    assert captured["api_key"] == "secret-token-1234"


def test_anthropic_model_discovery_uses_native_headers(monkeypatch):
    _install_probe_client(monkeypatch, _ProbeResponse(data={
        "data": [{"id": "claude-sonnet"}],
    }))

    response = TestClient(main.app).post("/api/settings/models/discover", json={
        "protocol": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "anthropic-secret",
    })

    assert response.json()["models"] == ["claude-sonnet"]
    method, url, kwargs = _ProbeClient.requests[0]
    assert (method, url) == ("GET", "https://api.anthropic.com/v1/models")
    assert kwargs["headers"]["x-api-key"] == "anthropic-secret"
    assert kwargs["headers"]["anthropic-version"] == "2023-06-01"


def test_openai_connection_test_calls_chat_not_model_list(monkeypatch):
    _install_probe_client(monkeypatch, _ProbeResponse(data={
        "choices": [{"message": {"content": "OK"}}],
    }))

    response = TestClient(main.app).post("/api/settings/models/test-connection", json={
        "protocol": "openai",
        "base_url": "https://gateway.example/v1",
        "api_key": "connection-secret",
        "model": "chat-model",
    })

    assert response.json()["ok"] is True
    method, url, kwargs = _ProbeClient.requests[0]
    assert (method, url) == ("POST", "https://gateway.example/v1/chat/completions")
    assert kwargs["json"]["model"] == "chat-model"
    assert kwargs["json"]["max_tokens"] == 1
    assert "/models" not in url
    assert "connection-secret" not in response.text


def test_anthropic_connection_test_calls_messages(monkeypatch):
    _install_probe_client(monkeypatch, _ProbeResponse(data={
        "content": [{"type": "text", "text": "OK"}],
    }))

    response = TestClient(main.app).post("/api/settings/models/test-connection", json={
        "protocol": "anthropic",
        "base_url": "https://proxy.example/anthropic",
        "api_key": "connection-secret",
        "model": "claude-model",
    })

    assert response.json()["ok"] is True
    method, url, kwargs = _ProbeClient.requests[0]
    assert (method, url) == ("POST", "https://proxy.example/anthropic/v1/messages")
    assert kwargs["headers"]["x-api-key"] == "connection-secret"
    assert kwargs["json"]["model"] == "claude-model"
    assert kwargs["json"]["max_tokens"] == 1


def test_connection_test_rejects_unrelated_success_response(monkeypatch):
    _install_probe_client(monkeypatch, _ProbeResponse(status_code=200, data={}))

    response = TestClient(main.app).post("/api/settings/models/test-connection", json={
        "protocol": "openai",
        "base_url": "https://ordinary-site.example/v1",
        "api_key": "connection-secret",
        "model": "chat-model",
    })

    assert response.json()["ok"] is False
    assert response.json()["status_code"] == 200
    assert "响应格式" in response.json()["error"]
