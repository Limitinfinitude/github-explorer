---
name: repo-intro
description: 获取 GitHub 仓库的真实信息（元数据/README）并介绍其用途、语言、规模。
---

# 仓库介绍（repo-intro）

用户想了解某个 GitHub 仓库时，用 web_fetch 获取真实信息，不要只凭模型记忆。

## 步骤

1. 从 URL 提取 owner/repo：`https://github.com/bfontaine/term2048` → `bfontaine/term2048`（用户只给名字没给 URL 时，可先用 web_search 或直接按 `https://github.com/{owner}/{repo}` 构造）。
2. 获取仓库元数据（JSON）：
   ```
   web_fetch https://api.github.com/repos/{owner}/{repo}
   ```
   从中取：`description`（一句话用途）、`stargazers_count`、`language`、`forks_count`、`default_branch`、`created_at`、`homepage`。
3. 获取 README 看细节（可选但推荐）：
   ```
   web_fetch https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/README.md
   ```
   `default_branch` 取第 2 步返回的字段（通常 `main` 或 `master`）。README 太长时只取开头部分。
4. 用 3-5 句介绍：这是什么、做什么用、用什么语言、规模（star/fork/创建时间）、典型用途，附上原始链接。

## 约束

- **只读操作**：绝不克隆仓库、绝不修改任何文件、绝不做写操作。用户后续要求"克隆/分析/修改"时再走对应流程。
- GitHub API 未认证限流（HTTP 403）时：换 `https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/README.md` 直接抓 README，或说明限流并基于已获取信息回答。
- 网络失败或仓库不存在（404）：如实说明，不要编造仓库信息。
