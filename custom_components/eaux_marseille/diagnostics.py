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
_REDACTED = "**REDACTED**"


def _scrub_exception(exc: BaseException, contract_id: str, username: str) -> str:
    """Return a printable form of ``exc`` with credentials/IDs redacted.

    The integration's error messages embed the full request URL (which
    contains the contract ID) and sometimes echo response bodies. We
    pre-scrub them here so the diagnostics download stays safe to attach
    to a public bug report.
    """
    text = repr(exc)
    if contract_id:
        text = text.replace(contract_id, _REDACTED)
    if username:
        text = text.replace(username, _REDACTED)
    return text


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
            "last_exception": (
                _scrub_exception(
                    last_exception,
                    contract_id=str(entry.data.get(CONF_CONTRACT_ID, "")),
                    username=str(entry.data.get(CONF_USERNAME, "")),
                )
                if last_exception
                else None
            ),
        },
        "data": asdict(coordinator.data) if coordinator.data is not None else None,
    }
