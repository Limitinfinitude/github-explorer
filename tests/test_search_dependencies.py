from pathlib import Path


def test_trending_parser_dependencies_are_declared():
    requirements = (Path(__file__).parent.parent / "requirements.txt").read_text(encoding="utf-8").lower()

    assert "beautifulsoup4" in requirements
    assert "lxml" in requirements
