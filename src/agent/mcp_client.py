"""
MCP 客户端 — 让 Agent 能调用 MCP Server 的工具

支持：
- GitHub MCP Server（仓库/代码/Issue/PR 搜索）
- Web Fetch MCP Server（网页内容读取）
- 自定义 MCP Server
"""

import os
import json
import asyncio
from pathlib import Path
from typing import Optional, Dict, Any, List
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class MCPClient:
    """MCP 客户端，连接 MCP Server 并调用其工具"""

    def __init__(self):
        self._sessions: Dict[str, ClientSession] = {}
        self._exit_stack: Optional[AsyncExitStack] = None
        self._tools_cache: Dict[str, List[dict]] = {}
        self._connected = False

    async def connect_from_config(self, config_path: str = None) -> dict:
        """
        从 .mcp.json 配置文件加载并连接所有 MCP Server。

        Returns:
            {"connected": [server_names], "failed": [server_names]}
        """
        if config_path is None:
            config_path = str(Path(__file__).parent.parent.parent / ".mcp.json")

        if not Path(config_path).exists():
            return {"connected": [], "failed": [], "error": "配置文件不存在"}

        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        connected = []
        failed = []

        for name, server_config in servers.items():
            try:
                await self.connect_server(name, server_config)
                connected.append(name)
            except Exception as e:
                failed.append({"name": name, "error": str(e)})

        self._connected = len(connected) > 0
        return {"connected": connected, "failed": failed}

    async def connect_server(self, name: str, config: dict) -> None:
        """连接单个 MCP Server"""
        if self._exit_stack is None:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

        server_type = config.get("type", "stdio")

        if server_type == "stdio":
            command = config.get("command", "")
            args = config.get("args", [])
            env = {**os.environ, **config.get("env", {})}

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
            )

            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            self._sessions[name] = session

            # 缓存工具列表
            tools_result = await session.list_tools()
            self._tools_cache[name] = [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": getattr(tool, "input_schema", None)
                    or getattr(tool, "inputSchema", None) or {},
                    "server": name,
                }
                for tool in tools_result.tools
            ]

        else:
            raise ValueError(f"不支持的 MCP Server 类型: {server_type}")

    async def disconnect(self) -> None:
        """断开所有 MCP Server 连接"""
        if self._exit_stack:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception:
                pass
            self._exit_stack = None
        self._sessions.clear()
        self._tools_cache.clear()
        self._connected = False

    def get_all_tools(self) -> List[dict]:
        """获取所有已连接 MCP Server 的工具列表"""
        all_tools = []
        for server_name, tools in self._tools_cache.items():
            all_tools.extend(tools)
        return all_tools

    def get_tools_for_server(self, server_name: str) -> List[dict]:
        """获取指定 MCP Server 的工具列表"""
        return self._tools_cache.get(server_name, [])

    async def call_tool(self, tool_name: str, arguments: dict = None) -> dict:
        """
        调用 MCP 工具。自动查找哪个 Server 提供了该工具。

        Args:
            tool_name: 工具名称
            arguments: 工具参数

        Returns:
            {"success": True, "content": str, "server": str} 或
            {"success": False, "error": str}
        """
        if arguments is None:
            arguments = {}

        # 查找提供该工具的 Server
        for server_name, tools in self._tools_cache.items():
            for tool in tools:
                if tool["name"] == tool_name:
                    session = self._sessions.get(server_name)
                    if not session:
                        return {"success": False, "error": f"Server {server_name} 未连接"}

                    try:
                        result = await session.call_tool(tool_name, arguments)
                        # 提取文本内容
                        content = ""
                        if hasattr(result, 'content'):
                            for block in result.content:
                                if hasattr(block, 'text'):
                                    content += block.text
                        return {
                            "success": True,
                            "content": content,
                            "server": server_name,
                        }
                    except Exception as e:
                        return {"success": False, "error": str(e)}

        return {"success": False, "error": f"未找到工具: {tool_name}"}

    def is_connected(self) -> bool:
        return self._connected

    def get_server_names(self) -> List[str]:
        return list(self._sessions.keys())


# 全局 MCP 客户端实例
_mcp_client: Optional[MCPClient] = None


async def get_mcp_client() -> MCPClient:
    """获取全局 MCP 客户端（懒加载）"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client


async def init_mcp() -> dict:
    """初始化 MCP 连接（从 .mcp.json 加载）"""
    client = await get_mcp_client()
    if client.is_connected():
        return {"status": "already_connected", "servers": client.get_server_names()}
    return await client.connect_from_config()


async def mcp_tool_call(tool_name: str, arguments: dict = None) -> dict:
    """便捷函数：调用 MCP 工具"""
    client = await get_mcp_client()
    if not client.is_connected():
        result = await init_mcp()
        if result.get("error"):
            return {"success": False, "error": f"MCP 连接失败: {result['error']}"}
    return await client.call_tool(tool_name, arguments)


def cached_mcp_tools() -> list:
    """同步读取已连接 MCP Server 的工具缓存（未连接时返回空列表）。

    agent 工具注册表是同步构建的，无法在此 await 连接；启动期由
    ensure_mcp_connected 预连，这里只读缓存。
    """
    if _mcp_client is None:
        return []
    return _mcp_client.get_all_tools()


async def ensure_mcp_connected() -> dict:
    """尽力连接 MCP（供任务启动前预热，失败不阻断任务）。"""
    try:
        return await init_mcp()
    except asyncio.CancelledError:
        # wait_for 超时取消：清理半连接，避免留下僵尸 npx/stdio 进程
        if _mcp_client is not None:
            try:
                await _mcp_client.disconnect()
            except Exception:
                pass
        raise
    except Exception as exc:
        return {"connected": [], "failed": [{"name": "*", "error": str(exc)}]}
