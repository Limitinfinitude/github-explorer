"""模型配置管理 — 从 main.py 拆出的独立模块

职责：加载/保存模型配置、激活模型选择、公开视图脱敏。
本模块以自身的全局状态为唯一数据源，API 层通过模块属性读写，
这样运行中切换模型不会引入跨模块的副本不一致。
"""
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_MODEL_CONFIGS_PATH = DATA_DIR / "model_configs.json"
_ACTIVE_MODEL_PATH = DATA_DIR / "active_model.json"

DEFAULT_MODEL_CONFIGS = [
    {"id": "mimo-v2.5",       "name": "Mimo v2.5",       "model": "mimo-v2.5",       "protocol": "anthropic", "icon": "M", "color": "#8250df", "tags": ["1M", "识图"],  "api_key": "", "base_url": "https://api.xiaomimimo.com/anthropic", "context_window": "1M"},
    {"id": "grok-4.5",        "name": "Grok 4.5",        "model": "grok-4.5",        "protocol": "anthropic", "icon": "G", "color": "#cf222e", "tags": ["极速"],        "api_key": "", "base_url": "https://zz.aiapi2025.top", "context_window": "256k"},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "model": "claude-sonnet-5", "protocol": "anthropic", "icon": "C", "color": "#0550ae", "tags": ["最新"],        "api_key": "", "base_url": "https://zz.aiapi2025.top", "context_window": "200k"},
]

DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000


def parse_context_window(value) -> int:
    """解析 context_window 配置：'1M'→1_000_000、'256k'→256_000、纯数字按 token；非法回退 128k。"""
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    try:
        if text.endswith("m"):
            return int(float(text[:-1]) * 1_000_000)
        if text.endswith("k"):
            return int(float(text[:-1]) * 1_000)
        return int(text)
    except (ValueError, TypeError):
        return DEFAULT_CONTEXT_WINDOW_TOKENS


def _load_model_configs() -> dict:
    base = {m["id"]: dict(m) for m in DEFAULT_MODEL_CONFIGS}
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
                merged.setdefault("thinking_effort", "off")
                merged.setdefault("context_window", "128k")
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


def _normalize_thinking_effort(value: object) -> str:
    """旧档位（on/deep）迁移到新档位（high/max）。"""
    return {"on": "high", "deep": "max"}.get(str(value or ""), str(value or "off"))


def public_model(cfg: dict) -> dict:
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
        "thinking_effort": _normalize_thinking_effort(cfg.get("thinking_effort", "off")),
        "context_window": cfg.get("context_window", "128k"),
        "context_window_tokens": parse_context_window(cfg.get("context_window")),
    }


def new_model_id(name: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in name).strip("-")
    base = f"custom-{slug or 'model'}"
    candidate = base
    suffix = 2
    while candidate in MODEL_CONFIGS:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def apply_model(model_id: str) -> bool:
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
    thinking_effort = _normalize_thinking_effort(cfg.get("thinking_effort", "off"))
    if thinking_effort in {"off", "high", "max"}:
        os.environ["LLM_THINKING_EFFORT"] = thinking_effort
    os.environ["LLM_CONTEXT_WINDOW_TOKENS"] = str(parse_context_window(cfg.get("context_window")))
    return True


def _compute_active_model_id() -> str:
    requested_config = os.environ.get("MODEL_CONFIG_ID", "")
    saved_config = _load_active_model_id()
    requested_model = (
        requested_config if requested_config in MODEL_CONFIGS
        else saved_config if saved_config in MODEL_CONFIGS
        else os.environ.get("LLM_MODEL") or os.environ.get("ANTHROPIC_MODEL", "")
    )
    return next(
        (mid for mid, cfg in MODEL_CONFIGS.items() if mid == requested_model or cfg.get("model") == requested_model),
        next(iter(MODEL_CONFIGS), ""),
    )


def get_active_model_id() -> str:
    return ACTIVE_MODEL_ID


MODEL_CONFIGS: dict = _load_model_configs()
ACTIVE_MODEL_ID: str = _compute_active_model_id()

if ACTIVE_MODEL_ID:
    apply_model(ACTIVE_MODEL_ID)
