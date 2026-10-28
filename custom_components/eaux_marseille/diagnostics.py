"""Diagnostics support for the Eaux de Marseille integration.

Home Assistant exposes a "Download diagnostics" button on the integration
page; clicking it calls :func:`async_get_config_entry_diagnostics` and
returns a JSON file the user can attach to bug reports.

We sanitize the username and contract number, and never include the
password — the goal is to surface useful runtime state without leaking
credentials.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import EauxDeMarseilleConfigEntry
from .const import CONF_CONTRACT_ID

_REDACT_KEYS = {CONF_PASSWORD, CONF_USERNAME, CONF_CONTRACT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: EauxDeMarseilleConfigEntry,
) -> dict[str, Any]:
    """Return sanitized diagnostics for ``entry``."""
    coordinator = entry.runtime_data.coordinator
    last_exception = coordinator.last_exception
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), _REDACT_KEYS),
            "options": dict(entry.options),
            "version": entry.version,
            "minor_version": entry.minor_version,
            "source": entry.source,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_s": (
                coordinator.update_interval.total_seconds() if coordinator.update_interval else None
            ),
            # Error messages embed request URLs, which carry the contract.
            "last_exception": (
                coordinator.client.redact(repr(last_exception)) if last_exception else None
            ),
        },
        "data": asdict(coordinator.data) if coordinator.data is not None else None,
    }
