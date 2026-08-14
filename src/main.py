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

@app.get("/", response_class=HTMLResponse)
async def index():
    dist_index = SRC_DIR / "web_dist" / "index.html"
    if dist_index.exists():
        return FileResponse(str(dist_index))
    return FileResponse(str(SRC_DIR / "web" / "index.html"))

# ========== 注册路由模块 ==========

from routes_search import router_search
from routes_agent import router_agent

app.include_router(router_search)
app.include_router(router_agent)

# ========== 多模型配置系统 ==========

_MODEL_CONFIGS_PATH = Path(__file__).parent.parent / "data" / "model_configs.json"
_ACTIVE_MODEL_PATH = Path(__file__).parent.parent / "data" / "active_model.json"

_DEFAULT_MODEL_CONFIGS = [
    {"id": "mimo-v2.5",       "name": "Mimo v2.5",       "model": "mimo-v2.5",       "protocol": "anthropic", "icon": "M", "color": "#8250df", "tags": ["1M", "识图"],  "api_key": "", "base_url": "https://api.xiaomimimo.com/anthropic"},
    {"id": "grok-4.5",        "name": "Grok 4.5",        "model": "grok-4.5",        "protocol": "anthropic", "icon": "G", "color": "#cf222e", "tags": ["极速"],        "api_key": "", "base_url": "https://zz.aiapi2025.top"},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "model": "claude-sonnet-5", "protocol": "anthropic", "icon": "C", "color": "#0550ae", "tags": ["最新"],        "api_key": "", "base_url": "https://zz.aiapi2025.top"},
]


def _load_model_configs() -> dict:
    base = {m["id"]: dict(m) for m in _DEFAULT_MODEL_CONFIGS}
    if _MODEL_CONFIGS_PATH.exists():
        try:
            saved = json.loads(_MODEL_CONFIGS_PATH.read_text(encoding="utf-8"))
            for m in saved:
                mid = m.get("id")
                if not mid:
                    continue
                merged = dict(base.get(mid, {}))
                merged.update(m)
                merged.setdefault("model", mid)
                merged.setdefault("protocol", "anthropic")
                merged.setdefault("name", mid)
                merged.setdefault("icon", merged["name"][:1].upper() or "M")
                merged.setdefault("color", "#238636")
                merged.setdefault("tags", ["Custom"])
                merged.setdefault("api_key", "")
                merged.setdefault("base_url", "")
                base[mid] = merged
        except Exception:
            pass
    environment_model = os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "")
    if environment_model and not any(
        model_id == environment_model or config.get("model") == environment_model
        for model_id, config in base.items()
    ):
        protocol = os.environ.get("LLM_PROTOCOL", "anthropic").lower()
        api_key = os.environ.get("LLM_API_KEY") or (
            os.environ.get("OPENAI_API_KEY")
            if protocol == "openai"
            else os.environ.get("ANTHROPIC_API_KEY")
        )
        base_url = os.environ.get("LLM_BASE_URL") or (
            os.environ.get("OPENAI_BASE_URL")
            if protocol == "openai"
            else os.environ.get("ANTHROPIC_BASE_URL")
        )
        environment_id = "environment-model"
        base[environment_id] = {
            "id": environment_id,
            "name": environment_model,
            "model": environment_model,
            "protocol": protocol,
            "icon": "E",
            "color": "#238636",
            "tags": ["Environment"],
            "api_key": api_key or "",
            "base_url": base_url or "",
            "source": "environment",
        }
    return base


def _save_model_configs(configs: dict):
    _MODEL_CONFIGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MODEL_CONFIGS_PATH.write_text(
        json.dumps(
            [config for config in configs.values() if config.get("source") != "environment"],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8"
    )


def _load_active_model_id() -> str:
    try:
        value = json.loads(_ACTIVE_MODEL_PATH.read_text(encoding="utf-8"))
        return str(value.get("model_id", "")).strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def _save_active_model_id(model_id: str) -> None:
    _ACTIVE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ACTIVE_MODEL_PATH.write_text(
        json.dumps({"model_id": model_id}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _mask_key(key: str) -> str:
    if len(key) > 8:
        return key[:4] + "*" * (len(key) - 8) + key[-4:]
    return "*" * len(key)


MODEL_CONFIGS: dict = _load_model_configs()
_requested_config = os.environ.get("MODEL_CONFIG_ID", "")
_saved_config = _load_active_model_id()
_requested_model = (
    _requested_config if _requested_config in MODEL_CONFIGS
    else _saved_config if _saved_config in MODEL_CONFIGS
    else os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "")
)
ACTIVE_MODEL_ID: str = next(
    (mid for mid, cfg in MODEL_CONFIGS.items() if mid == _requested_model or cfg.get("model") == _requested_model),
    next(iter(MODEL_CONFIGS), ""),
)


def _apply_model(model_id: str) -> bool:
    global ACTIVE_MODEL_ID
    cfg = MODEL_CONFIGS.get(model_id)
    if not cfg:
        return False
    ACTIVE_MODEL_ID = model_id
    provider_model = cfg.get("model") or model_id
    protocol = cfg.get("protocol", "anthropic")
    os.environ["MODEL_CONFIG_ID"] = model_id
    os.environ["LLM_MODEL"] = provider_model
    os.environ["LLM_PROTOCOL"] = protocol
    os.environ["ANTHROPIC_MODEL"] = provider_model
    if cfg.get("api_key"):
        os.environ["LLM_API_KEY"] = cfg["api_key"]
        os.environ["ANTHROPIC_API_KEY"] = cfg["api_key"]
    else:
        os.environ.pop("LLM_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
    if cfg.get("base_url"):
        os.environ["LLM_BASE_URL"] = cfg["base_url"]
        os.environ["ANTHROPIC_BASE_URL"] = cfg["base_url"]
    else:
        os.environ.pop("LLM_BASE_URL", None)
        os.environ.pop("ANTHROPIC_BASE_URL", None)
    return True


if ACTIVE_MODEL_ID:
    _apply_model(ACTIVE_MODEL_ID)


class SettingsSelectRequest(BaseModel):
    model_id: str


class ModelConfigUpdate(BaseModel):
    name: Optional[str] = None
    model: Optional[str] = None
    protocol: Optional[Literal["anthropic", "openai"]] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None


class ModelConfigCreate(BaseModel):
    name: str
    model: str
    protocol: Literal["anthropic", "openai"]
    api_key: str = ""
    base_url: str = ""


class ModelLatencyRequest(BaseModel):
    base_url: str


class ModelProbeRequest(BaseModel):
    protocol: Literal["anthropic", "openai"]
    base_url: str
    api_key: str = ""
    model_config_id: Optional[str] = None


class ModelConnectionRequest(ModelProbeRequest):
    model: str


def _public_model(cfg: dict) -> dict:
    return {
        "id": cfg["id"],
        "name": cfg["name"],
        "model": cfg.get("model", cfg["id"]),
        "protocol": cfg.get("protocol", "anthropic"),
        "icon": cfg["icon"],
        "color": cfg["color"],
        "tags": cfg["tags"],
        "api_key_masked": _mask_key(cfg.get("api_key", "")),
        "base_url": cfg.get("base_url", ""),
        "has_key": bool(cfg.get("api_key", "")),
    }


def _new_model_id(name: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in name).strip("-")
    base = f"custom-{slug or 'model'}"
    candidate = base
    suffix = 2
    while candidate in MODEL_CONFIGS:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


@app.get("/api/settings")
async def get_settings():
    models_out = [_public_model(cfg) for cfg in MODEL_CONFIGS.values()]
    return {
        "active_model": ACTIVE_MODEL_ID,
        "current_model": ACTIVE_MODEL_ID,
        "models": models_out,
    }


@app.post("/api/settings/select")
async def select_model_endpoint(s: SettingsSelectRequest):
    ok = _apply_model(s.model_id)
    if ok:
        _save_active_model_id(s.model_id)
    return {"ok": ok, "active_model": ACTIVE_MODEL_ID}


@app.post("/api/settings/models/latency")
async def measure_model_latency(s: ModelLatencyRequest):
    from agent.model_probe import measure_latency
    return await measure_latency(s.base_url)


@app.post("/api/settings/models/discover")
async def discover_model_ids(s: ModelProbeRequest):
    from agent.model_probe import discover_models
    saved_key = MODEL_CONFIGS.get(s.model_config_id or "", {}).get("api_key", "")
    return await discover_models(s.protocol, s.base_url, s.api_key or saved_key)


@app.post("/api/settings/models/test-connection")
async def test_model_connection(s: ModelConnectionRequest):
    from agent.model_probe import test_connection
    saved_key = MODEL_CONFIGS.get(s.model_config_id or "", {}).get("api_key", "")
    return await test_connection(s.protocol, s.base_url, s.api_key or saved_key, s.model)


@app.post("/api/settings/models/{model_id}")
async def update_model_config(model_id: str, s: ModelConfigUpdate):
    if model_id not in MODEL_CONFIGS:
        raise HTTPException(status_code=404, detail="未知模型")
    if MODEL_CONFIGS[model_id].get("source") == "environment":
        raise HTTPException(status_code=403, detail="环境变量模型不可编辑")
    name = s.name.strip() if s.name is not None else MODEL_CONFIGS[model_id]["name"]
    provider_model = s.model.strip() if s.model is not None else MODEL_CONFIGS[model_id].get("model", model_id)
    if not name or not provider_model:
        raise HTTPException(status_code=422, detail="模型名称和模型 ID 不能为空")
    MODEL_CONFIGS[model_id]["name"] = name
    MODEL_CONFIGS[model_id]["model"] = provider_model
    MODEL_CONFIGS[model_id]["icon"] = name[:1].upper()
    if s.protocol is not None:
        MODEL_CONFIGS[model_id]["protocol"] = s.protocol
        MODEL_CONFIGS[model_id]["color"] = "#238636" if s.protocol == "openai" else "#d97757"
        MODEL_CONFIGS[model_id]["tags"] = ["自定义", "OpenAI Compatible" if s.protocol == "openai" else "Anthropic"]
    if s.api_key is not None and s.api_key.strip() and not s.api_key.startswith("*"):
        MODEL_CONFIGS[model_id]["api_key"] = s.api_key.strip()
    if s.base_url is not None:
        MODEL_CONFIGS[model_id]["base_url"] = s.base_url.strip()
    _save_model_configs(MODEL_CONFIGS)
    if model_id == ACTIVE_MODEL_ID:
        _apply_model(model_id)
        _save_active_model_id(model_id)
    return {"ok": True, "model": _public_model(MODEL_CONFIGS[model_id])}


@app.post("/api/settings/models")
async def create_model_config(s: ModelConfigCreate):
    name = s.name.strip()
    provider_model = s.model.strip()
    if not name or not provider_model:
        raise HTTPException(status_code=422, detail="模型名称和模型 ID 不能为空")
    model_id = _new_model_id(name)
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
    }
    MODEL_CONFIGS[model_id] = cfg
    _save_model_configs(MODEL_CONFIGS)
    _apply_model(model_id)
    _save_active_model_id(model_id)
    return {"ok": True, "model": _public_model(cfg)}

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


class FileOperation(BaseModel):
    path: str
    name: Optional[str] = None


@app.post("/api/local/create-folder")
async def create_folder(request: FileOperation):
    """创建文件夹"""
    try:
        target = Path(request.path) / request.name
        target.mkdir(parents=True, exist_ok=True)
        return {"success": True, "path": str(target)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/local/create-file")
async def create_file(request: FileOperation):
    """创建文件"""
    try:
        target = Path(request.path) / request.name
        target.touch()
        return {"success": True, "path": str(target)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/local/delete")
async def delete_item(request: FileOperation):
    """删除文件或文件夹"""
    try:
        target = Path(request.path)
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/local/rename")
async def rename_item(request: FileOperation):
    """重命名"""
    try:
        target = Path(request.path)
        new_path = target.parent / request.name
        target.rename(new_path)
        return {"success": True, "path": str(new_path)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/local/save")
async def save_file(request: dict):
    """保存文件内容"""
    try:
        path = request.get("path")
        content = request.get("content", "")
        Path(path).write_text(content, encoding='utf-8')
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


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
        return FileResponse(str(dist_index))
    return HTMLResponse("<h3>前端未构建，请先运行 cd src/web && npm run build</h3>", status_code=404)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding='utf-8')
    port = int(os.getenv("PORT", 7788))
    print(f"\n[启动] GitHub探索者")
    print(f"[地址] http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port)
