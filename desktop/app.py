"""
GitHub Explorer 桌面应用 — 基于 LangGraph
"""
import os
import sys
import asyncio
import json
from pathlib import Path

# 添加路径
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))

from desktop.settings import Settings, settings
from agent.memory import Memory
from agent.tools import run_command, clone_repo as tool_clone_repo, detect_project


class API:
    """本地 API — 供 pywebview 前端调用

    所有 Agent 逻辑通过 LangGraph graph 调度。
    """

    def __init__(self):
        self.memory = Memory()
        self.window = None
        self._apply_settings()

    def _apply_settings(self):
        api_config = settings.get_api_config()
        if api_config.get("api_key"):
            os.environ["ANTHROPIC_API_KEY"] = api_config["api_key"]
        if api_config.get("base_url"):
            os.environ["ANTHROPIC_BASE_URL"] = api_config["base_url"]
        if api_config.get("model"):
            os.environ["ANTHROPIC_MODEL"] = api_config["model"]

    def set_window(self, window):
        self.window = window

    def get_settings(self):
        return settings.get_all()

    def update_settings(self, new_settings):
        for key, value in new_settings.items():
            if isinstance(value, dict):
                for k, v in value.items():
                    settings.set(f"{key}.{k}", v)
            else:
                settings.set(key, value)
        self._apply_settings()
        return {"success": True}

    def search_projects(self, query, language=""):
        from agent.tools import search_github
        result = search_github(query, language)
        return result.get("repos", []) if result.get("success") else []

    def get_trending(self, period=7):
        return self.search_projects("stars:>5000 pushed:>2024-01-01")

    def clone_repo(self, repo):
        """直接调用工具克隆仓库"""
        clone_dir = settings.get("general.clone_dir", "./cloned_repos")
        Path(clone_dir).mkdir(parents=True, exist_ok=True)
        target = str(ROOT_DIR / clone_dir / repo.split("/")[-1])
        result = tool_clone_repo(repo, target)
        if result["success"]:
            self.memory.update_project(repo, local_path=result.get("path", target), status="cloned")
            self.memory.log_action("desktop", "clone", repo, "成功", True)
        return result

    def setup_project(self, repo):
        """通过 LangGraph 执行部署流程"""
        from agent.graph import get_graph

        async def _setup():
            graph = await get_graph()
            config = {"configurable": {"thread_id": "desktop"}}
            result = await graph.ainvoke(
                {
                    "user_message": f"部署 {repo}",
                    "session_id": "desktop",
                    "repo": repo,
                    "intent": "execute",
                    "needs_confirm": False,
                    "confirm_question": "",
                    "confirmed": True,
                    "response": "",
                    "execution_steps": [],
                },
                config=config,
            )
            return {
                "success": True,
                "steps": result.get("execution_steps", []),
                "message": result.get("response", ""),
            }

        return asyncio.run(_setup())

    def run_command(self, command, cwd=None):
        """直接调用工具执行命令"""
        if not cwd:
            project = self.memory.get_project(command.split()[0] if "/" in command else "")
            if project and project.get("local_path"):
                cwd = project["local_path"]
            else:
                cwd = str(ROOT_DIR / settings.get("general.clone_dir", "./cloned_repos"))
        return run_command(command, cwd=cwd)

    def chat(self, message, repo=None):
        """通过 LangGraph 对话"""
        from agent.graph import get_graph

        async def _chat():
            graph = await get_graph()
            config = {"configurable": {"thread_id": "desktop"}}
            result = await graph.ainvoke(
                {
                    "user_message": message,
                    "session_id": "desktop",
                    "repo": repo,
                    "intent": "chat",
                    "needs_confirm": False,
                    "confirm_question": "",
                    "confirmed": False,
                    "response": "",
                    "execution_steps": [],
                },
                config=config,
            )
            return result.get("response", "")

        return asyncio.run(_chat())

    def explain_project(self, repo):
        """通过 LangGraph 分析节点解读项目"""
        from agent.graph import get_graph

        async def _explain():
            graph = await get_graph()
            config = {"configurable": {"thread_id": "desktop"}}
            result = await graph.ainvoke(
                {
                    "user_message": f"解读 {repo}",
                    "session_id": "desktop",
                    "repo": repo,
                    "intent": "analyze",
                    "needs_confirm": False,
                    "confirm_question": "",
                    "confirmed": False,
                    "response": "",
                    "execution_steps": [],
                },
                config=config,
            )
            return result.get("response", "")

        return asyncio.run(_explain())

    def get_projects(self):
        return self.memory.get_all_projects()

    def get_history(self):
        return self.memory.get_action_logs(limit=50)


def create_app():
    """创建桌面窗口并返回，供 run_desktop.py 使用"""
    import webview

    api = API()

    # 确保目录存在
    Path(ROOT_DIR / settings.get("general.clone_dir", "./cloned_repos")).mkdir(exist_ok=True)
    Path(ROOT_DIR / "data").mkdir(exist_ok=True)

    # 创建窗口 - 指向Web版本
    window = webview.create_window(
        title="GitHub Explorer",
        url="http://127.0.0.1:7788",
        js_api=api,
        width=1280,
        height=860,
        min_size=(960, 640),
        resizable=True,
        text_select=True,
    )

    api.set_window(window)
    return window


def main():
    """主入口"""
    import webview

    create_app()
    webview.start(debug=False)


if __name__ == "__main__":
    main()
