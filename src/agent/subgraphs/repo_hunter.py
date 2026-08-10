"""
Repo Hunter（探索者）子智能体 - 负责搜索和评估 GitHub 项目

子图流程：[START] → fetch_trending → assess_health → rate_worth → [END]
"""

from langgraph.graph import StateGraph, END
from langsmith import traceable

import asyncio as _asyncio

from ..swarm_state import SwarmState
from ..tools import search_github, fetch_repo_info, get_commit_frequency, web_search
from ..llm import call_llm
from ..prompts import REPO_HUNTER_SYSTEM_PROMPT, REPO_HEALTH_PROMPT


@traceable(name="repo_hunter_fetch_trending")
async def fetch_trending_node(state: SwarmState) -> dict:
    """获取热门仓库信息"""
    repo = state.get("repo")
    if repo:
        repo_info = await _asyncio.to_thread(fetch_repo_info, repo)
        search_results = [repo_info] if repo_info else []
    else:
        user_message = state.get("user_message", "")
        query = user_message.strip()
        if query:
            # 用 LLM 提取搜索关键词和语言
            from ..llm import call_llm, call_llm_json
            kw_prompt = (
                f"从以下用户消息中提取搜索信息，返回 JSON：\n"
                f'{{"keywords": "搜索关键词", "language": "编程语言或空"}}\n'
                f"只返回 JSON，不要其他内容。\n\n"
                f"用户消息：{query}"
            )
            try:
                kw_result = await call_llm_json(
                    "你是关键词提取器。只返回 JSON。",
                    [{"role": "user", "content": kw_prompt}],
                    max_tokens=80,
                    temperature=0.1,
                )
                search_query = kw_result.get("keywords", "").strip() or query
                language = kw_result.get("language", "").strip()
            except Exception:
                search_query = query
                language = ""

            # GitHub API 搜索（带语言筛选和最低 stars）
            raw = await _asyncio.to_thread(
                search_github, search_query, language, 10, "stars", 100
            )
            search_results = raw.get("repos", []) if isinstance(raw, dict) else []

            # 同时用 Web 搜索获取外部资源推荐
            web_query = f"{search_query} github {language} best repos".strip()
            web_raw = await _asyncio.to_thread(web_search, web_query, 5)
            web_context = ""
            if web_raw.get("success") and web_raw.get("results"):
                web_context = "\n".join(
                    f"- {r.get('title', '')}: {r.get('snippet', '')}"
                    for r in web_raw["results"]
                )

            # 如果结果不足 3 个，用 LLM 补充推荐（带 web 搜索上下文）
            if len(search_results) < 3:
                llm_prompt = (
                    f"用户想搜索：{query}\n"
                    f"GitHub API 只找到 {len(search_results)} 个项目。\n"
                )
                if web_context:
                    llm_prompt += f"\nWeb 搜索到的相关信息：\n{web_context}\n"
                llm_prompt += (
                    f"请基于以上信息和你的知识，推荐 5 个相关的高质量 GitHub 项目。\n"
                    f"返回 JSON 数组：\n"
                    f'[{{"full_name": "owner/repo", "description": "描述", "stars": 数字, "language": "语言"}}]'
                )
                try:
                    llm_result = await call_llm_json(
                        "你是 GitHub 项目推荐专家。只返回 JSON 数组。",
                        [{"role": "user", "content": llm_prompt}],
                        max_tokens=800,
                        temperature=0.3,
                    )
                    if isinstance(llm_result, list):
                        existing_names = {r.get("full_name", "") for r in search_results}
                        for item in llm_result:
                            if isinstance(item, dict) and item.get("full_name") not in existing_names:
                                item.setdefault("forks", 0)
                                item.setdefault("topics", [])
                                item.setdefault("pushed_at", "")
                                item.setdefault("html_url", f"https://github.com/{item['full_name']}")
                                search_results.append(item)
                                existing_names.add(item["full_name"])
                except Exception:
                    pass
        else:
            search_results = []

    return {"search_results": search_results}


@traceable(name="repo_hunter_assess_health")
async def assess_health_node(state: SwarmState) -> dict:
    """
    评估仓库健康度。

    - 取 search_results 的前 3 个 repo
    - 对每个调用 get_commit_frequency 获取提交频率
    - 计算简单健康度：stars 权重 + commit 频率权重
    """
    search_results = state.get("search_results", [])
    repos_to_assess = search_results[:3]

    repo_health = {}
    for repo_info in repos_to_assess:
        full_name = repo_info.get("full_name", "")
        if not full_name:
            continue

        # 获取提交频率
        commit_freq = await _asyncio.to_thread(get_commit_frequency, full_name)
        avg_weekly_commits = commit_freq.get("avg_weekly_commits", 0)

        # 计算健康度（简单加权）
        stars = repo_info.get("stars", 0)
        # stars 权重: log scale，避免超大仓库主导
        import math
        star_score = min(math.log10(stars + 1) / 5, 1.0) * 40  # 最高 40 分
        # commit 频率权重: 每周 commit 越多越活跃
        commit_score = min(avg_weekly_commits / 50, 1.0) * 60  # 最高 60 分
        health_score = round(star_score + commit_score, 2)

        repo_health[full_name] = {
            "stars": stars,
            "avg_weekly_commits": avg_weekly_commits,
            "health_score": health_score,
        }

    return {"repo_health": repo_health}


@traceable(name="repo_hunter_rate_worth")
async def rate_worth_node(state: SwarmState) -> dict:
    """
    评估仓库是否值得学习。

    - 用 REPO_HEALTH_PROMPT 格式化健康数据
    - 调用 call_llm 生成评估报告
    """
    repo_health = state.get("repo_health", {})

    if not repo_health:
        return {
            "worth_learning": "无法评估",
            "response": "未找到有效的仓库信息进行评估。",
        }

    # 格式化健康数据为 prompt
    health_data_str = ""
    for repo_name, health in repo_health.items():
        health_data_str += f"- {repo_name}: stars={health['stars']}, "
        health_data_str += f"weekly_commits={health['avg_weekly_commits']}, "
        health_data_str += f"health_score={health['health_score']}\n"

    prompt = REPO_HEALTH_PROMPT.format(health_data=health_data_str)

    # 调用 LLM 生成评估
    response = await call_llm(
        REPO_HUNTER_SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
    )

    # 从 response 中提取 worth_learning 判断
    worth_learning = "值得学习" if any(
        h["health_score"] >= 50 for h in repo_health.values()
    ) else "谨慎考虑"

    return {
        "worth_learning": worth_learning,
        "response": response,
    }


def build_repo_hunter_graph():
    """编译 Repo Hunter 子图。"""
    graph = StateGraph(SwarmState)

    graph.add_node("fetch_trending", fetch_trending_node)
    graph.add_node("assess_health", assess_health_node)
    graph.add_node("rate_worth", rate_worth_node)

    graph.set_entry_point("fetch_trending")
    graph.add_edge("fetch_trending", "assess_health")
    graph.add_edge("assess_health", "rate_worth")
    graph.add_edge("rate_worth", END)

    return graph.compile()
