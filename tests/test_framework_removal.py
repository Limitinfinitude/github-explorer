from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOTS = (ROOT / "src" / "agent", ROOT / "src" / "web" / "src")


def test_production_source_and_default_requirements_do_not_reference_retired_frameworks():
    forbidden = ("langgraph", "langsmith")
    offenders = []
    paths = [ROOT / "requirements.txt"]
    for source_root in PRODUCTION_ROOTS:
        paths.extend(path for path in source_root.rglob("*") if path.suffix in {".py", ".ts", ".tsx"})

    for path in paths:
        text = path.read_text(encoding="utf-8").casefold()
        if any(term in text for term in forbidden):
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []


def test_retired_graph_modules_and_legacy_requirements_are_removed():
    retired = [
        ROOT / "src" / "agent" / "graph.py",
        ROOT / "src" / "agent" / "state.py",
        ROOT / "src" / "agent" / "nodes.py",
        ROOT / "src" / "agent" / "swarm_graph.py",
        ROOT / "src" / "agent" / "swarm_state.py",
        ROOT / "src" / "agent" / "subgraphs",
        ROOT / "src" / "agent" / "runtime" / "legacy.py",
        ROOT / "requirements-legacy.txt",
    ]

    assert [str(path.relative_to(ROOT)) for path in retired if path.exists()] == []
