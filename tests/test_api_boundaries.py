"""API 边界回归测试：无边界后门端点必须 410，只读端点保持可用。"""
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)

# 已停用的无边界执行/写操作端点
GONE_ENDPOINTS = [
    ("post", "/api/agent/execute"),
    ("post", "/api/local/run"),
    ("post", "/api/local/reset-cwd"),
    ("post", "/api/local/create-folder"),
    ("post", "/api/local/create-file"),
    ("post", "/api/local/delete"),
    ("post", "/api/local/rename"),
    ("post", "/api/local/save"),
]


def test_legacy_unbounded_execution_endpoints_are_gone():
    for method, path in GONE_ENDPOINTS:
        response = getattr(client, method)(path, json={})
        assert response.status_code == 410, (method, path, response.status_code)
        assert "410" in str(response.status_code)


def test_gone_endpoints_point_to_the_agent_path():
    response = client.post("/api/agent/execute", json={})
    assert "tasks/start" in response.json()["detail"]


def test_local_file_write_endpoints_are_gone():
    for path in [
        "/api/local/create-folder",
        "/api/local/create-file",
        "/api/local/delete",
        "/api/local/rename",
        "/api/local/save",
    ]:
        response = client.post(path, json={"path": "C:\\Windows\\System32", "name": "evil"})
        assert response.status_code == 410
        assert "Agent 工具" in response.json()["detail"]


def test_read_only_local_endpoints_stay_available():
    assert client.get("/api/local/list").status_code == 200
    assert client.get("/api/local/drives").status_code == 200
    assert client.get("/api/local/info").status_code == 200


def test_local_read_rejects_directory_and_missing_file(tmp_path):
    assert client.get("/api/local/read", params={"path": str(tmp_path)}).status_code == 200
    missing = client.get("/api/local/read", params={"path": str(tmp_path / "nope.txt")})
    assert missing.json()["error"] == "文件不存在"
