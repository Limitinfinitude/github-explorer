"""流程型验收项兜底：体检/评测类任务在验证全过+标完成时放行，行为型仍严格。"""
import json

from agent.runtime.acceptance import WorkProductEvaluator


def _evaluate(criterion: str, response: str, verification: list[dict]):
    evaluator = WorkProductEvaluator()
    result = evaluator.evaluate(
        criteria=[{"id": 1, "text": criterion}],
        response_text=response,
        summary={
            "workspace_root": "C:/proj",
            "changed_files": [],
            "verification": verification,
            "processes": [],
        },
    )
    return result["requirement_coverage"]["items"][0]


def test_flow_criterion_passes_with_successful_verification():
    # r11b buku：体检流程类验收，3 项 unit 验证全过，回复逐条 [完成]
    item = _evaluate(
        criterion="完整评测流程（阶段1 体检 → 阶段2 环境准备 → 阶段3 运行验收）",
        response="[1] [完成] 完整评测流程（阶段1 体检 → 阶段2 环境准备 → 阶段3 运行验收） 全部完成。",
        verification=[{"kind": "unit", "command": "python -m pytest", "success": True}],
    )
    assert item["status"] == "passed"


def test_flow_criterion_still_requires_completion_marker():
    item = _evaluate(
        criterion="完整评测流程（阶段1 体检 → 阶段2 环境准备 → 阶段3 运行验收）",
        response="[1] [未完成] 阶段3 运行验收因环境问题无法完成。",
        verification=[{"kind": "unit", "command": "python -m pytest", "success": True}],
    )
    assert item["status"] == "failed"


def test_behavior_criterion_not_loosened():
    # 行为型（搜索功能）的验证与行为语义无关（http 存活 ≠ 搜索可用）时不放行
    item = _evaluate(
        criterion="支持按关键词搜索笔记",
        response="[1] [完成] 搜索功能已实现。",
        verification=[{"kind": "http", "command": "GET /", "success": True}],
    )
    assert item["status"] == "unverified"
