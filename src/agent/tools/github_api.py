"""
GitHub API 工具 — 仓库信息、搜索、克隆、文件树、Issue、分支、PR 等
"""
import os
import subprocess
from pathlib import Path
from typing import Optional

import httpx
import base64


CLONE_DIR = Path("./cloned_repos")


def fetch_repo_info(repo: str) -> dict:
    """
    通过 GitHub API 获取仓库详情。
    """
    try:
        url = f"https://api.github.com/repos/{repo}"
        response = httpx.get(url, headers={"Accept": "application/vnd.github.v3+json"})
        if response.status_code == 200:
            data = response.json()
            return {
                "full_name": data["full_name"],
                "description": data.get("description", ""),
                "language": data.get("language"),
                "stars": data.get("stargazers_count", 0),
                "forks": data.get("forks_count", 0),
                "topics": data.get("topics", []),
                "license": (data.get("license") or {}).get("name"),
                "homepage": data.get("homepage"),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "pushed_at": data.get("pushed_at"),
                "open_issues": data.get("open_issues_count", 0),
                "archived": data.get("archived", False),
            }
        return {"error": f"GitHub API 返回 {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}


def search_github(query: str, language: str = "", limit: int = 20,
                   sort: str = "stars", min_stars: int = 0,
                   created_after: str = "") -> dict:
    """
    搜索 GitHub 仓库。

    Args:
        query: 搜索关键词
        language: 编程语言筛选
        limit: 返回数量
        sort: 排序方式 (stars/updated/forks)
        min_stars: 最低 stars 数
        created_after: 创建时间筛选 (如 "2024-01-01")
    """
    try:
        q = query.strip()
        if min_stars > 0:
            q += f" stars:>={min_stars}"
        if language:
            q += f" language:{language}"
        if created_after:
            q += f" created:>{created_after}"

        response = httpx.get(
            "https://api.github.com/search/repositories",
            params={"q": q, "sort": sort, "order": "desc", "per_page": limit},
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=30,
        )

        if response.status_code == 200:
            items = response.json().get("items", [])
            repos = [
                {
                    "full_name": r["full_name"],
                    "description": r.get("description", ""),
                    "stars": r["stargazers_count"],
                    "forks": r["forks_count"],
                    "language": r.get("language"),
                    "topics": r.get("topics", []),
                    "pushed_at": r.get("pushed_at", ""),
                    "html_url": r["html_url"],
                }
                for r in items
            ]
            return {"success": True, "repos": repos}
        return {"success": False, "error": f"API 返回 {response.status_code}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def clone_repo(repo: str, target_dir: str = None) -> dict:
    """
    克隆 GitHub 仓库。使用 args list 而非字符串拼接，防止命令注入。
    """
    if target_dir is None:
        CLONE_DIR.mkdir(parents=True, exist_ok=True)
        target_dir = str(CLONE_DIR / repo.split("/")[-1])

    clone_url = f"https://github.com/{repo}.git"
    clean_env = {k: v for k, v in os.environ.items()
                 if not k.startswith("CONDA") and k != "SSL_CERT_FILE"}
    try:
        result = subprocess.run(
            ["git", "clone", clone_url, target_dir],
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
            env=clean_env,
        )
        output = result.stdout or result.stderr or ""
        success = result.returncode == 0
        return {"success": success, "output": output.strip(), "path": target_dir if success else None}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "git clone 超时（120s）", "path": None}
    except Exception as e:
        return {"success": False, "output": str(e), "path": None}


def get_file_tree(repo: str, branch: str = None) -> dict:
    """获取仓库文件树（递归）"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        # 自动检测默认分支
        if not branch:
            repo_resp = httpx.get(f"https://api.github.com/repos/{repo}", headers=headers, timeout=30)
            if repo_resp.status_code == 200:
                branch = repo_resp.json().get("default_branch", "main")
            else:
                branch = "main"

        # 先获取 branch 的 SHA
        url = f"https://api.github.com/repos/{repo}/branches/{branch}"
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error": f"获取分支失败: {resp.status_code}"}
        sha = resp.json()["commit"]["sha"]

        # 获取递归文件树
        url = f"https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1"
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error": f"获取文件树失败: {resp.status_code}"}
        data = resp.json()
        tree = [item["path"] for item in data.get("tree", []) if item["type"] == "blob"]
        return {"success": True, "tree": tree, "truncated": data.get("truncated", False)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def read_file_content(repo: str, path: str) -> dict:
    """通过 GitHub API 读取文件内容"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        if data.get("encoding") == "base64":
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")
        else:
            content = data.get("content", "")
        return {"success": True, "content": content, "size": data.get("size", 0)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_issue_details(repo: str, issue_number: int) -> dict:
    """获取 GitHub Issue 详情"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        issue = resp.json()
        return {
            "success": True,
            "number": issue["number"],
            "title": issue["title"],
            "body": issue.get("body", ""),
            "state": issue["state"],
            "labels": [l["name"] for l in issue.get("labels", [])],
            "user": issue["user"]["login"],
            "created_at": issue["created_at"],
            "comments_count": issue["comments"],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_issue_comments(repo: str, issue_number: int) -> dict:
    """获取 Issue 的所有评论"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
        resp = httpx.get(url, headers=headers, timeout=30, params={"per_page": 100})
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        comments = [
            {"user": c["user"]["login"], "body": c["body"], "created_at": c["created_at"]}
            for c in resp.json()
        ]
        return {"success": True, "comments": comments, "count": len(comments)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_commit_frequency(repo: str) -> dict:
    """获取最近 52 周的 commit 频率统计"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{repo}/stats/commit_activity"
        resp = httpx.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        # 最近 12 周的 total
        recent = [w["total"] for w in data[-12:]]
        avg_weekly = sum(recent) / len(recent) if recent else 0
        return {
            "success": True,
            "weekly_totals": recent,
            "avg_weekly_commits": round(avg_weekly, 1),
            "total_last_12_weeks": sum(recent),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_workflow_runs(repo: str, limit: int = 10) -> dict:
    """获取 GitHub Actions workflow 运行记录"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{repo}/actions/runs"
        resp = httpx.get(url, headers=headers, timeout=30, params={"per_page": limit})
        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        data = resp.json()
        runs = [
            {
                "name": r["name"],
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "created_at": r["created_at"],
                "run_started_at": r.get("run_started_at"),
                "html_url": r["html_url"],
            }
            for r in data.get("workflow_runs", [])
        ]
        return {"success": True, "runs": runs, "total_count": data.get("total_count", 0)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_branch(repo: str, branch_name: str, base_sha: str) -> dict:
    """创建新分支"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            headers["Authorization"] = f"token {token}"

        url = f"https://api.github.com/repos/{repo}/git/refs"
        resp = httpx.post(url, headers=headers, json={
            "ref": f"refs/heads/{branch_name}",
            "sha": base_sha,
        }, timeout=30)
        if resp.status_code in (200, 201):
            return {"success": True, "ref": resp.json()["ref"]}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def commit_file(repo: str, path: str, content: str, message: str, branch: str) -> dict:
    """提交文件到指定分支"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return {"success": False, "error": "需要 GITHUB_TOKEN"}

        headers["Authorization"] = f"token {token}"

        # 获取现有文件的 SHA（如果存在）
        url = f"https://api.github.com/repos/{repo}/contents/{path}"
        resp = httpx.get(url, headers=headers, timeout=30, params={"ref": branch})
        sha = resp.json().get("sha") if resp.status_code == 200 else None

        # 提交
        body = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha

        resp = httpx.put(url, headers=headers, json=body, timeout=30)
        if resp.status_code in (200, 201):
            return {"success": True, "commit": resp.json().get("commit", {}).get("sha", "")}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_pull_request(repo: str, title: str, body: str, head: str, base: str = "main") -> dict:
    """创建 Pull Request"""
    try:
        headers = {"Accept": "application/vnd.github.v3+json"}
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            return {"success": False, "error": "需要 GITHUB_TOKEN"}

        headers["Authorization"] = f"token {token}"
        url = f"https://api.github.com/repos/{repo}/pulls"
        resp = httpx.post(url, headers=headers, json={
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }, timeout=30)
        if resp.status_code in (200, 201):
            pr = resp.json()
            return {"success": True, "url": pr["html_url"], "number": pr["number"]}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
