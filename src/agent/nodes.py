"""
LangGraph 节点实现

每个节点是一个纯函数：接收 AgentState，返回部分更新。
直接调用 Anthropic SDK，不经过任何上层封装。
"""
import json
from typing import Any

from langsmith import traceable

from .state import AgentState
from .llm import call_llm, call_llm_json
from .prompts import (
    CLASSIFY_PROMPT,
    CHAT_SYSTEM_PROMPT,
    ANALYZE_SYSTEM_PROMPT,
    EXPLAIN_SYSTEM_PROMPT,
    LEARNING_PATH_PROMPT,
    EXECUTE_CONFIRM_PROMPT,
    EXECUTE_SUMMARY_PROMPT,
)
from .tools import (
    run_command,
    detect_project,
    clone_repo,
    install_deps,
    get_run_command,
    fetch_repo_info,
    get_system_info,
)
from .memory import memory


# ========== classify 节点 ==========


@traceable(name="node_classify")
async def classify_node(state: AgentState) -> dict:
    """
    意图分类节点：调用 LLM 判断用户意图。
    输出 intent: chat / analyze / execute
    """
    # 如果已经显式设置了 intent（如 API 直接传入），跳过分类
    if state.get("intent") and state["intent"] in ("chat", "analyze", "execute"):
        return {}

    user_msg = state["user_message"]
    repo = state.get("repo")

    # 简单规则预判 + LLM 兜底
    execute_keywords = ["部署", "安装", "运行", "执行", "clone", "克隆", "启动", "配置环境", "setup"]
    analyze_keywords = ["分析", "解读", "学习路径", "对比", "介绍", "是什么", "怎么用"]

    msg_lower = user_msg.lower()
    if any(kw in msg_lower for kw in execute_keywords) and repo:
        return {"intent": "execute"}
    if any(kw in msg_lower for kw in analyze_keywords):
        return {"intent": "analyze"}

    # LLM 兜底分类
    result = await call_llm_json(
        system=CLASSIFY_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
        max_tokens=50,
        temperature=0.1,
    )

    intent = result.get("intent", "chat")
    if intent not in ("chat", "analyze", "execute"):
        intent = "chat"

    return {"intent": intent}


# ========== chat 节点 ==========


@traceable(name="node_chat")
async def chat_node(state: AgentState) -> dict:
    """
    普通对话节点：结合记忆上下文和项目信息回复用户。
    """
    session_id = state["session_id"]
    user_msg = state["user_message"]
    repo = state.get("repo")

    # 加载对话历史
    history = memory.get_history(session_id, limit=10)

    # 构建系统提示
    system_info = get_system_info()
    system = CHAT_SYSTEM_PROMPT + f"\n\n系统信息: {system_info['os']} {system_info['python']}"

    # 加载项目上下文
    if repo:
        project = memory.get_project(repo)
        if project:
            system += f"\n\n当前项目: {repo}"
            system += f"\n状态: {project.get('status', '未知')}"
            if project.get("local_path"):
                system += f"\n本地路径: {project['local_path']}"

    # 构建消息列表
    messages = []
    for h in history[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_msg})

    response = await call_llm(system=system, messages=messages)

    # 保存到记忆
    memory.add_message(session_id, "user", user_msg, repo)
    memory.add_message(session_id, "assistant", response, repo)

    return {"response": response, "messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": response}]}


# ========== analyze 节点 ==========


@traceable(name="node_analyze")
async def analyze_node(state: AgentState) -> dict:
    """
    项目分析节点：获取项目信息并进行深度分析。
    """
    repo = state.get("repo")
    user_msg = state["user_message"]

    if not repo:
        return {"response": "请先选择一个项目再进行分析。"}

    # 获取项目详情
    repo_info = fetch_repo_info(repo)
    if "error" in repo_info:
        return {"response": f"获取项目信息失败: {repo_info['error']}"}

    # 判断分析类型
    if "学习路径" in user_msg or "学习" in user_msg:
        level = "beginner"
        if "中级" in user_msg or "intermediate" in user_msg:
            level = "intermediate"
        elif "高级" in user_msg or "advanced" in user_msg:
            level = "advanced"

        prompt = LEARNING_PATH_PROMPT.format(
            level=level,
            repo_name=repo_info.get("full_name", repo),
            description=repo_info.get("description", ""),
            language=repo_info.get("language", "未知"),
        )
        response = await call_llm(
            system=ANALYZE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return {"response": response, "analysis_result": {"type": "learning_path", "content": response}}

    elif "对比" in user_msg or "比较" in user_msg:
        # 多项目对比（暂用单项目分析兜底）
        prompt = f"分析项目 {repo} 的优缺点，与同类项目对比。\n\n项目信息:\n{json.dumps(repo_info, ensure_ascii=False, indent=2)}"
        response = await call_llm(system=ANALYZE_SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}])
        return {"response": response, "analysis_result": {"type": "comparison", "content": response}}

    elif "解读" in user_msg or "介绍" in user_msg or "是什么" in user_msg:
        # 通俗解读
        prompt_parts = [f"项目名称：{repo_info.get('full_name', repo)}"]
        prompt_parts.append(f"描述：{repo_info.get('description', '暂无')}")
        prompt_parts.append(f"语言：{repo_info.get('language', '未知')}")
        prompt_parts.append(f"Stars：{repo_info.get('stars', 0):,}")
        prompt_parts.append(f"Forks：{repo_info.get('forks', 0):,}")
        if repo_info.get("topics"):
            prompt_parts.append(f"主题：{', '.join(repo_info['topics'])}")

        prompt = "请解读以下 GitHub 开源项目：\n\n" + "\n".join(prompt_parts)
        response = await call_llm(system=EXPLAIN_SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}])
        return {"response": response, "analysis_result": {"type": "explanation", "content": response}}

    else:
        # 深度分析（结构化 JSON）
        prompt = f"请深度分析这个 GitHub 项目:\n\n{json.dumps(repo_info, ensure_ascii=False, indent=2)}"
        result = await call_llm_json(system=ANALYZE_SYSTEM_PROMPT, messages=[{"role": "user", "content": prompt}])

        if "raw" in result:
            return {"response": result["raw"], "analysis_result": result}

        # 格式化为可读文本
        formatted = _format_analysis(result, repo_info)
        return {"response": formatted, "analysis_result": result}


def _format_analysis(analysis: dict, repo_info: dict) -> str:
    """将结构化分析结果格式化为可读文本"""
    lines = [f"# {repo_info.get('full_name', '项目')} 分析报告\n"]

    if "complexity" in analysis:
        lines.append(f"**复杂度**: {analysis['complexity']}/10")
    if "beginner_friendly" in analysis:
        lines.append(f"**适合初学者**: {'是' if analysis['beginner_friendly'] else '否'}")
    if "learning_value" in analysis:
        lines.append(f"**学习价值**: {analysis['learning_value']}")
    if "active_maintenance" in analysis:
        lines.append(f"**维护状态**: {analysis['active_maintenance']}")

    if "tech_stack" in analysis:
        lines.append(f"\n**技术栈**: {', '.join(analysis['tech_stack'])}")
    if "use_cases" in analysis:
        lines.append(f"\n**适用场景**:")
        for uc in analysis["use_cases"]:
            lines.append(f"- {uc}")
    if "pros" in analysis:
        lines.append(f"\n**优点**:")
        for p in analysis["pros"]:
            lines.append(f"- {p}")
    if "cons" in analysis:
        lines.append(f"\n**缺点**:")
        for c in analysis["cons"]:
            lines.append(f"- {c}")
    if "quick_start" in analysis:
        lines.append(f"\n**快速上手**: {analysis['quick_start']}")

    return "\n".join(lines)


# ========== request_confirm 节点 ==========


@traceable(name="node_request_confirm")
async def request_confirm_node(state: AgentState) -> dict:
    """
    确认请求节点：在执行危险操作前，生成确认问题。
    interrupt_before 会在此节点前暂停，等待用户确认。
    """
    repo = state.get("repo", "未知项目")

    # 如果已被 interrupt 恢复且 confirmed=True，直接通过
    if state.get("confirmed"):
        return {"needs_confirm": False}

    # 生成确认步骤列表
    steps = []
    project = memory.get_project(repo) if repo else None

    if not project or not project.get("local_path"):
        steps.append(f"1. 克隆仓库 {repo}")

    steps.append(f"{'2' if steps else '1'}. 检测项目类型和依赖")
    steps.append(f"{'3' if len(steps) > 1 else '2'}. 安装项目依赖")
    steps.append(f"{'4' if len(steps) > 2 else '3'}. 启动项目")

    steps_text = "\n".join(steps)
    question = EXECUTE_CONFIRM_PROMPT.format(steps=steps_text)

    return {
        "needs_confirm": True,
        "confirm_question": question,
    }


# ========== execute 节点 ==========


@traceable(name="node_execute")
async def execute_node(state: AgentState) -> dict:
    """
    执行节点：顺序执行工具链（克隆 → 检测 → 安装 → 运行）。
    每步结果记录到 execution_steps。
    """
    repo = state.get("repo")
    session_id = state["session_id"]
    steps = []

    if not repo:
        return {
            "response": "请先选择一个项目。",
            "execution_steps": [{"step": "error", "success": False, "output": "未指定项目"}],
        }

    # Step 1: 检查是否已克隆
    project = memory.get_project(repo)
    local_path = None

    if project and project.get("local_path"):
        local_path = project["local_path"]
        steps.append({"step": "clone", "success": True, "output": f"已克隆: {local_path}"})
    else:
        clone_result = clone_repo(repo)
        steps.append({"step": "clone", **clone_result})
        if not clone_result["success"]:
            memory.log_action(session_id, "clone", repo, clone_result["output"], False)
            return {"response": f"克隆失败:\n{clone_result['output']}", "execution_steps": steps}
        local_path = clone_result["path"]
        memory.update_project(repo, local_path=local_path, status="cloned")
        memory.log_action(session_id, "clone", repo, "克隆成功", True)

    # Step 2: 检测项目
    project_info = detect_project(local_path)
    steps.append({"step": "detect", "success": True, "info": project_info})
    memory.update_project(repo, status="analyzed")

    # Step 3: 安装依赖
    install_result = install_deps(project_info, local_path)
    steps.append({"step": "install", **install_result})
    memory.log_action(session_id, "install", repo, install_result["output"][:500], install_result["success"])

    if not install_result["success"]:
        return {
            "response": f"依赖安装失败:\n{install_result['output'][:500]}",
            "execution_steps": steps,
        }

    # Step 4: 获取运行命令（不自动执行，返回给用户）
    run_cmd = get_run_command(project_info)
    steps.append({"step": "run_command", "success": True, "command": run_cmd})

    memory.update_project(repo, status="ready", env_configured=True)
    memory.log_action(session_id, "setup", repo, "环境配置完成", True)

    # 生成总结
    summary_prompt = EXECUTE_SUMMARY_PROMPT.format(steps=json.dumps(steps, ensure_ascii=False, indent=2))
    summary = await call_llm(system="简洁总结执行结果，中文回复。", messages=[{"role": "user", "content": summary_prompt}])

    return {
        "response": summary,
        "execution_steps": steps,
    }


# ========== respond 节点 ==========


@traceable(name="node_respond")
async def respond_node(state: AgentState) -> dict:
    """
    响应节点：格式化最终回复，确保记忆已写入。
    大多数节点已经直接写入记忆，这里做兜底处理。
    """
    response = state.get("response", "")
    if not response:
        response = "处理完成，但没有生成回复。"

    return {"response": response}
