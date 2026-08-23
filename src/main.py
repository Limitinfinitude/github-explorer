import asyncio
import os
import sys
from pathlib import Path
from dataclasses import asdict
from dotenv import load_dotenv

load_dotenv(override=True)

# 修复 SSL_CERT_FILE 指向不存在文件的问题
_ssl_cert = os.environ.get("SSL_CERT_FILE", "")
if _ssl_cert and not os.path.isfile(_ssl_cert):
    os.environ.pop("SSL_CERT_FILE", None)
    os.environ.pop("SSL_CERT_DIR", None)

ROOT_DIR = Path(__file__).parent.parent
SRC_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(SRC_DIR))

from typing import Optional, List, Any, Literal
from fastapi import FastAPI, Query, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn
import json

app = FastAPI(title="GitHub探索者", version="1.0.0")

WEB_DIST = SRC_DIR / "web_dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIST / "assets")), name="assets")

# ========== 请求模型 ==========

class ExplainRequest(BaseModel):
    repo: str

class CloneRequest(BaseModel):
    repo: str

class ChatRequest(BaseModel):
    message: str
    repo: Optional[str] = None
    history: Optional[List[dict]] = None

class AnalyzeRequest(BaseModel):
    repo: str

class LearningPathRequest(BaseModel):
    repo: str
    level: str = "beginner"

# ========== 首页 ==========

def _html_response(path: Path) -> FileResponse:
    """index.html 禁止缓存：构建后 hash 资源会变化，缓存旧入口会导致资源 404。"""
    return FileResponse(
        str(path),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/", response_class=HTMLResponse)
async def index():
    dist_index = SRC_DIR / "web_dist" / "index.html"
    if dist_index.exists():
        return _html_response(dist_index)
    return _html_response(SRC_DIR / "web" / "index.html")

# ========== 注册路由模块 ==========

from routes_search import router_search
from routes_agent import router_agent

app.include_router(router_search)
app.include_router(router_agent)

# ========== 多模型配置系统（独立模块） ==========

import model_config


class SettingsSelectRequest(BaseModel):
    model_id: str


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    protocol: Optional[Literal["anthropic", "openai"]] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    thinking_effort: Optional[Literal["off", "high", "max"]] = None


class ModelConfigCreate(BaseModel):
    name: str
    model: str
    protocol: Literal["anthropic", "openai"]
    api_key: str = ""
    base_url: str = ""
    thinking_effort: Literal["off", "high", "max"] = "off"


class ModelLatencyRequest(BaseModel):
    base_url: str


class ModelProbeRequest(BaseModel):
    protocol: Literal["anthropic", "openai"]
    base_url: str
    api_key: str = ""
    model_config_id: Optional[str] = None


class ModelConnectionRequest(ModelProbeRequest):
    model: str


@app.get("/api/settings")
async def get_settings():
    models_out = [model_config.public_model(cfg) for cfg in model_config.MODEL_CONFIGS.values()]
    return {
        "active_model": model_config.get_active_model_id(),
        "current_model": model_config.get_active_model_id(),
        "models": models_out,
    }


@app.post("/api/settings/select")
async def select_model_endpoint(s: SettingsSelectRequest):
    ok = model_config.apply_model(s.model_id)
    if ok:
        model_config._save_active_model_id(s.model_id)
    return {"ok": ok, "active_model": model_config.get_active_model_id()}


@app.post("/api/settings/models/latency")
async def measure_model_latency(s: ModelLatencyRequest):
    from agent.model_probe import measure_latency
    return await measure_latency(s.base_url)


@app.post("/api/settings/models/discover")
async def discover_model_ids(s: ModelProbeRequest):
    from agent.model_probe import discover_models
    saved_key = model_config.MODEL_CONFIGS.get(s.model_config_id or "", {}).get("api_key", "")
    return await discover_models(s.protocol, s.base_url, s.api_key or saved_key)


@app.post("/api/settings/models/test-connection")
async def test_model_connection(s: ModelConnectionRequest):
    from agent.model_probe import test_connection
    saved_key = model_config.MODEL_CONFIGS.get(s.model_config_id or "", {}).get("api_key", "")
    return await test_connection(s.protocol, s.base_url, s.api_key or saved_key, s.model)


@app.post("/api/settings/models/{model_id}")
async def update_model_config(model_id: str, s: ModelConfigUpdate):
    configs = model_config.MODEL_CONFIGS
    if model_id not in configs:
        raise HTTPException(status_code=404, detail="未知模型")
    if configs[model_id].get("source") == "environment":
        raise HTTPException(status_code=403, detail="环境变量模型不可编辑")
    name = s.name.strip() if s.name is not None else configs[model_id]["name"]
    provider_model = s.model.strip() if s.model is not None else configs[model_id].get("model", model_id)
    if not name or not provider_model:
        raise HTTPException(status_code=422, detail="模型名称和模型 ID 不能为空")
    configs[model_id]["name"] = name
    configs[model_id]["model"] = provider_model
    configs[model_id]["icon"] = name[:1].upper()
    if s.protocol is not None:
        configs[model_id]["protocol"] = s.protocol
        configs[model_id]["color"] = "#238636" if s.protocol == "openai" else "#d97757"
        configs[model_id]["tags"] = ["自定义", "OpenAI Compatible" if s.protocol == "openai" else "Anthropic"]
    if s.api_key is not None and s.api_key.strip() and not s.api_key.startswith("*"):
        configs[model_id]["api_key"] = s.api_key.strip()
    if s.base_url is not None:
        configs[model_id]["base_url"] = s.base_url.strip()
    if s.thinking_effort is not None:
        configs[model_id]["thinking_effort"] = s.thinking_effort
    model_config._save_model_configs(configs)
    if model_id == model_config.get_active_model_id():
        model_config.apply_model(model_id)
        model_config._save_active_model_id(model_id)
    return {"ok": True, "model": model_config.public_model(configs[model_id])}


@app.post("/api/settings/models")
async def create_model_config(s: ModelConfigCreate):
    name = s.name.strip()
    provider_model = s.model.strip()
    if not name or not provider_model:
        raise HTTPException(status_code=422, detail="模型名称和模型 ID 不能为空")
    model_id = model_config.new_model_id(name)
    cfg = {
        "id": model_id,
        "name": name,
        "model": provider_model,
        "protocol": s.protocol,
        "icon": name[:1].upper(),
        "color": "#238636" if s.protocol == "openai" else "#d97757",
        "tags": ["自定义", "OpenAI Compatible" if s.protocol == "openai" else "Anthropic"],
        "api_key": s.api_key.strip(),
        "base_url": s.base_url.strip(),
        "thinking_effort": s.thinking_effort,
    }
    model_config.MODEL_CONFIGS[model_id] = cfg
    model_config._save_model_configs(model_config.MODEL_CONFIGS)
    model_config.apply_model(model_id)
    model_config._save_active_model_id(model_id)
    return {"ok": True, "model": model_config.public_model(cfg)}

# ========== AI 分析 API（统一 Local Runtime） ==========

async def _run_compat_agent(message: str, history: list[dict] | None = None) -> dict:
    from routes_agent import run_local_agent_once

    return await run_local_agent_once("web", message, history=history)

@app.post("/api/chat")
async def chat_with_agent(request: ChatRequest):
    result = await _run_compat_agent(request.message, request.history)
    return {"response": result["response"], "status": result["status"], "task_id": result["task_id"]}


@app.post("/api/analyze")
async def analyze_project(request: AnalyzeRequest):
    result = await _run_compat_agent(f"深度分析 {request.repo}")
    return {"analysis": result["response"], "status": result["status"], "task_id": result["task_id"]}


@app.post("/api/learning-path")
async def get_learning_path(request: LearningPathRequest):
    result = await _run_compat_agent(f"为 {request.repo} 生成 {request.level} 水平的学习路径")
    return {"path": result["response"], "status": result["status"], "task_id": result["task_id"]}


@app.post("/api/usage-example")
async def get_usage_example(request: AnalyzeRequest):
    result = await _run_compat_agent(f"为 {request.repo} 生成使用示例")
    return {"example": result["response"], "status": result["status"], "task_id": result["task_id"]}

# ========== 本地文件访问 API ==========

@app.get("/api/local/list")
async def list_local_files(path: str = "."):
    """列出本地目录内容"""
    try:
        target = Path(path).resolve()
        if not target.exists():
            return {"error": "路径不存在", "path": path}

        items = []
        for item in sorted(target.iterdir()):
            if item.name.startswith('.'):
                continue
            items.append({
                "name": item.name,
                "path": str(item),
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
                "modified": item.stat().st_mtime
            })

        return {
            "current": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "items": items
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/local/read")
async def read_local_file(path: str, max_lines: int = 500):
    """读取本地文件内容"""
    try:
        target = Path(path).resolve()
        if not target.exists():
            return {"error": "文件不存在"}

        if target.is_dir():
            return {"error": "这是一个目录"}

        if target.stat().st_size > 1024 * 1024:
            return {"error": "文件过大，请使用其他方式查看"}

        content = target.read_text(encoding='utf-8', errors='replace')
        lines = content.split('\n')

        return {
            "path": str(target),
            "name": target.name,
            "size": target.stat().st_size,
            "total_lines": len(lines),
            "content": '\n'.join(lines[:max_lines])
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/local/info")
async def get_system_info():
    """获取系统信息"""
    import platform
    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "home": str(Path.home()),
        "cwd": str(Path.cwd()),
        "clone_dir": str(ROOT_DIR / "cloned_repos")
    }


@app.get("/api/local/drives")
async def get_drives():
    """获取磁盘驱动器列表（Windows）"""
    import string
    drives = []
    if os.name == 'nt':
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append({"letter": letter, "path": drive})
    else:
        drives.append({"letter": "/", "path": "/"})
    return {"drives": drives}


# ========== 本地文件写操作（已停用） ==========
# 旧写操作端点不参与工作区边界与权限校验，无法安全映射，统一返回 410。
# 文件创建、编辑、删除等能力收敛至 Agent 文件工具（edit_files 等），
# 由 LocalAgentRuntime 统一执行工作区校验与变更证据记录。

_FILE_WRITE_GONE = "本地文件写操作端点已停用。请通过 Agent 工具（edit_files / create_directory）或 /api/agent/tasks/start 完成文件变更。"


@app.post("/api/local/create-folder")
async def create_folder(request: dict):
    raise HTTPException(status_code=410, detail=_FILE_WRITE_GONE)


@app.post("/api/local/create-file")
async def create_file(request: dict):
    raise HTTPException(status_code=410, detail=_FILE_WRITE_GONE)


@app.post("/api/local/delete")
async def delete_item(request: dict):
    raise HTTPException(status_code=410, detail=_FILE_WRITE_GONE)


@app.post("/api/local/rename")
async def rename_item(request: dict):
    raise HTTPException(status_code=410, detail=_FILE_WRITE_GONE)


@app.post("/api/local/save")
async def save_file(request: dict):
    raise HTTPException(status_code=410, detail=_FILE_WRITE_GONE)


@app.post("/api/local/set-workspace")
async def set_workspace(request: dict):
    """设置工作目录"""
    try:
        path = request.get("path")
        if path and Path(path).is_dir():
            from agent.memory import memory
            memory.set_preference("workspace", path)
            return {"success": True, "workspace": path}
        return {"success": False, "error": "无效路径"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/local/workspace")
async def get_workspace():
    """获取工作目录"""
    try:
        from agent.memory import memory
        workspace = memory.get_preference("workspace") or str(Path.cwd())
        return {"workspace": workspace}
    except:
        return {"workspace": str(Path.cwd())}


@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """SPA fallback — 所有非 API 路由返回 index.html"""
    dist_index = SRC_DIR / "web_dist" / "index.html"
    if dist_index.exists():
        return _html_response(dist_index)
    return HTMLResponse("<h3>前端未构建，请先运行 cd src/web && npm run build</h3>", status_code=404)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
    port = int(os.getenv("PORT", 7788))
    print(f"\n[启动] GitHub探索者")
    print(f"[地址] http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port)
