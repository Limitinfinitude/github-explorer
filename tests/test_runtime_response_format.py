from agent.runtime.response_format import format_final_response


def test_final_response_uses_stable_chinese_sections_without_duplicate_text():
    result = format_final_response(
        "Created the file.\n\nCreated the file.\n\nThe user asked me to create it. Done.\n\n现在我需要告诉用户已经完成。\n\n完美！我已经完成。\n\n用户明确要求简洁回复。",
        {
            "changed_files": ["src/app.py"],
            "verification": [{"command": "pytest -q", "success": True}],
            "processes": [{"process_id": "p1", "status": "running", "url": "http://127.0.0.1:7788"}],
        },
    )

    assert result.count("Created the file.") == 1
    assert result.count("## 完成结果") == 1
    assert result.count("## 文件变更") == 1
    assert result.count("## 验证") == 1
    assert result.count("## 运行状态") == 1
    assert "src/app.py" in result
    assert "`pytest -q`：通过" in result
    assert "http://127.0.0.1:7788" in result


def test_final_response_hides_echoed_edit_tool_arguments():
    result = format_final_response(
        '[{"path":"cute-cat.html","operation":"write","content":"<html>"}]',
        {"changed_files": ["cute-cat.html"]},
    )

    assert "operation" not in result
    assert "cute-cat.html" in result
    assert "已修改 1 个文件" in result


def test_final_response_removes_chinese_internal_narration():
    result = format_final_response(
        '你好！有什么我可以帮助你的吗？用户说"你好"，这是一个简单的问候。我应该友好地回应，并保持简洁。',
    )

    assert "有什么我可以帮助你的吗？" in result
    assert "用户说" not in result
    assert "我应该" not in result


def test_plain_chat_without_execution_summary_returns_only_the_answer():
    result = format_final_response("你好！有什么我可以帮助你的吗？")

    assert result == "你好！有什么我可以帮助你的吗？"
    assert "完成结果" not in result
    assert "文件变更" not in result


def test_final_response_hides_xml_tool_protocol():
    result = format_final_response(
        '<tool_call><function=edit_files><parameter=edits>[]</parameter></tool_call>',
        {"changed_files": ["cat.html"]},
    )

    assert "<tool_call>" not in result
    assert "<function=edit_files>" not in result


def test_final_response_hides_dsml_tool_protocol():
    result = format_final_response(
        '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="edit_files">payload</｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>',
        {},
    )
    assert "DSML" not in result


def test_protocol_fallback_uses_structured_facts_instead_of_generic_placeholder():
    result = format_final_response(
        '<｜｜DSML｜｜tool_calls>broken payload</｜｜DSML｜｜tool_calls>',
        {
            "changed_files": ["app.py"],
            "verification": [{
                "command": "pytest -q",
                "success": True,
                "cwd": "C:/workspace",
                "python_executable": "C:/workspace/.venv/Scripts/python.exe",
                "returncode": 0,
            }],
        },
    )

    assert "本地操作已执行" not in result
    assert "已修改 1 个文件，1 项验证通过" in result
    assert "C:/workspace" in result
    assert "python.exe" in result


def test_final_response_removes_model_generated_summary_tail():
    result = format_final_response(
        "已完成小猫网页。\n\n**文件变更：**\n- 新建 cat.html\n\n**验证：**\n- 文件存在",
        {"changed_files": ["cat.html"], "verification": [{"path": "cat.html", "success": True}]},
    )

    assert result.count("文件变更") == 1
    assert result.count("验证") == 1


def test_final_response_shows_unverified_acceptance_when_model_answer_is_empty():
    result = format_final_response(
        "",
        {
            "changed_files": ["app.py"],
            "verification": [{"command": "pytest -q", "success": True}],
            "acceptance": [
                {
                    "id": 1,
                    "text": "支持按标签筛选",
                    "status": "unverified",
                    "reason": "缺少明确的完成状态",
                    "evidence": [],
                },
                {
                    "id": 2,
                    "text": "支持 JSON 导入导出",
                    "status": "failed",
                    "reason": "回复明确标记为未完成",
                    "evidence": [],
                },
            ],
        },
    )

    assert "1. [未验证] 支持按标签筛选（缺少明确的完成状态）" in result
    assert "2. [未完成] 支持 JSON 导入导出（回复明确标记为未完成）" in result


def test_empty_execution_reply_uses_all_available_facts():
    result = format_final_response("", {
        "changed_files": ["app.py"],
        "verification": [{"command": "pytest -q", "success": True}],
        "successful_tools": ["edit_files", "verify_project"],
    })

    assert "模型未返回最终说明" in result
    assert "已修改 1 个文件" in result
    assert "1 项验证通过" in result
    assert "已成功执行 2 个操作" in result
