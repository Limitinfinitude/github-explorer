from pathlib import Path

import pytest

from agent.runtime.evidence import EvidencePathError, normalize_evidence_path


def test_normalizes_absolute_and_windows_paths_to_workspace_relative_posix(tmp_path: Path):
    root = tmp_path / "Demo"
    target = root / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.touch()

    absolute = normalize_evidence_path(root, target)
    windows_style = normalize_evidence_path(root, r"src\main.py")

    assert absolute == "src/main.py"
    assert windows_style == absolute


def test_preserves_unicode_directory_and_workspace_root(tmp_path: Path):
    root = tmp_path / "中文项目"
    directory = root / "资料"
    directory.mkdir(parents=True)

    assert normalize_evidence_path(root, directory) == "资料"
    assert normalize_evidence_path(root, root) == "."


def test_windows_path_comparison_is_case_insensitive(tmp_path: Path, monkeypatch):
    root = tmp_path / "Demo"
    root.mkdir()
    monkeypatch.setattr("agent.runtime.evidence.os.name", "nt")

    normalized = normalize_evidence_path(str(root).upper(), str(root / "SRC" / "App.py"))

    assert normalized == "SRC/App.py"


def test_rejects_path_outside_workspace(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(EvidencePathError) as error:
        normalize_evidence_path(root, tmp_path / "outside.txt")

    assert error.value.code == "outside_workspace"
