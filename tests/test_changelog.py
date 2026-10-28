"""docs/CHANGELOG.md keeps step with the version."""

import re
from pathlib import Path

from capscan import __version__

CHANGELOG = Path(__file__).resolve().parents[1] / "docs" / "CHANGELOG.md"


def _versions():
    return re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.M)


def test_changelog_has_the_current_version():
    assert __version__ in _versions()


def test_changelog_starts_with_unreleased_and_every_version_is_linked():
    text = CHANGELOG.read_text(encoding="utf-8")
    versions = _versions()
    assert versions[0] == "Unreleased"
    for v in versions:
        assert re.search(rf"^\[{re.escape(v)}\]: https://", text, re.M), v
