import json
from pathlib import Path

from agent.runtime.commands import CommandRunner
from agent.runtime.project_tools import ProjectTools
from agent.runtime.verifier import Verifier
from agent.runtime.workspace import WorkspaceManager


def make_runtime(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    manager = WorkspaceManager()
    manager.bind("session", root)
    runner = CommandRunner(manager)
    return root, ProjectTools(manager, runner), Verifier(manager, runner)


def test_detects_python_and_node_projects(tmp_path: Path):
    root, projects, _ = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (root / "package.json").write_text(json.dumps({
        "scripts": {"build": "vite build", "test": "vitest"},
    }), encoding="utf-8")

    result = projects.detect("session")

    assert result.success is True
    assert result.data["languages"] == ["python", "node"]
    assert result.data["package_managers"] == ["pip", "npm"]
    assert result.data["node_scripts"] == {"build": "vite build", "test": "vitest"}


def test_verification_commands_use_project_python_without_masking_failures(tmp_path: Path):
    root, projects, verifier = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert projects.ensure_venv("session").success
    info = projects.detect("session").data

    commands = verifier.commands("session", info)

    assert len(commands) == 1
    assert "-m pytest -q" in commands[0]
    assert "|| true" not in commands[0]


def test_verifier_keeps_python_syntax_failure(tmp_path: Path):
    root, projects, verifier = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / "app.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    assert projects.ensure_venv("session").success
    info = projects.detect("session").data

    result = verifier.run("session", info)

    assert result.success is False
    assert result.data["checks"][0]["success"] is False
    assert result.data["checks"][0]["returncode"] != 0


def test_install_command_uses_workspace_venv_python(tmp_path: Path):
    root, projects, _ = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    info = projects.detect("session").data

    commands = projects.install_commands("session", info)

    assert str(venv_python) in commands[0]
    assert "-m pip install -r requirements.txt" in commands[0]


def test_python_project_without_venv_does_not_fall_back_to_host_python(tmp_path: Path):
    root, projects, verifier = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("", encoding="utf-8")
    info = projects.detect("session").data

    result = verifier.run("session", info)

    assert result.success is False
    assert "ensure_venv" in result.error
    assert result.data["environment"]["project_root"] == str(root)
    assert result.data["environment"]["python_executable"] is None

    install = projects.install_dependencies("session", info)
    assert install.success is False
    assert "ensure_venv" in install.error


def test_verification_evidence_records_python_cwd_and_executable(tmp_path: Path):
    root, projects, verifier = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("", encoding="utf-8")
    assert projects.ensure_venv("session").success
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    info = projects.detect("session").data

    result = verifier.run("session", info)

    assert result.success is True
    check = result.data["checks"][0]
    assert check["cwd"] == str(root)
    assert check["python_executable"] == str(root / ".venv" / "Scripts" / "python.exe")
    assert check["returncode"] == 0
    assert check["kind"] == "static"


def test_ensure_venv_reports_created_project_interpreter(tmp_path: Path):
    root, projects, _ = make_runtime(tmp_path)

    result = projects.ensure_venv("session")

    assert result.success is True
    assert result.data["cwd"] == str(root)
    assert result.data["python_executable"] == str(
        root / ".venv" / "Scripts" / "python.exe"
    )


def test_pytest_verification_is_classified_as_unit(tmp_path: Path):
    root, projects, verifier = make_runtime(tmp_path)
    (root / "requirements.txt").write_text("", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert projects.ensure_venv("session").success
    info = projects.detect("session").data
    command = verifier.commands("session", info)[0]
    assert verifier.command_kind(command) == "unit"


def test_verifier_without_runnable_checks_is_not_success(tmp_path: Path):
    root, _, verifier = make_runtime(tmp_path)
    result = verifier.run("session", {"root": str(root), "languages": []})
    assert result.success is False
    assert result.data["checks"] == []


def test_subproject_uses_its_own_venv(tmp_path: Path):
    root, projects, verifier = make_runtime(tmp_path)
    subproject = root / "repo"
    subproject.mkdir()
    (subproject / "requirements.txt").write_text("", encoding="utf-8")
    venv_python = subproject / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    info = projects.detect("session", "repo").data

    install = projects.install_commands("session", info)
    verify = verifier.commands("session", info)

    assert str(venv_python) in install[0]
    assert str(venv_python) in verify[0]
