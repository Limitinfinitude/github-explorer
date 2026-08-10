"""
Web 搜索与抓取工具 — web_search, web_fetch, code_search
"""
import os
import re

import httpx

from langsmith import traceable


@traceable(name="tool_web_search")
def web_search(query: str, max_results: int = 5) -> dict:
    """
    通过 DuckDuckGo 搜索网页（无需 API Key）。
    用于搜索技术博客、教程、StackOverflow 等外部资源。
    """
    try:
        # 使用 DuckDuckGo HTML 版本搜索
        url = "https://html.duckduckgo.com/html/"
        resp = httpx.post(url, data={"q": query}, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=15, follow_redirects=True)

        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}

        # 简单解析搜索结果
        from html.parser import HTMLParser

        class DDGParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.results = []
                self.current = None
                self.in_result = False
                self.in_snippet = False

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                if tag == "a" and "result__a" in attrs_dict.get("class", ""):
                    self.in_result = True
                    self.current = {
                        "title": "",
                        "url": attrs_dict.get("href", ""),
                        "snippet": ""
                    }
                if tag == "a" and "result__snippet" in attrs_dict.get("class", ""):
                    self.in_snippet = True

            def handle_data(self, data):
                if self.in_result and self.current:
                    self.current["title"] += data
                if self.in_snippet and self.current:
                    self.current["snippet"] += data

            def handle_endtag(self, tag):
                if tag == "a" and self.in_result:
                    self.in_result = False
                if tag == "a" and self.in_snippet:
                    self.in_snippet = False
                    if self.current:
                        self.current["title"] = self.current["title"].strip()
                        self.current["snippet"] = self.current["snippet"].strip()
                        if self.current["title"]:
                            self.results.append(self.current)
                        self.current = None

        parser = DDGParser()
        parser.feed(resp.text)

        results = parser.results[:max_results]
        return {"success": True, "results": results, "count": len(results)}

    except Exception as e:
        return {"success": False, "error": str(e)}


@traceable(name="tool_web_fetch")
def web_fetch(url: str, max_length: int = 5000) -> dict:
    """
    获取网页内容并提取文本。
    用于读取 README、博客文章、文档等。
    """
    try:
        resp = httpx.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }, timeout=15, follow_redirects=True)

        if resp.status_code != 200:
            return {"success": False, "error": f"HTTP {resp.status_code}"}

        # 简单 HTML → 文本转换
        text = resp.text
        # 移除 script 和 style
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        # 将 <br> 和 <p> 转为换行
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</p>', '\n', text, flags=re.IGNORECASE)
        # 移除所有 HTML 标签
        text = re.sub(r'<[^>]+>', '', text)
        # 清理空白
        text = re.sub(r'\n\s*\n', '\n\n', text)
        text = text.strip()

        if len(text) > max_length:
            text = text[:max_length] + "\n... (内容已截断)"

        return {"success": True, "content": text, "url": url, "length": len(text)}

    except Exception as e:
        return {"success": False, "error": str(e)}


@traceable(name="tool_code_search")
def code_search(query: str, language: str = "", repo: str = "", limit: int = 10) -> dict:
    """
    搜索 GitHub 代码。
    需要 GITHUB_TOKEN 才能使用代码搜索 API。
    """
    try:
        token = os.environ.get("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        q = query.strip()
        if language:
            q += f" language:{language}"
        if repo:
            q += f" repo:{repo}"

        resp = httpx.get(
            "https://api.github.com/search/code",
            params={"q": q, "per_page": limit},
            headers=headers,
            timeout=30,
        )

        if resp.status_code == 200:
            items = resp.json().get("items", [])
            results = [
                {
                    "name": item["name"],
                    "path": item["path"],
                    "repo": item["repository"]["full_name"],
                    "url": item["html_url"],
                    "score": item.get("score", 0),
                }
                for item in items
            ]
            return {"success": True, "results": results, "count": len(results)}
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

    except Exception as e:
        return {"success": False, "error": str(e)}
