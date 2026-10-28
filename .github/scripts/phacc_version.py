"""Print the pytest-homeassistant-custom-component release for a HA channel.

Each phacc release pins exactly one Home Assistant version, betas included
(e.g. 0.13.368 -> homeassistant==2026.10.0b0). CI tests three channels:

* ``latest``: the newest phacc release, whatever HA it pins (may be a beta).
* ``stable``: the newest phacc release that pins a final HA release.
* ``minimum``: the newest phacc release that pins the HA version declared
  in ``hacs.json``, so the advertised floor is one CI actually runs.

Usage: python .github/scripts/phacc_version.py {stable|latest|minimum}
"""

import json
import re
import sys
import urllib.request
from pathlib import Path

PYPI = "https://pypi.org/pypi/pytest-homeassistant-custom-component"
FINAL = re.compile(r"\d+\.\d+\.\d+")
HACS_JSON = Path(__file__).resolve().parents[2] / "hacs.json"


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https URL)
        return json.load(resp)


def _pinned_ha(version: str) -> str:
    requires = _get(f"{PYPI}/{version}/json")["info"].get("requires_dist") or []
    return next((r.split("==", 1)[1] for r in requires if r.startswith("homeassistant==")), "")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def _declared_minimum() -> str:
    return json.loads(HACS_JSON.read_text(encoding="utf-8"))["homeassistant"]


def resolve(channel: str) -> str:
    releases = _get(f"{PYPI}/json")["releases"]
    candidates = sorted(
        (
            v
            for v, files in releases.items()
            if FINAL.fullmatch(v) and files and not all(f.get("yanked") for f in files)
        ),
        key=_key,
        reverse=True,
    )
    floor = _declared_minimum() if channel == "minimum" else ""
    for version in candidates:
        pinned = _pinned_ha(version)
        if channel == "latest" or (channel == "stable" and FINAL.fullmatch(pinned)):
            return version
        if floor and pinned == floor:
            return version
        if floor and FINAL.fullmatch(pinned) and _key(pinned) < _key(floor):
            break  # walked past the floor: no release pins it
    raise SystemExit(f"No {channel} pytest-homeassistant-custom-component release found")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"stable", "latest", "minimum"}:
        raise SystemExit(__doc__)
    print(resolve(sys.argv[1]))
