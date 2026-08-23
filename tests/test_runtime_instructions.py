from agent.runtime.instructions import InstructionLoader


def test_loader_collects_user_root_and_nested_instructions_in_order(tmp_path):
    user_dir = tmp_path / "user"
    root = tmp_path / "project"
    nested = root / "src"
    nested.mkdir(parents=True)
    user_dir.mkdir()
    (user_dir / "AGENTS.md").write_text("user rule", encoding="utf-8")
    (root / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (nested / "AGENTS.md").write_text("nested rule", encoding="utf-8")

    result = InstructionLoader(root, nested, user_home=user_dir).load()

    assert [item.scope for item in result.sources] == ["user", "project", "directory"]
    assert result.rendered.index("user rule") < result.rendered.index("root rule")
    assert result.rendered.index("root rule") < result.rendered.index("nested rule")


def test_loader_override_replaces_same_directory_file(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENTS.md").write_text("normal", encoding="utf-8")
    (root / "AGENTS.override.md").write_text("override", encoding="utf-8")

    result = InstructionLoader(root, root, user_home=tmp_path / "missing").load()

    assert "override" in result.rendered
    assert "normal" not in result.rendered


def test_loader_does_not_walk_above_workspace_root(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "project"
    root.mkdir(parents=True)
    (parent / "AGENTS.md").write_text("outside", encoding="utf-8")

    result = InstructionLoader(root, root, user_home=tmp_path / "missing").load()

    assert result.sources == []


def test_loader_preserves_more_specific_rule_when_budget_is_small(tmp_path):
    root = tmp_path / "project"
    nested = root / "src"
    nested.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rule " * 1000, encoding="utf-8")
    (nested / "AGENTS.md").write_text("specific rule", encoding="utf-8")

    result = InstructionLoader(
        root, nested, user_home=tmp_path / "missing", max_bytes=100,
    ).load()

    assert "specific rule" in result.rendered
    assert result.warnings


def test_loader_skips_invalid_utf8_and_reports_warning(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "AGENTS.md").write_bytes(b"\xff\xfeinvalid")

    result = InstructionLoader(root, root, user_home=tmp_path / "missing").load()

    assert result.sources == []
    assert result.rendered == ""
    assert any("无法读取指令文件" in warning for warning in result.warnings)
