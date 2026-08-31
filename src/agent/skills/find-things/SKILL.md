---
name: find-things
description: 发现并推荐项目、库、技能、工具：扫描本机已装技能 + 多源搜索生态，给出带证据的可行动清单。
---

# 发现与推荐（find-things）

用户想找/推荐项目、库、技能或工具时，先发现真实信息再回答，不要只凭模型记忆罗列。核心纪律：**每个推荐都要有来源和数字（star/最近更新），没有证据的候选不进清单**。

## 步骤

1. **扫描本机已装的 Agent 技能**（run_command，PowerShell）：
   ```
   Get-ChildItem $env:USERPROFILE\.agents\skills, $env:USERPROFILE\.cc-switch\skills, $env:USERPROFILE\.claude\skills -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
   ```
   命中用户需求的技能作为「本机已有」优先推荐（安装成本为零）。

2. **用 `search_github` 工具搜开源项目**（结构化结果：full_name / description / stars / html_url）：
   - 关键词组合 2 组：功能描述（`pdf to markdown`）+ 生态词（`pdf library`）；
   - **质量过滤**：stars < 50 的跳过（除非领域极冷门）；半年无更新的标注"维护不活跃"；
   - 3 个候选都拿不到时再用 `web_search "<关键词> github"` 补充。

3. **用 `web_search` 补非 GitHub 生态**：官方工具页、对比评测文章、技能市场（`skills.sh`）、awesome 列表。识别「谁在推荐它、为什么」比单一 star 数更可信。

4. **（可选）核实关键候选**：对最终推荐的前 1-2 个，用 `web_fetch` 抓其 README 确认功能与用户需求匹配，避免"名字像但不是"。

5. **输出可行动清单**，按此结构：
   - **本机已装的相关技能**（名称 + 一句话 + 为何匹配）
   - **推荐项目/库**（名称 / 用途 / stars / 最近更新 / 链接）
   - **生态资源**（文章/榜单/技能，来源链接）
   - **按场景给一个推荐组合**（如"日常办公用 X、扫描件用 Y、喂大模型用 Z"），说明取舍理由

## 约束

- **只读**：不克隆、不安装；用户明确要求"装/克隆/跑起来"才执行。
- GitHub 未认证限流（403）时：改用 `web_search "<项目名> github stars"` 拿数字，不编造 star 数。
- 搜索无结果：如实说明并给出已排除的路径，不编造项目和链接。
