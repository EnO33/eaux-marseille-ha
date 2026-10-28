"""Verify the runtime requirements are declared identically everywhere.

``manifest.json`` is what Home Assistant installs for users.
``pyproject.toml`` (read by deptry) and ``requirements_test.txt`` (installed
by CI) mirror it. Dependabot bumps only the last two, so a drift would let
CI test a floor users are not guaranteed to get.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

_REPO = Path(__file__).parent.parent
_MANIFEST = _REPO / "custom_components" / "eaux_marseille" / "manifest.json"


def _name(requirement: str) -> str:
    """Return the distribution name of a PEP 508 requirement."""
    return re.split(r"[\s<>=!~;\[]", requirement.strip(), maxsplit=1)[0]


def _manifest_requirements() -> set[str]:
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    return set(manifest.get("requirements", []))


def _pyproject_requirements() -> set[str]:
    pyproject = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    return set(pyproject.get("project", {}).get("dependencies", []))


def _test_requirements_for(names: set[str]) -> set[str]:
    """Return the ``requirements_test.txt`` lines pinning one of ``names``."""
    lines = (_REPO / "requirements_test.txt").read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and _name(line) in names}


def test_pyproject_matches_manifest() -> None:
    assert _pyproject_requirements() == _manifest_requirements()


def test_requirements_test_matches_manifest() -> None:
    manifest = _manifest_requirements()
    names = {_name(req) for req in manifest}
    assert _test_requirements_for(names) == manifest
