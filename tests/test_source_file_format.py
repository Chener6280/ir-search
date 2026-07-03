from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
KEY_FILES = [
    "ir_search/research/orchestrator.py",
    "ir_search/research/synthesizer.py",
    "ir_search/research/schemas.py",
    "ir_search/documents/fetcher.py",
    "ir_search/evidence/extractor.py",
    "ir_search/evidence/verifier.py",
    "ir_search/mcp_server.py",
]

KEY_LINE_THRESHOLDS = {
    "ir_search/mcp_server.py": 100,
    "ir_search/research/orchestrator.py": 250,
    "ir_search/research/synthesizer.py": 80,
    "ir_search/research/schemas.py": 30,
    "ir_search/evidence/extractor.py": 150,
    "ir_search/evidence/verifier.py": 150,
    "tests/acceptance_cases.yaml": 100,
}


def test_source_files_are_lf_multiline():
    for path in _source_files():
        data = path.read_bytes()

        assert b"\r" not in data, path
        if path.name in {"README.md", "pyproject.toml"} or path.suffix in {".py", ".md", ".toml", ".yaml", ".yml"}:
            assert _lf_line_count(path) > 1, path


def test_key_python_files_have_reviewable_line_counts():
    gitattributes_path = REPO_ROOT / ".gitattributes"
    gitattributes = gitattributes_path.read_text(encoding="utf-8")

    assert b"\r" not in gitattributes_path.read_bytes()
    assert _lf_line_count(gitattributes_path) >= 13
    assert ".cursorindexingignore text eol=lf" in gitattributes.split("\n")
    for rel in KEY_FILES:
        path = REPO_ROOT / rel

        assert _lf_line_count(path) > 20, rel
    for rel, minimum in KEY_LINE_THRESHOLDS.items():
        path = REPO_ROOT / rel

        assert _lf_line_count(path) >= minimum, rel


def test_pyproject_and_readme_are_parseable_and_multiline():
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None

    pyproject_text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if tomllib is not None:
        tomllib.loads(pyproject_text)
    else:
        assert "[build-system]" in pyproject_text

    assert _lf_line_count(REPO_ROOT / "README.md") > 20


def _lf_line_count(path: Path) -> int:
    data = path.read_bytes()
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in ["ir_search", "tests", "scripts", "benchmarks", "docs", "templates"]:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix in {".py", ".md", ".toml", ".yaml", ".yml", ".json", ".template", ".mdc"} or path.name in {
                ".cursorignore",
                ".cursorindexingignore",
            }:
                files.append(path)
    files.extend([REPO_ROOT / "README.md", REPO_ROOT / "pyproject.toml"])
    return files
