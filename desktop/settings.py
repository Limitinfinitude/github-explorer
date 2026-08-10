"""
设置管理模块
"""

import json
from pathlib import Path
from typing import Optional, Dict

CONFIG_DIR = Path("./config")
CONFIG_FILE = CONFIG_DIR / "settings.json"


class Settings:
    """应用设置管理"""

    DEFAULT = {
        "api": {
            "provider": "anthropic",  # anthropic / openai / custom
            "api_key": "",
            "base_url": "",
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 2000,
            "temperature": 0.7
        },
        "github": {
            "token": ""
        },
        "ui": {
            "theme": "dark",
            "language": "zh-CN",
            "sidebar_collapsed": False,
            "detail_panel_open": True
        },
        "general": {
            "clone_dir": "./cloned_repos",
            "auto_detect_env": True,
            "save_history": True
        }
    }

    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._settings = self._load()

    def _load(self) -> Dict:
        """加载设置"""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # 合并默认值
                return self._merge(self.DEFAULT, saved)
            except:
                pass
        return self.DEFAULT.copy()

    def _merge(self, default: Dict, override: Dict) -> Dict:
        """深度合并字典"""
        result = default.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge(result[key], value)
            else:
                result[key] = value
        return result

    def save(self):
        """保存设置"""
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self._settings, f, indent=2, ensure_ascii=False)

    def get(self, path: str, default=None):
        """
        获取设置值

        路径格式: "api.model" 或 "github.token"
        """
        keys = path.split(".")
        value = self._settings
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, path: str, value):
        """
        设置值

        路径格式: "api.model" 或 "github.token"
        """
        keys = path.split(".")
        target = self._settings
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value
        self.save()

    def get_all(self) -> Dict:
        """获取所有设置"""
        return self._settings.copy()

    def reset(self):
        """重置为默认设置"""
        self._settings = self.DEFAULT.copy()
        self.save()

    def get_api_config(self) -> Dict:
        """获取API配置"""
        return {
            "api_key": self.get("api.api_key") or self.get("github.token"),
            "base_url": self.get("api.base_url"),
            "model": self.get("api.model"),
            "max_tokens": self.get("api.max_tokens"),
            "temperature": self.get("api.temperature")
        }


# 全局实例
settings = Settings()
