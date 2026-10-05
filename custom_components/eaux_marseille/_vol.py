"""Schema library shared with Home Assistant, across HA releases.

Home Assistant 2026.10 replaced voluptuous with probatio, a drop-in
reimplementation with the same API, and its own signatures
(``async_show_form``, ``ServiceRegistry.async_register``...) are now typed
against probatio. Older releases ship and type against voluptuous.

Runtime: importing ``voluptuous`` is correct on every release. From 2026.10
on, the ``homeassistant`` package aliases ``voluptuous`` to probatio in
``sys.modules`` before any integration loads, so this resolves to the very
library HA validates with; before 2026.10 it is the real voluptuous.

Type checking: expose probatio, which is what current HA expects. On a
release that predates it, probatio is not installed and mypy falls back to
``Any`` for this module (see the ``ignore_missing_imports`` override in
pyproject.toml), which matches HA's loosely typed voluptuous boundary there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import probatio as vol
else:
    import voluptuous as vol

__all__ = ["vol"]
