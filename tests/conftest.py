import os
import sys
from pathlib import Path

# 测试环境禁用 MCP 预热（supervisor 任务启动会 spawn npx server，拖慢/挂起套件）
os.environ.setdefault("GE_DISABLE_MCP_PREWARM", "1")

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
