"""
图片生成模块 - 使用fal.ai GPT Image 2为项目生成配图
"""
import os
import httpx
import base64
from typing import Optional

FAL_KEY = "8acf3c4e-0185-441f-b82c-f6b4ac66f4b9:8034b349329e368eb20701546d509fc8"

async def generate_project_image(project_name: str, description: str, language: str) -> Optional[str]:
    """
    为项目生成配图

    Returns:
        base64编码的图片数据URI，或None
    """
    # 构建提示词 - 生成与项目相关的抽象科技图
    prompt = f"""Create a modern, minimalist tech illustration for a software project called "{project_name}".
Description: {description[:100]}
Primary language: {language}

Style: Clean geometric shapes, subtle gradients, dark background (#0f172a), accent colors matching the tech vibe.
No text, no code, no logos. Abstract representation of the project's concept.
Think: neural networks for AI, connected nodes for web, data flows for analytics."""

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                "https://queue.fal.run/openai/gpt-image-2",
                headers={
                    "Authorization": f"Key {FAL_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "prompt": prompt,
                    "image_size": "landscape_4_3",
                    "quality": "medium",
                    "num_images": 1,
                    "output_format": "png",
                    "sync_mode": True
                }
            )

            if response.status_code == 200:
                data = response.json()
                images = data.get("images", [])
                if images:
                    return images[0].get("url")
            else:
                print(f"Image gen error: {response.status_code} - {response.text[:200]}")

    except Exception as e:
        print(f"Image gen failed: {e}")

    return None


async def get_or_generate_image(project_name: str, description: str, language: str) -> str:
    """获取项目配图，优先使用缓存"""
    # 简单的内存缓存
    cache_key = project_name.replace("/", "_").lower()

    # 生成图片
    url = await generate_project_image(project_name, description or "A software project", language or "Python")
    return url or ""
