from __future__ import annotations

from pathlib import Path


def test_pyproject_limits_setuptools_package_discovery():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.setuptools.packages.find]" in pyproject
    assert 'include = ["ir_search*"]' in pyproject


def test_mcp_extra_matches_the_fastmcp_v1_runtime():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'mcp>=1,<2' in pyproject
