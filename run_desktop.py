#!/usr/bin/env python3
"""
GitHub Explorer 桌面应用启动脚本
"""

import sys
import os
import time
import socket
import subprocess
from pathlib import Path

# Windows编码修复
if sys.platform == "win32":
    os.system("chcp 65001 >nul")
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))

# 修复 SSL_CERT_FILE 指向不存在文件的问题（Anaconda 常见）
ssl_cert = os.environ.get("SSL_CERT_FILE")
if ssl_cert and not Path(ssl_cert).is_file():
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()


def wait_for_server(port=7788, timeout=15):
    """等待后端服务就绪"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            sock.connect(('127.0.0.1', port))
            sock.close()
            return True
        except:
            time.sleep(0.3)
    return False


def main():
    """启动桌面应用"""
    try:
        import webview
    except ImportError:
        print("正在安装依赖...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pywebview"])
        import webview

    # 用子进程启动后端（避免线程与 pywebview 冲突）
    print("正在启动后端服务...")
    server_proc = subprocess.Popen(
        [sys.executable, "-c", """
import sys, os
sys.path.insert(0, '.')
sys.path.insert(0, 'src')
from pathlib import Path
ssl_cert = os.environ.get('SSL_CERT_FILE')
if ssl_cert and not Path(ssl_cert).is_file():
    import certifi
    os.environ['SSL_CERT_FILE'] = certifi.where()
from dotenv import load_dotenv
load_dotenv(override=True)
import uvicorn
from src.main import app
uvicorn.run(app, host='127.0.0.1', port=7788, log_level='error', access_log=False)
"""],
        cwd=str(ROOT_DIR),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    )

    if not wait_for_server():
        print("后端服务启动超时，请检查端口 7788 是否被占用")
        server_proc.terminate()
        sys.exit(1)

    print("后端服务已就绪，启动桌面窗口...")
    try:
        from desktop.app import create_app
        create_app()
        webview.start(debug=False)
    finally:
        server_proc.terminate()


if __name__ == "__main__":
    main()
