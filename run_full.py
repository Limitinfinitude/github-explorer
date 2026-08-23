#!/usr/bin/env python3
"""GitHub Explorer 本地主应用启动入口。"""
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def apply_environment_baseline() -> None:
    """评测/运行环境基线：go 默认 proxy 与 sumdb 在国内网络不可达，
    Agent 的子进程（go 命令）继承本进程环境变量。"""
    os.environ.setdefault("GOPROXY", "direct")
    os.environ.setdefault("GONOSUMDB", "*")
    os.environ.setdefault("GONOSUMCHECK", "1")


def main() -> None:
    import uvicorn
    from src.main import app

    apply_environment_baseline()
    port = int(os.getenv("PORT", "7788"))
    print("=" * 56)
    print("  GitHub Explorer - Local Agent")
    print(f"  http://127.0.0.1:{port}")
    print("=" * 56)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
