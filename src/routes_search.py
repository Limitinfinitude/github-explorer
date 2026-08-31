"""
搜索相关路由 — /api/search, /api/trending, /api/repo/, /api/stars/,
/api/explain, /api/clone, /api/launch-instructions/, /api/image/
"""
import re
from pathlib import Path
from dataclasses import asdict

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
import httpx

router_search = APIRouter()


def repo_to_dict(repo):
    """将RepoInfo对象转为字典"""
    if hasattr(repo, '__dataclass_fields__'):
        return asdict(repo)
    return repo


@router_search.get("/api/search")
async def search_repos(
    q: str = Query("", description="搜索关键词"),
    lang: str = Query("", description="编程语言"),
    period: int = Query(30, description="时间范围(天)"),
    hint: str = Query("", description="中文意图限定词（前端发现 preset 传入，提升中文搜索命中）"),
):
    from api.github_client import GitHubClient
    query = q if q.strip() else "stars:>1000"
    # 中文友好：hint 里给定英文限定词时拼进查询（用户没指定 lang 时效果最明显——
    # GitHub 搜索按 README 匹配，英文 query 对中文描述库命中率低）
    if hint.strip():
        query = f"{query} {hint.strip()}"
    async with GitHubClient() as client:
        repos = await client.search_repos(query, lang, period)
        return {"repos": [repo_to_dict(r) for r in repos]}


async def _enrich_trending_repos(repos: list[dict], limit: int = 8) -> None:
    """给 trending 抓页结果补详情：owner avatar / topics / open_issues / pushed_at / license。

    trending 页面本身只有 7 个字段——卡片缺判断依据（topics、活跃度、维护信号）。
    对前 limit 条用 /repos/{owner}/{repo} 批量补全（未认证 core API 限流 60/小时，
    批量 8 个远低于阈值）。Python 过滤：只处理主语言含 Python 的条目，其他跳过。
    """
    import asyncio
    from api.github_client import GitHubClient

    targets = [
        r for r in repos[:limit]
        if r.get("language", "").casefold() == "python"
    ]
    if not targets:
        return

    async with GitHubClient() as client:
        async def fetch_one(repo: dict) -> dict:
            full = repo.get("full_name", "")
            if "/" not in full:
                return repo
            owner, name = full.split("/", 1)
            try:
                detail = await client.get_repo_details(owner, name)
                payload = repo_to_dict(detail)
                repo.update({
                    "topics": payload.get("topics") or [],
                    "owner_avatar": payload.get("owner", {}).get("avatar_url") if isinstance(payload.get("owner"), dict) else "",
                    "open_issues": payload.get("open_issues_count"),
                    "pushed_at": payload.get("pushed_at"),
                    "license": (payload.get("license") or {}).get("spdx_id") if isinstance(payload.get("license"), dict) else "",
                })
            except Exception:
                pass  # 详情失败不阻断：保留 trending 原有字段
            return repo

        await asyncio.gather(*(fetch_one(repo) for repo in targets))


@router_search.get("/api/trending")
async def get_trending(
    period: int = Query(7, description="时间范围(天): 1=daily, 7=weekly, 30=monthly"),
    lang: str = Query("", description="编程语言")
):
    """抓取 github.com/trending 页面的真实热榜数据"""
    from bs4 import BeautifulSoup

    # 映射 period 到 github.com/trending 的 since 参数
    since_map = {1: "daily", 7: "weekly", 30: "monthly"}
    since = since_map.get(period, "weekly")

    # 构建 URL
    url = f"https://github.com/trending/{lang}" if lang else "https://github.com/trending"
    url += f"?since={since}"

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            })
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        articles = soup.find_all("article", class_="Box-row")

        repos = []
        for art in articles:
            # 仓库名
            h2 = art.find("h2")
            a_tag = h2.find("a") if h2 else None
            repo_name = a_tag.get("href", "").strip("/") if a_tag else ""
            if not repo_name:
                continue

            # 描述
            p_tag = art.find("p")
            description = p_tag.get_text(strip=True) if p_tag else ""

            # 编程语言
            lang_tag = art.find("span", itemprop="programmingLanguage")
            language = lang_tag.get_text(strip=True) if lang_tag else ""

            # 总 star 数
            star_link = art.find("a", href=lambda h: h and "stargazers" in h)
            total_stars_text = star_link.get_text(strip=True).replace(",", "") if star_link else "0"
            try:
                total_stars = int(total_stars_text)
            except ValueError:
                total_stars = 0

            # Fork 数
            fork_link = art.find("a", href=lambda h: h and "/forks" in h)
            forks_text = fork_link.get_text(strip=True).replace(",", "") if fork_link else "0"
            try:
                forks = int(forks_text)
            except ValueError:
                forks = 0

            # 期间新增 star
            all_text = art.get_text()
            gain_match = re.search(r"([\d,]+)\s+stars?\s+(today|this week|this month)", all_text)
            stars_today = int(gain_match.group(1).replace(",", "")) if gain_match else 0

            repos.append({
                "name": repo_name,
                "full_name": repo_name,
                "description": description,
                "language": language,
                "stars": total_stars,
                "forks": forks,
                "stars_today": stars_today,
                "url": f"https://github.com/{repo_name}",
                "trending_period": since,
            })

        # 详情补全：前 8 条 Python 仓库批量加 topics/avatar/issues/活跃度/许可
        # （trending 页面只有 7 字段，补全后卡片才有判断依据）
        await _enrich_trending_repos(repos, limit=8)

        return {"repos": repos, "source": "github.com/trending", "period": since}
    except Exception as e:
        return {"repos": [], "error": str(e), "source": "github.com/trending"}


@router_search.get("/api/repo/{owner}/{repo}")
async def get_repo_detail(owner: str, repo: str):
    from api.github_client import GitHubClient
    async with GitHubClient() as client:
        detail = await client.get_repo_details(owner, repo)
        return repo_to_dict(detail)


@router_search.get("/api/repo/{owner}/{repo}/readme")
async def get_repo_readme(owner: str, repo: str, max_chars: int = Query(1200, ge=100, le=4000)):
    """抽屉预览用的 README 截取：只取开头一段（标题/简介），不做全文返回。"""
    from api.github_client import GitHubClient
    async with GitHubClient() as client:
        content = await client.get_readme(owner, repo)
    if not content:
        return {"readme": None}
    # 跳过首个 # 标题行和连续空行，取前 max_chars 字符（截到最近的段落边界）
    lines = content.split("\n")
    start = 0
    for i, line in enumerate(lines[:10]):
        if line.strip() and not line.lstrip().startswith("#"):
            start = i
            break
    body = "\n".join(lines[start:]).strip()
    if len(body) > max_chars:
        cut = body.rfind("\n\n", 0, max_chars)
        body = body[:cut if cut > max_chars // 2 else max_chars].rstrip() + "…"
    return {"readme": body}


@router_search.get("/api/stars/{owner}/{repo}")
async def get_star_history(owner: str, repo: str):
    from api.github_client import GitHubClient
    async with GitHubClient() as client:
        history = await client.get_star_history(owner, repo)
        return {"history": repo_to_dict(history)}


@router_search.post("/api/explain")
async def explain_repo(request):
    from routes_agent import run_local_agent_once

    result = await run_local_agent_once("web", f"解读 {request.repo}")
    return {
        "explanation": result["response"],
        "status": result["status"],
        "task_id": result["task_id"],
    }


@router_search.post("/api/clone")
async def clone_repo_endpoint(request):
    from utils.local_launcher import clone_repo as do_clone, get_launch_instructions
    repo_url = f"https://github.com/{request.repo}.git"
    path = do_clone(repo_url)
    instructions = get_launch_instructions(path)
    return {
        "success": True,
        "path": str(path),
        "instructions": "\n".join(instructions.get("commands", []))
    }


@router_search.get("/api/launch-instructions/{owner}/{repo}")
async def get_launch_instructions(owner: str, repo: str):
    from utils.local_launcher import get_launch_instructions as get_instructions
    repo_path = Path("./cloned_repos") / repo
    if repo_path.exists():
        instructions = get_instructions(repo_path)
        return {"instructions": "\n".join(instructions.get("commands", []))}
    return {"instructions": "项目尚未克隆到本地，请先点击克隆按钮"}


@router_search.get("/api/image/{owner}/{repo}")
async def get_project_image(owner: str, repo: str):
    from api.image_gen import get_or_generate_image
    from api.github_client import GitHubClient
    async with GitHubClient() as client:
        detail = await client.get_repo_details(owner, repo)
    image_url = await get_or_generate_image(
        detail.full_name,
        detail.description,
        detail.language
    )
    return {"image_url": image_url}
