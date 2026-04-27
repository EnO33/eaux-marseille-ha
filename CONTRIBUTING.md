# Contributing to Eaux de Marseille

Thanks for considering a contribution. This guide covers the bits that are not obvious from the codebase.

## Before opening a PR

- For non-trivial changes, **open an issue first** so we can agree on the approach. Saves work for everyone.
- The integration declares **Gold** quality scale. Any change should keep all existing quality-scale rules satisfied (`custom_components/eaux_marseille/quality_scale.yaml`).
- We don't accept PRs that mention "Claude", "GPT", "Copilot" or any AI tooling in the commit message, PR title or release notes. Tooling is a means; the contribution should stand on its own merits.

## Running tests locally

Two paths depending on how thoroughly you need to test:

### Quick path — API-only tests (no Home Assistant install)

Most of the test surface (HTTP client, retries, redirects, models, diagnostics) runs without the full Home Assistant package. Useful for fast iteration:

```bash
pip install pytest pytest-asyncio pytest-cov aioresponses tenacity
python -m pytest tests/test_api.py
```

The `tests/conftest.py` stubs the `homeassistant.*` modules with `MagicMock` so the integration's source can be imported standalone. Tests that actually touch HA helpers are marked `@pytest.mark.ha_required` and **skipped** in this mode (you'll see `19 skipped`).

### Full path — every test, like CI

Install the full test stack — pulls in Home Assistant core and ~100 transitive dependencies (≈450 MB). Recommended once, before opening a PR:

```bash
pip install -r requirements_test.txt
python -m pytest tests/
```

You should see `49 passed`. If you see skips, your environment didn't pick up `pytest-homeassistant-custom-component`; double-check the install.

The pinned versions in `requirements_test.txt` are constrained by `pytest-homeassistant-custom-component` 0.13.316 (the last release that supports Python 3.13). Don't relax the pins without coordinating — see the `dependabot.yml` ignore list and the comments in the file.

## Style and quality gates

The CI runs five jobs and they all must pass before merge:

| Tool                        | Command (local)                                    |
|-----------------------------|----------------------------------------------------|
| Ruff (lint)                 | `python -m ruff check custom_components/ tests/`   |
| Ruff (format)               | `python -m ruff format custom_components/ tests/`  |
| Mypy (strict)               | `python -m mypy custom_components/eaux_marseille/` |
| Pytest                      | `python -m pytest tests/` (with the full stack)    |
| Hassfest + HACS validation  | only on CI (Docker-based)                          |

Run the first four before pushing — they're all fast and they catch ~99% of CI failures.

## Code conventions

- Modules stay short and single-purpose. Aim for under ~250 lines per file; prefer extracting a focused helper module over growing an existing one.
- Defensive parsing on all upstream payloads: use `dict.get(key, default)`, never `dict[key]`. The portal can and does return partial data on freshly activated contracts.
- `# type: ignore[code]` is allowed at the HA boundary (their stubs are weak), always with an inline comment explaining why. `# noqa` only with a specific code, never bare.
- Public exception types in `exceptions.py`. Internal helpers prefixed with `_` (file or method).

## Commit messages

Conventional Commits style is preferred but not required:

```
fix: drop unsafe=True on CookieJar
feat: cache AEL token across coordinator polls
docs: explain the 1h polling interval
refactor: split self.token into _app_token and _ael_token
```

Keep the subject line under 72 chars. Use the body for the *why*, not the *what* — the diff already says what changed.

## Releases

Maintainer-only.

```bash
# After CI is green on main:
git tag -a vX.Y.Z -m "vX.Y.Z: short summary"
git push origin vX.Y.Z
gh release create vX.Y.Z --title "vX.Y.Z - ..." --notes "..."
```

Bump rules:

- Patch (X.Y.**Z**) — bug fixes, doc-only changes, internal refactors that don't change behaviour
- Minor (X.**Y**.0) — new feature, new sensor, dependency added
- Major (**X**.0.0) — breaking change for users (removed config option, renamed entity, etc.)

The minimum HA version is bumped via `hacs.json` (`homeassistant` field) and the README "Requirements" line. Keep the two in sync.
