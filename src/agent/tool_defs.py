"""
工具定义 — 结构化工具列表，供 LLM Tool Use 调用。

架构设计（任务生命周期）：
  1. 感知阶段（Pre-computation）：Repo URL 变更时自动触发，结果写入 State
  2. 决策阶段：LLM 根据 State 中的元数据决定行动
  3. 执行阶段：基础工具执行操作
  4. 验证阶段：Critic 审查 + 环境门控，失败则回退重试

关键设计：
- SenseState 只保留核心元数据（<500 tokens），详细数据按需加载
- 感知工具返回 Schema 校验过的 JSON
- 自愈循环有 Critic 节点打破死循环
- DevOps 门控返回硬约束布尔值
"""

import os
import json

# ========== 基础工具 ==========

BASE_TOOLS = [
    {
        "name": "run_command",
        "description": "执行一条 shell 命令（PowerShell on Windows, bash on Linux/Mac）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "cwd": {"type": "string", "description": "工作目录（可选）"},
                "timeout": {"type": "integer", "description": "超时秒数（可选，默认 60）", "default": 60}
            },
            "required": ["command"]
        }
    },
    {
        "name": "clone_repo",
        "description": "克隆一个 GitHub 仓库到本地。传入 owner/repo 格式。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库路径，如 'octocat/Hello-World'"},
                "target_dir": {"type": "string", "description": "目标目录（可选）"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "search_github",
        "description": "搜索 GitHub 仓库。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "language": {"type": "string", "description": "编程语言筛选（可选）"},
                "limit": {"type": "integer", "description": "返回数量（默认 10）", "default": 10}
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_repo_info",
        "description": "获取一个 GitHub 仓库的详细信息。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库路径"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "read_file",
        "description": "读取本地文件内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "list_directory",
        "description": "列出目录内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "detect_project",
        "description": "检测项目类型（语言、包管理器等）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "项目目录路径"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "install_deps",
        "description": "根据项目类型自动安装依赖。",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_path": {"type": "string", "description": "项目目录路径"}
            },
            "required": ["project_path"]
        }
    },
]

# ========== 感知工具（Agent 模式下可用）==========
# 返回结构化 JSON，Schema 校验后写入 State

AGENT_TOOLS = [
    {
        "name": "sense_repo_health",
        "description": "预计算：评估项目健康度。返回结构化数据。当检测到新 Repo URL 时系统自动触发。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库路径"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "sense_architecture",
        "description": "预计算：分析代码架构和设计模式。返回结构化数据。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库路径"},
                "local_path": {"type": "string", "description": "本地路径（已克隆时使用）"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "sense_issues",
        "description": "预计算：分析 Issue 痛点。返回结构化数据。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库路径"}
            },
            "required": ["repo"]
        }
    },
    {
        "name": "sense_devops",
        "description": "门控：检查 CI/CD 状态。返回 {passed: bool, reason: string}。失败时系统强制回退，不允许输出给用户。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "仓库路径"}
            },
            "required": ["repo"]
        }
    },
]


def get_tools(agent_mode: bool = False) -> list[dict]:
    tools = list(BASE_TOOLS)
    if agent_mode:
        tools.extend(AGENT_TOOLS)
    return tools


# ========== 轻量级共享状态 ==========

# Schema 定义：感知工具必须返回符合这些 Schema 的 JSON
SENSE_SCHEMAS = {
    "repo_health": {
        "required": ["score", "maintenance_status", "learning_value"],
        "types": {"score": (int, float), "maintenance_status": str, "learning_value": str},
    },
    "architecture": {
        "required": ["patterns", "tech_stack"],
        "types": {"patterns": list, "tech_stack": list},
    },
    "issues": {
        "required": ["pain_points", "suggested_solutions"],
        "types": {"pain_points": list, "suggested_solutions": list},
    },
    "devops": {
        "required": ["passed"],
        "types": {"passed": bool},
    },
}


class SenseState:
    """
    轻量级状态容器 — 只保留核心元数据（<500 tokens）。
    自动持久化到 SQLite，跨重启保留。
    """
    def __init__(self, session_id: str = "default"):
        self._session_id = session_id
        self.current_repo = ""
        self.local_path = ""

        self.health_score = None
        self.health_level = ""
        self.tech_stack = []
        self.patterns = []
        self.devops_passed = None
        self.devops_reason = ""

        self._detail_cache = {}

        self.errors = []
        self.fix_attempts = 0
        self.critic_feedback = ""

    def set_health(self, data: dict):
        self.health_score = data.get("score", 0)
        self.health_level = data.get("learning_value", "未知")
        self._detail_cache["health"] = data
        self._save()

    def set_architecture(self, data: dict):
        self.tech_stack = data.get("tech_stack", [])
        self.patterns = data.get("patterns", [])
        self._detail_cache["architecture"] = data
        self._save()

    def set_issues(self, data: dict):
        self._detail_cache["issues"] = data
        self._save()

    def set_devops(self, data: dict):
        self.devops_passed = data.get("passed", None)
        self.devops_reason = data.get("reason", "")
        self._detail_cache["devops"] = data
        self._save()

    def get_detail(self, key: str) -> dict:
        return self._detail_cache.get(key, {})

    def to_context(self) -> str:
        parts = []
        if self.health_score is not None:
            parts.append(f"健康度: {self.health_score}/10 ({self.health_level})")
        if self.tech_stack:
            parts.append(f"技术栈: {', '.join(self.tech_stack[:5])}")
        if self.patterns:
            parts.append(f"架构模式: {', '.join(self.patterns[:3])}")
        if self.devops_passed is not None:
            status = "✅ 通过" if self.devops_passed else f"❌ 失败: {self.devops_reason}"
            parts.append(f"CI/CD: {status}")
        if self.critic_feedback:
            parts.append(f"Critic 审查: {self.critic_feedback}")
        return "\n".join(parts) if parts else ""

    def _to_dict(self) -> dict:
        return {
            "current_repo": self.current_repo,
            "local_path": self.local_path,
            "health_score": self.health_score,
            "health_level": self.health_level,
            "tech_stack": self.tech_stack,
            "patterns": self.patterns,
            "devops_passed": self.devops_passed,
            "devops_reason": self.devops_reason,
            "detail_cache": self._detail_cache,
        }

    def _from_dict(self, d: dict):
        self.current_repo = d.get("current_repo", "")
        self.local_path = d.get("local_path", "")
        self.health_score = d.get("health_score")
        self.health_level = d.get("health_level", "")
        self.tech_stack = d.get("tech_stack", [])
        self.patterns = d.get("patterns", [])
        self.devops_passed = d.get("devops_passed")
        self.devops_reason = d.get("devops_reason", "")
        self._detail_cache = d.get("detail_cache", {})

    def _save(self):
        try:
            _state_db().execute(
                "INSERT OR REPLACE INTO sense_states (session_id, data, updated_at) VALUES (?, ?, datetime('now'))",
                (self._session_id, json.dumps(self._to_dict(), ensure_ascii=False, default=str)),
            )
            _state_db().commit()
        except Exception:
            pass


def _state_db():
    """获取/初始化状态持久化 DB 连接"""
    global _db_conn
    if _db_conn is None:
        import sqlite3
        from pathlib import Path
        db_path = Path(__file__).parent.parent.parent / "data" / "memory.db"
        _db_conn = sqlite3.connect(str(db_path), check_same_thread=False)
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("""
            CREATE TABLE IF NOT EXISTS sense_states (
                session_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        _db_conn.commit()
    return _db_conn


_db_conn = None
_states: dict[str, SenseState] = {}


def get_state(session_id: str) -> SenseState:
    if session_id not in _states:
        state = SenseState(session_id)
        try:
            row = _state_db().execute(
                "SELECT data FROM sense_states WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row:
                state._from_dict(json.loads(row[0]))
        except Exception:
            pass
        _states[session_id] = state
    return _states[session_id]


# ========== 工具执行器 ==========

async def execute_tool(name: str, args: dict, session_id: str = "default") -> dict:
    """根据工具名称和参数，执行对应的工具函数。"""
    state = get_state(session_id)

    # --- 基础工具 ---
    if name in ("run_command", "clone_repo", "search_github", "fetch_repo_info", "detect_project", "install_deps"):
        from agent.tools import (
            run_command, clone_repo, search_github,
            fetch_repo_info, detect_project, install_deps,
        )
        try:
            if name == "run_command":
                return run_command(cmd=args["command"], cwd=args.get("cwd"), timeout=args.get("timeout", 60))
            elif name == "clone_repo":
                result = clone_repo(repo=args["repo"], target_dir=args.get("target_dir"))
                if result.get("success") or not result.get("error"):
                    state.current_repo = args["repo"]
                    state.local_path = result.get("path", "")
                return result
            elif name == "search_github":
                return search_github(query=args["query"], language=args.get("language", ""), limit=args.get("limit", 10))
            elif name == "fetch_repo_info":
                return fetch_repo_info(repo=args["repo"])
            elif name == "detect_project":
                return detect_project(repo_path=args["path"])
            elif name == "install_deps":
                from agent.tools import detect_project as _detect
                project_info = _detect(args["project_path"])
                return install_deps(project_info=project_info, cwd=args["project_path"])
        except Exception as e:
            return {"success": False, "output": f"工具执行错误: {str(e)}"}

    # --- 文件操作 ---
    # 统一走 harness 的会话工作区边界（resolve）：越界直接拒绝，不再裸 open。
    # 未绑定工作区时回退到进程 cwd（保持轻量模式的无绑定语义）。
    elif name in ("read_file", "list_directory"):
        path = str(args.get("path") or ".")
        try:
            from routes_agent import get_local_agent_services
            from agent.runtime.workspace import WorkspaceError

            services = get_local_agent_services()
            try:
                resolved = services.workspaces.resolve(session_id, path)
            except WorkspaceError:
                return {"success": False, "output": f"路径超出工作区范围: {path}"}
        except Exception:
            resolved = os.path.abspath(path)
        path = str(resolved)

        if name == "read_file":
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            return {"success": True, "content": content, "size": len(content)}

        entries = []
        for entry in os.scandir(path):
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else 0,
            })
        entries.sort(key=lambda x: (x["type"] != "dir", x["name"]))
        return {"success": True, "entries": entries, "count": len(entries)}

    # --- 感知工具（Schema 校验 + 写入 State）---
    elif name.startswith("sense_"):
        return await _execute_sense_tool(name, args, state)

    else:
        return {"success": False, "output": f"未知工具: {name}"}


# ========== 自愈循环 ==========

MAX_FIX_ATTEMPTS = 3


import re as _re

# 规则表 — 正则优先匹配，命中则跳过 LLM 调用
_IMPOSSIBLE_PATTERNS = [
    r"PermissionError|Access is denied|拒绝访问",
    r"No space left on device|磁盘空间不足|disk full",
    r"Network is unreachable|getaddrinfo failed|Name or service not known",
    r"SSL: CERTIFICATE_VERIFY_FAILED",
    r"OSError: \[WinError 5\]",
]
_ENV_ERROR_PATTERNS = [
    r"is not recognized as.*(?:internal|external) command",
    r"'(\w+)' n'est pas reconnu",
    r"command not found",
    r"No such file or directory.*(?:git|python|node|npm|pip)",
    r"Cannot find.*git\.exe",
]
_DEP_ERROR_PATTERNS = [
    r"ModuleNotFoundError: No module named",
    r"ImportError:",
    r"Cannot find module '",
    r"npm ERR! 404",
    r"pip.*not found",
]


async def critique_and_retry(state: SenseState, error_output: str, original_code: str = "") -> dict:
    """
    Critic 节点：分析错误，打破重试死循环。
    规则优先（正则匹配），未命中时 LLM 兜底。
    """
    from agent.llm import call_llm

    state.fix_attempts += 1
    state.errors.append(error_output[:500])

    # --- 规则优先分类（不调 LLM）---
    for pattern in _IMPOSSIBLE_PATTERNS:
        if _re.search(pattern, error_output, _re.IGNORECASE):
            msg = f"检测到不可修复的系统错误（规则匹配: {pattern.split('|')[0]}）"
            state.critic_feedback = msg
            return {
                "success": False,
                "output": msg,
                "should_retry": False,
                "attempt": state.fix_attempts,
                "error_type": "impossible",
            }

    for pattern in _ENV_ERROR_PATTERNS:
        if _re.search(pattern, error_output, _re.IGNORECASE):
            msg = f"环境工具未安装（规则匹配），请先安装对应程序。原始错误：{error_output[:200]}"
            state.critic_feedback = msg
            return {
                "success": True,
                "output": msg,
                "should_retry": True,
                "attempt": state.fix_attempts,
                "error_type": "env_error",
            }

    for pattern in _DEP_ERROR_PATTERNS:
        if _re.search(pattern, error_output, _re.IGNORECASE):
            msg = f"依赖缺失（规则匹配），需安装相关依赖包。原始错误：{error_output[:200]}"
            state.critic_feedback = msg
            return {
                "success": True,
                "output": msg,
                "should_retry": True,
                "attempt": state.fix_attempts,
                "error_type": "dep_error",
            }

    if state.fix_attempts > MAX_FIX_ATTEMPTS:
        return {
            "success": False,
            "output": f"已重试 {MAX_FIX_ATTEMPTS} 次仍失败。最后错误: {error_output[:200]}",
            "should_retry": False,
            "error_type": "max_retries",
        }

    # 上下文剪枝：第2次起只给失败摘要，不堆积完整错误历史
    failed_attempts_summary = ""
    if state.fix_attempts >= 2 and len(state.errors) >= 2:
        failed_attempts_summary = "\n".join([
            f"第{i+1}次失败: {err[:150]}" for i, err in enumerate(state.errors[:-1])
        ])

    system = """你是一个严厉的 Code Reviewer。你的任务是判断错误根因并给出精准修复指令。

分类规则（必须选一个）：
- code_error: 代码逻辑错误、语法错误、类型错误
- env_error: 程序未安装（如 git not found、python not found、npm not found）
- dep_error: 依赖库缺失或版本不兼容（如 ModuleNotFoundError、版本冲突）
- config_error: 配置文件错误、权限问题、路径不存在
- impossible: 无法通过代码修复（如网络不通、磁盘已满、权限被系统锁定）

规则：
1. impossible 类型必须明确说明为何无法通过代码/命令修复
2. env_error 必须给出安装命令，不是代码修改
3. 严禁重复上次已经失败的修复方式
4. 回复必须包含 JSON 块：{"type": "...", "reason": "...", "fix_command": "..."}"""

    prompt = f"""## 当前错误
{error_output[:800]}

## 失败的命令/代码
{original_code[:400] if original_code else '（未提供）'}
"""
    if failed_attempts_summary:
        prompt += f"\n## 之前尝试过的方案（均已失败，不要重复）\n{failed_attempts_summary}\n"

    prompt += f"\n## 当前是第 {state.fix_attempts}/{MAX_FIX_ATTEMPTS} 次重试\n\n请输出 JSON 分类块 + 修复说明。"

    review = await call_llm(
        system=system,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=800,
        temperature=0.2,
    )
    state.critic_feedback = review

    # 解析错误类型
    import re
    error_type = "code_error"
    is_impossible = False
    try:
        m = re.search(r'\{[^}]*"type"\s*:\s*"([^"]+)"[^}]*\}', review)
        if m:
            parsed_type = m.group(1)
            error_type = parsed_type
            is_impossible = parsed_type == "impossible"
    except Exception:
        pass

    return {
        "success": True,
        "output": review,
        "should_retry": not is_impossible,
        "attempt": state.fix_attempts,
        "error_type": error_type,
    }


# ========== 感知工具实现 ==========

async def _execute_sense_tool(name: str, args: dict, state: SenseState) -> dict:
    """执行感知工具 — Schema 校验 + 写入 State"""
    from agent.llm import call_llm
    from agent.tools import fetch_repo_info

    repo = args.get("repo", state.current_repo)
    local_path = args.get("local_path", state.local_path)

    try:
        # 获取基础信息
        repo_info = fetch_repo_info(repo) if repo else {}
        info_text = ""
        if repo_info and not repo_info.get("error"):
            r = repo_info
            info_text = f"仓库: {repo}\n描述: {r.get('description','')}\n语言: {r.get('language','')}\nStars: {r.get('stars',0)}\nForks: {r.get('forks',0)}\nIssues: {r.get('open_issues',0)}\n创建: {r.get('created_at','')}\n更新: {r.get('pushed_at','')}"

        if name == "sense_repo_health":
            raw = await _llm_sense(
                "你是项目健康度分析器。只返回 JSON，不要其他内容。",
                f"分析以下项目健康度，返回 JSON：\n\n{info_text or f'仓库: {repo}'}\n\n返回格式：{{\"score\": 8.5, \"maintenance_status\": \"活跃\", \"learning_value\": \"高\", \"risks\": [\"风险1\"]}}"
            )
            result = _validate_and_parse(raw, "repo_health")
            state.set_health(result)

        elif name == "sense_architecture":
            file_tree = _scan_file_tree(local_path) if local_path and os.path.isdir(local_path) else ""
            raw = await _llm_sense(
                "你是代码架构分析器。只返回 JSON，不要其他内容。",
                f"分析以下项目架构，返回 JSON：\n\n{info_text}\n\n文件结构：\n{file_tree or '（未提供）'}\n\n返回格式：{{\"patterns\": [\"MVC\"], \"tech_stack\": [\"FastAPI\"], \"modules\": [{{\"name\": \"api\", \"responsibility\": \"路由处理\"}}], \"mermaid_diagram\": \"graph TD\\nA-->B\"}}"
            )
            result = _validate_and_parse(raw, "architecture")
            state.set_architecture(result)

        elif name == "sense_issues":
            raw = await _llm_sense(
                "你是 Issue 分析器。只返回 JSON，不要其他内容。",
                f"分析以下项目的 Issue 痛点，返回 JSON：\n\n{info_text}\n\n返回格式：{{\"pain_points\": [\"痛点1\"], \"suggested_solutions\": [{{\"approach\": \"方案\", \"difficulty\": \"中\"}}], \"community_health\": \"活跃\"}}"
            )
            result = _validate_and_parse(raw, "issues")
            state.set_issues(result)

        elif name == "sense_devops":
            raw = await _llm_sense(
                "你是 CI/CD 分析器。只返回 JSON，不要其他内容。关键：必须包含 \"passed\" 布尔字段。",
                f"分析以下项目的 CI/CD 状态，返回 JSON：\n\n{info_text}\n\n返回格式：{{\"passed\": true/false, \"has_ci\": true, \"status\": \"通过/失败\", \"reason\": \"原因\", \"recommendations\": [\"建议\"]}}"
            )
            result = _validate_and_parse(raw, "devops")
            # 硬门控：确保 passed 字段存在
            if "passed" not in result:
                result["passed"] = None
                result["reason"] = "无法确定 CI/CD 状态"
            state.set_devops(result)

        else:
            result = {"error": f"未知感知工具: {name}"}

        return {"success": True, "output": result}

    except Exception as e:
        return {"success": False, "output": f"感知工具错误: {str(e)}"}


async def _llm_sense(system: str, prompt: str) -> str:
    """调用 LLM 获取感知结果"""
    from agent.llm import call_llm
    return await call_llm(system=system, messages=[{"role": "user", "content": prompt}], max_tokens=1500, temperature=0.3)


def _validate_and_parse(raw: str, sense_type: str) -> dict:
    """
    Schema 校验 + JSON 解析。
    如果返回不符合 Schema，返回默认值而非崩溃。
    """
    result = _parse_json(raw)

    schema = SENSE_SCHEMAS.get(sense_type, {})
    required = schema.get("required", [])
    types = schema.get("types", {})

    # 检查必需字段
    for field in required:
        if field not in result:
            result[field] = None

    # 检查字段类型（宽松匹配）
    for field, expected_type in types.items():
        if field in result and result[field] is not None:
            if not isinstance(result[field], expected_type):
                # 类型不匹配，尝试转换
                try:
                    if expected_type == bool:
                        result[field] = str(result[field]).lower() in ("true", "1", "yes")
                    elif expected_type == (int, float):
                        result[field] = float(result[field])
                    elif expected_type == list:
                        result[field] = [result[field]] if not isinstance(result[field], list) else result[field]
                    elif expected_type == str:
                        result[field] = str(result[field])
                except (ValueError, TypeError):
                    result[field] = None

    return result


def _parse_json(raw: str) -> dict:
    """解析 LLM 返回的 JSON"""
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        for part in parts[1::2]:
            if part.startswith("json"):
                part = part[4:]
            try:
                return json.loads(part.strip())
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _scan_file_tree(path: str, max_depth: int = 3) -> str:
    """扫描目录树"""
    lines = []
    for root, dirs, files in os.walk(path):
        depth = root.replace(path, "").count(os.sep)
        if depth >= max_depth:
            dirs.clear()
            continue
        indent = "  " * depth
        basename = os.path.basename(root) if depth > 0 else path
        lines.append(f"{indent}{basename}/")
        subindent = "  " * (depth + 1)
        for f in files[:20]:
            lines.append(f"{subindent}{f}")
        if len(files) > 20:
            lines.append(f"{subindent}... ({len(files) - 20} more)")
    return "\n".join(lines[:200])
