from agent.runtime.acceptance import WorkProductEvaluator


def test_http_liveness_does_not_prove_search_behavior():
    report = WorkProductEvaluator().evaluate(
        criteria=[{"id": 1, "text": "支持中文搜索"}],
        response_text="1. [完成] 中文搜索可用。[证据:check:http]",
        summary={
            "changed_files": ["app.py"],
            "verification": [{"kind": "http", "command": "GET /", "success": True}],
            "processes": [],
        },
    )

    assert report["technical_verification"] == {
        "status": "passed",
        "passed": 1,
        "failed": 0,
        "total": 1,
    }
    assert report["requirement_coverage"]["success"] is False
    assert report["requirement_coverage"]["items"][0]["status"] == "unverified"
    assert report["requirement_coverage"]["items"][0]["evidence"][0] == {
        "type": "check",
        "ref": "http",
        "valid": True,
        "sufficient": False,
    }


def test_behavior_check_proves_search_and_file_proves_page_creation():
    report = WorkProductEvaluator().evaluate(
        criteria=[
            {"id": 1, "text": "提供网页"},
            {"id": 2, "text": "支持中文搜索"},
        ],
        response_text=(
            "1. [完成] 网页已提供。[证据:file:templates/index.html]\n"
            "2. [完成] 中文搜索已验证。[证据:check:unit]"
        ),
        summary={
            "changed_files": ["templates/index.html"],
            "verification": [{"kind": "unit", "command": "pytest", "success": True}],
            "processes": [],
        },
    )

    assert report["requirement_coverage"]["success"] is True
    assert [
        item["status"] for item in report["requirement_coverage"]["items"]
    ] == ["passed", "passed"]


def test_bold_bracketed_numbered_sections_keep_explicit_evidence():
    report = WorkProductEvaluator().evaluate(
        criteria=[{"id": 1, "text": "Provide the page"}],
        response_text=(
            "**[1] Provide the page - [完成]** "
            "[证据:file:templates/index.html]"
        ),
        summary={
            "changed_files": ["templates/index.html"],
            "verification": [],
            "processes": [],
        },
    )

    assert report["requirement_coverage"]["success"] is True
    assert report["requirement_coverage"]["items"][0]["status"] == "passed"


def test_ascii_evidence_marker_is_accepted_for_provider_encoding_stability():
    report = WorkProductEvaluator().evaluate(
        criteria=[{"id": 1, "text": "Provide the page"}],
        response_text="1. [完成] Page is ready. [evidence:file:templates/index.html]",
        summary={
            "changed_files": ["templates/index.html"],
            "verification": [],
            "processes": [],
        },
    )

    assert report["requirement_coverage"]["success"] is True


def test_completed_section_without_marker_uses_deterministic_backed_evidence():
    report = WorkProductEvaluator().evaluate(
        criteria=[{"id": 1, "text": "提供网页"}],
        response_text="1. [完成] 网页已提供。",
        summary={
            "changed_files": ["templates/index.html"],
            "verification": [{"kind": "unit", "success": True}],
            "processes": [],
        },
    )

    item = report["requirement_coverage"]["items"][0]
    assert item["status"] == "passed"
    assert item["evidence"] == [{"type": "file", "ref": "templates/index.html", "valid": True, "auto": True}]


def test_behavior_evidence_does_not_pass_a_semantically_different_reply():
    report = WorkProductEvaluator().evaluate(
        criteria=[{"id": 5, "text": "支持删除书签，并为不存在的 ID 提供明确结果"}],
        response_text="5. [完成] 编辑书签功能已实现。[证据:check:unit]",
        summary={
            "changed_files": ["app.py"],
            "verification": [{"kind": "unit", "command": "pytest", "success": True}],
            "processes": [],
        },
    )

    item = report["requirement_coverage"]["items"][0]
    assert report["requirement_coverage"]["success"] is False
    assert item["status"] == "unverified"
    assert item["reason"] == "完成声明与该需求的核心动作不一致"


def test_local_response_numbering_maps_to_later_ledger_positions_in_order():
    report = WorkProductEvaluator().evaluate(
        criteria=[
            {"id": 3, "text": "适配 390px 宽移动端"},
            {"id": 4, "text": "支持键盘焦点与删除确认"},
            {"id": 5, "text": "JSON 导入失败时展示明确原因"},
        ],
        response_text=(
            "1) 适配 390px 宽移动端。[完成][证据:check:browser]\n"
            "2) 支持键盘焦点与删除确认。[完成][证据:check:browser]\n"
            "3) JSON 导入失败时展示明确原因。[完成][证据:check:unit]"
        ),
        summary={
            "changed_files": ["templates/index.html"],
            "verification": [
                {"kind": "browser", "success": True},
                {"kind": "unit", "success": True},
            ],
            "processes": [],
        },
    )

    assert report["requirement_coverage"]["success"] is True
    assert [item["id"] for item in report["requirement_coverage"]["items"]] == [3, 4, 5]
