"""
MCP 工具集成 — mcp_search_repos, mcp_search_code, mcp_get_file_contents,
mcp_list_issues, mcp_search_issues, mcp_web_fetch, get_mcp_tools_info
"""


async def mcp_search_repos(query: str, language: str = "", limit: int = 10) -> dict:
    """通过 GitHub MCP Server 搜索仓库"""
    from ..mcp_client import mcp_tool_call
    q = query
    if language:
        q += f" language:{language}"
    return await mcp_tool_call("search_repositories", {"query": q, "perPage": limit})


async def mcp_search_code(query: str, repo: str = "", language: str = "") -> dict:
    """通过 GitHub MCP Server 搜索代码"""
    from ..mcp_client import mcp_tool_call
    q = query
    if repo:
        q += f" repo:{repo}"
    if language:
        q += f" language:{language}"
    return await mcp_tool_call("search_code", {"q": q})


async def mcp_get_file_contents(owner: str, repo: str, path: str) -> dict:
    """通过 GitHub MCP Server 获取文件内容"""
    from ..mcp_client import mcp_tool_call
    return await mcp_tool_call("get_file_contents", {
        "owner": owner, "repo": repo, "path": path
    })


async def mcp_list_issues(owner: str, repo: str, state: str = "open") -> dict:
    """通过 GitHub MCP Server 列出 Issue"""
    from ..mcp_client import mcp_tool_call
    return await mcp_tool_call("list_issues", {
        "owner": owner, "repo": repo, "state": state
    })


async def mcp_search_issues(query: str, limit: int = 10) -> dict:
    """通过 GitHub MCP Server 搜索 Issue"""
    from ..mcp_client import mcp_tool_call
    return await mcp_tool_call("search_issues", {"q": query, "per_page": limit})


async def mcp_web_fetch(url: str) -> dict:
    """通过 Web Fetch MCP Server 获取网页内容"""
    from ..mcp_client import mcp_tool_call
    return await mcp_tool_call("fetch", {"url": url})


async def get_mcp_tools_info() -> dict:
    """获取所有可用的 MCP 工具信息"""
    from ..mcp_client import get_mcp_client, init_mcp
    client = await get_mcp_client()
    if not client.is_connected():
        await init_mcp()
    return {
        "connected": client.is_connected(),
        "servers": client.get_server_names(),
        "tools": client.get_all_tools(),
    }
