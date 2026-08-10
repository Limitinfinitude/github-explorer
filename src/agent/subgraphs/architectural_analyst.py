"""
Architectural Analyst（讲解员）子智能体

子图流程：[START] -> scan_structure -> read_key_files -> generate_diagram -> identify_patterns -> [END]

负责：
- 扫描项目文件结构
- 读取关键文件（README、入口、配置）
- 生成 Mermaid 架构图
- 识别设计模式
"""
from langgraph.graph import StateGraph, END
from langsmith import traceable

from ..swarm_state import SwarmState
from ..tools import get_file_tree, read_file_content
from ..llm import call_llm
from ..prompts import (
    ARCHITECT_SYSTEM_PROMPT,
    ARCHITECT_DIAGRAM_PROMPT,
    ARCHITECT_PATTERNS_PROMPT,
)


# ── 关键文件识别规则 ──────────────────────────────────────────────

KEY_FILE_CANDIDATES = [
    # 文档
    "README.md",
    "README.rst",
    "README",
    # Python
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    # Node / JavaScript
    "package.json",
    # Rust
    "Cargo.toml",
    # Go
    "go.mod",
]


def _find_key_files(tree: list[str]) -> list[str]:
    """从文件树中筛选关键文件路径"""
    found = []
    for candidate in KEY_FILE_CANDIDATES:
        if candidate in tree:
            found.append(candidate)

    # 寻找 main 入口文件（优先级：main.* > app.* > index.*）
    for prefix in ("main.", "app.", "index."):
        for path in tree:
            basename = path.rsplit("/", 1)[-1] if "/" in path else path
            if basename.startswith(prefix) and path not in found:
                found.append(path)
                break
        if any(p.startswith(("main.", "app.", "index.")) for p in found):
            break

    return found


def _format_file_tree(tree: list[str]) -> str:
    """将文件树列表格式化为每行一个路径的字符串"""
    return "\n".join(tree)


# ── 节点函数 ──────────────────────────────────────────────────────

@traceable(name="architect_scan_structure")
async def scan_structure_node(state: SwarmState) -> dict:
    """扫描项目文件结构"""
    repo = state["repo"]
    result = get_file_tree(repo)

    if not result.get("success"):
        return {"file_tree": f"[获取文件树失败] {result.get('error', '未知错误')}"}

    tree = result["tree"]
    formatted = _format_file_tree(tree)
    return {"file_tree": formatted}


@traceable(name="architect_read_key_files")
async def read_key_files_node(state: SwarmState) -> dict:
    """读取关键文件内容（README、入口、配置）"""
    repo = state["repo"]
    file_tree_str = state.get("file_tree", "")
    tree_lines = [line.strip() for line in file_tree_str.splitlines() if line.strip()]

    key_files_to_read = _find_key_files(tree_lines)

    key_files: dict[str, str] = {}
    for path in key_files_to_read:
        result = read_file_content(repo, path)
        if result.get("success"):
            key_files[path] = result["content"]
        else:
            key_files[path] = f"[读取失败: {result.get('error', '未知错误')}]"

    return {"project_info": {"key_files": key_files}}


@traceable(name="architect_generate_diagram")
async def generate_diagram_node(state: SwarmState) -> dict:
    """用 LLM 生成 Mermaid 架构图"""
    file_tree = state.get("file_tree", "")
    project_info = state.get("project_info") or {}
    key_files = project_info.get("key_files", {})
    key_files_str = "\n\n".join(
        f"=== {path} ===\n{content}" for path, content in key_files.items()
    )

    prompt = ARCHITECT_DIAGRAM_PROMPT.format(
        file_tree=file_tree,
        key_files=key_files_str,
    )
    response = await call_llm(
        ARCHITECT_SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
    )
    return {"architecture_mermaid": response}


@traceable(name="architect_identify_patterns")
async def identify_patterns_node(state: SwarmState) -> dict:
    """用 LLM 识别设计模式并生成完整分析报告"""
    file_tree = state.get("file_tree", "")
    project_info = state.get("project_info") or {}
    key_files = project_info.get("key_files", {})
    key_files_str = "\n\n".join(
        f"=== {path} ===\n{content}" for path, content in key_files.items()
    )

    prompt = ARCHITECT_PATTERNS_PROMPT.format(
        file_tree=file_tree,
        key_files=key_files_str,
    )
    response = await call_llm(
        ARCHITECT_SYSTEM_PROMPT,
        [{"role": "user", "content": prompt}],
    )

    # 尝试从响应中提取结构化的模式列表
    # LLM 可能返回自然语言，这里做简单的解析兜底
    pattern_list = []
    for line in response.splitlines():
        line = line.strip()
        # 匹配以数字或 - 开头的模式名称行
        if line and (line[0].isdigit() or line.startswith("-") or line.startswith("*")):
            # 去掉前缀标记
            cleaned = line.lstrip("0123456789.-*) ").strip()
            if cleaned:
                pattern_list.append(cleaned)

    return {
        "design_patterns": pattern_list if pattern_list else [response[:200]],
        "response": response,
    }


# ── 子图编译 ──────────────────────────────────────────────────────

def build_architect_graph():
    """
    编译 Architectural Analyst 子图。

    流程：scan_structure -> read_key_files -> generate_diagram -> identify_patterns
    """
    graph = StateGraph(SwarmState)

    graph.add_node("scan_structure", scan_structure_node)
    graph.add_node("read_key_files", read_key_files_node)
    graph.add_node("generate_diagram", generate_diagram_node)
    graph.add_node("identify_patterns", identify_patterns_node)

    graph.set_entry_point("scan_structure")
    graph.add_edge("scan_structure", "read_key_files")
    graph.add_edge("read_key_files", "generate_diagram")
    graph.add_edge("generate_diagram", "identify_patterns")
    graph.add_edge("identify_patterns", END)

    return graph.compile()
