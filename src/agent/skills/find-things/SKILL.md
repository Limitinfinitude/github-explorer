---
name: find-things
description: 发现并推荐项目、库、技能、工具：扫描本机已装技能 + 搜索生态，给出可行动清单。
---

# 发现与推荐（find-things）

用户想找/推荐项目、库、技能或工具时，先发现真实信息再回答，不要只凭模型记忆罗列。

## 步骤

1. **扫描本机已装的 Agent 技能**（run_command，Windows PowerShell 语法）：
   ```
   Get-ChildItem $env:USERPROFILE\.agents\skills, $env:USERPROFILE\.cc-switch\skills, $env:USERPROFILE\.claude\skills -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
   ```
   找到与用户需求相关的技能名（如 pdf、web-search、impeccable 等），稍后作为「本机已有」列出。
2. **用 web_fetch 发现开源项目**（GitHub 搜索 API，返回 JSON，不需要认证）：
   ```
   web_fetch https://api.github.com/search/repositories?q={关键词}&sort=stars&per_page=6
   ```
   从返回的 `items` 里取 `full_name`、`description`、`stargazers_count`、`html_url`。关键词按用户主题构造（如 `pdf processing`、`pdf to markdown`）。
3. **（可选）抓技能生态目录**补充技能推荐：
   - `https://www.skills.sh/`（技能市场）
   - 官方技能仓库：`https://github.com/anthropics/skills`、`https://github.com/openai/skills` 的 README
4. **输出可行动清单**，按此结构：
   - **本机已装的相关技能**（目录名 + 一句话，来自步骤 1）
   - **生态推荐项目/库**（名称 / 用途 / star 数 / 链接，来自步骤 2）
   - **生态推荐技能**（名称 / 来源 / 安装方式，如 `npx skills add openai/skills@pdf -g -y`）
   - **按场景给一个推荐组合**（如"日常办公用 X、扫描件用 Y、喂大模型用 Z"）

## 约束

- **只读**：不克隆、不安装任何东西；用户明确要求"装/克隆/跑起来"时才执行。
- GitHub API 未认证限流（HTTP 403）时：换 `web_fetch https://raw.githubusercontent.com/.../README.md` 或说明限流，不编造 star 数。
- 网络失败或搜索无结果：如实说明，不编造项目和链接。
