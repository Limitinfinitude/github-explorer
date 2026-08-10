"""
桌面应用启动器 - 针对Windows 11优化
"""
import sys
import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "src"))

def main():
    import webview

    # Windows优化设置
    if sys.platform == "win32":
        os.environ["PYWEBVIEW_GUI"] = "edgechromium"

    from desktop.app import API
    from desktop.settings import settings

    api = API()

    # 确保目录存在
    (ROOT_DIR / "cloned_repos").mkdir(exist_ok=True)
    (ROOT_DIR / "data").mkdir(exist_ok=True)

    window = webview.create_window(
        title="GitHub Explorer",
        url="http://127.0.0.1:7788",
        js_api=api,
        width=1280,
        height=860,
        min_size=(960, 640),
        resizable=True,
        text_select=True
    )

    api.set_window(window)

    # 尝试启动
    try:
        webview.start(gui="edgechromium", debug=False)
    except Exception:
        try:
            webview.start(gui="mshtml", debug=False)
        except Exception:
            webview.start(debug=False)


if __name__ == "__main__":
    main()
