"""Print the pytest-homeassistant-custom-component release for a HA channel.

Each phacc release pins exactly one Home Assistant version, betas included
(e.g. 0.13.368 -> homeassistant==2026.10.0b0). CI tests both channels:

* ``latest``: the newest phacc release, whatever HA it pins (may be a beta).
* ``stable``: the newest phacc release that pins a final HA release.

Usage: python .github/scripts/phacc_version.py {stable|latest}
"""

import json
import re
import sys
import urllib.request

PYPI = "https://pypi.org/pypi/pytest-homeassistant-custom-component"
FINAL = re.compile(r"\d+\.\d+\.\d+")


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https URL)
        return json.load(resp)


def _pinned_ha(version: str) -> str:
    requires = _get(f"{PYPI}/{version}/json")["info"].get("requires_dist") or []
    return next((r.split("==", 1)[1] for r in requires if r.startswith("homeassistant==")), "")


def resolve(channel: str) -> str:
    releases = _get(f"{PYPI}/json")["releases"]
    candidates = sorted(
        (
            v
            for v, files in releases.items()
            if FINAL.fullmatch(v) and files and not all(f.get("yanked") for f in files)
        ),
        key=lambda v: tuple(int(p) for p in v.split(".")),
        reverse=True,
    )
    for version in candidates:
        if channel == "latest" or FINAL.fullmatch(_pinned_ha(version)):
            return version
    raise SystemExit(f"No {channel} pytest-homeassistant-custom-component release found")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"stable", "latest"}:
        raise SystemExit(__doc__)
    print(resolve(sys.argv[1]))
