"""会话分享路由：生成公开可访问的静态 HTML 快照。

- POST /api/chats/{session_id}/share —— 渲染当前会话为单文件 HTML，返回可分享 URL
- GET  /share/{token}               —— 返回该 HTML（无需鉴权，供外部访问）
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent.memory import memory
from share_render import write_share

router_share = APIRouter()

ROOT_DIR = Path(__file__).parent.parent
SHARE_DIR = ROOT_DIR / "data" / "shares"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")


class ShareRequest(BaseModel):
    title: str = ""


def _default_title(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user" and str(msg.get("content") or "").strip():
            text = " ".join(str(msg["content"]).split())
            return text[:36] + ("…" if len(text) > 36 else "")
    return "会话分享"


@router_share.post("/api/chats/{session_id}/share")
async def share_chat(session_id: str, payload: ShareRequest, request: Request):
    messages = memory.get_chat_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, detail="该会话没有可分享的消息")
    title = (payload.title or "").strip() or _default_title(messages)
    token = secrets.token_urlsafe(12).replace("=", "")
    write_share(messages, title, SHARE_DIR, token)
    base = str(request.base_url).rstrip("/")
    return {
        "success": True,
        "token": token,
        "title": title,
        "url": f"{base}/share/{token}",
        "path": str(SHARE_DIR / f"{token}.html"),
    }


@router_share.api_route("/share/{token}", methods=["GET", "HEAD"])
async def get_share(token: str):
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="分享不存在")
    target = SHARE_DIR / f"{token}.html"
    if not target.is_file():
        raise HTTPException(status_code=404, detail="分享不存在或已过期")
    return FileResponse(target, media_type="text/html; charset=utf-8")


@router_share.delete("/api/chats/share/{token}")
async def delete_share(token: str):
    """撤回分享（删除已生成的 HTML 快照）。"""
    if not _TOKEN_RE.match(token):
        raise HTTPException(status_code=404, detail="分享不存在")
    target = SHARE_DIR / f"{token}.html"
    if target.is_file():
        target.unlink()
    return {"success": True}
