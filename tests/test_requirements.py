"""requirements.txt (what KiCad installs into the plugin's venv) matches pyproject.toml."""

import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")       # Python 3.11 on

ROOT = Path(__file__).resolve().parents[1]


def _reqs(lines):
    return sorted(re.sub(r"\s+", "", l) for l in lines if l.strip() and not l.startswith("#"))


def test_requirements_match_pyproject():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert _reqs(req) == _reqs(project["dependencies"])
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert _reqs(dev) == _reqs(project["optional-dependencies"]["dev"])
