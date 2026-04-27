"""Verify translation files stay structurally in sync.

``strings.json`` is the canonical source. ``translations/en.json`` is the
English copy that ships with the integration (HA falls back to it when
the user's locale isn't translated). ``translations/fr.json`` is the
French translation.

A drift between any two of these files (a key added in one but not the
others) is a real bug — past releases have shipped with mismatched
files until a user spotted the missing label.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).parent.parent / "custom_components" / "eaux_marseille"
_STRINGS = _ROOT / "strings.json"
_TRANSLATIONS = _ROOT / "translations"


def _flatten(d: Any, prefix: str = "") -> set[str]:
    """Return the set of leaf paths in a nested dict (e.g. ``a.b.c``)."""
    keys: set[str] = set()
    if isinstance(d, dict):
        for k, v in d.items():
            sub = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                keys |= _flatten(v, sub)
            else:
                keys.add(sub)
    return keys


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.fixture
def strings_keys() -> set[str]:
    return _flatten(_load(_STRINGS))


@pytest.mark.parametrize("locale", ["en", "fr"])
def test_translation_matches_strings(locale: str, strings_keys: set[str]) -> None:
    """Each translations/<locale>.json carries exactly the keys in strings.json."""
    translation_path = _TRANSLATIONS / f"{locale}.json"
    translation_keys = _flatten(_load(translation_path))

    missing = strings_keys - translation_keys
    extra = translation_keys - strings_keys

    assert not missing, (
        f"translations/{locale}.json is missing {len(missing)} key(s) present in "
        f"strings.json: {sorted(missing)}"
    )
    assert not extra, (
        f"translations/{locale}.json has {len(extra)} extra key(s) absent from "
        f"strings.json: {sorted(extra)}"
    )


def test_no_url_in_translation_strings() -> None:
    """Hassfest forbids URLs in translation values; we mirror the check locally.

    Catches the regression we already shipped once (v1.11.0 -> v1.11.1).
    URLs go in ``learn_more_url`` on ``async_create_issue`` instead.
    """
    for path in [_STRINGS, _TRANSLATIONS / "en.json", _TRANSLATIONS / "fr.json"]:
        text = path.read_text(encoding="utf-8")
        # Crude but effective: scan for http(s):// in any string value.
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            # Only flag content lines (key/value pairs), not structural braces.
            if ('"http://' in stripped or '"https://' in stripped) and ":" in stripped:
                raise AssertionError(
                    f"{path.name}:{line_no} contains a URL — use learn_more_url "
                    f"on the issue/error helper instead. Line: {stripped}"
                )
