"""探索功能升级的后端逻辑测试：trending 详情补全（enrichment 只在 Python 且前 N 条）。"""
import asyncio
from types import SimpleNamespace

from routes_search import _enrich_trending_repos


class _FakeClient:
    """假 GitHubClient：按 owner/repo 返回预设详情，模拟 /repos 调用。"""

    def __init__(self, details: dict):
        self._details = details
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get_repo_details(self, owner: str, repo: str):
        self.calls.append(f"{owner}/{repo}")
        data = self._details.get(f"{owner}/{repo}")
        if data is None:
            raise RuntimeError("not found")
        return data


def _fake_detail():
    return {
        "topics": ["llm", "agent"],
        "owner": {"avatar_url": "https://avatars.example/u.png"},
        "open_issues_count": 7,
        "pushed_at": "2026-08-30T10:00:00Z",
        "license": {"spdx_id": "MIT"},
    }


def test_enrich_only_python_and_first_limit(monkeypatch):
    repos = [
        {"full_name": "a/py1", "language": "Python"},
        {"full_name": "a/py2", "language": "Python"},
        {"full_name": "a/js1", "language": "TypeScript"},  # 非 Python：跳过
    ] + [{"full_name": f"a/py{i}", "language": "Python"} for i in range(3, 12)]

    fake = _FakeClient({"a/py1": _fake_detail()})
    import api.github_client as gh
    monkeypatch.setattr(gh, "GitHubClient", lambda: fake)

    asyncio.run(_enrich_trending_repos(repos, limit=8))

    # 前 8 条里 Python 的才有详情调用；非 Python 跳过；详情失败的不阻断
    assert fake.calls[0] == "a/py1"
    assert all("js1" not in c for c in fake.calls)
    enriched = repos[0]
    assert enriched["topics"] == ["llm", "agent"]
    assert enriched["owner_avatar"] == "https://avatars.example/u.png"
    assert enriched["open_issues"] == 7
    assert enriched["license"] == "MIT"
    assert repos[2].get("topics") is None  # 非 Python 未补全


def test_enrich_detail_failure_keeps_original(monkeypatch):
    repos = [{"full_name": "missing/repo", "language": "Python", "stars": 100}]
    fake = _FakeClient({})  # 没有任何详情 → 全部 404

    import api.github_client as gh
    monkeypatch.setattr(gh, "GitHubClient", lambda: fake)

    asyncio.run(_enrich_trending_repos(repos, limit=8))
    assert repos[0]["stars"] == 100
    assert repos[0].get("topics") is None  # 失败保留原字段，不阻断
