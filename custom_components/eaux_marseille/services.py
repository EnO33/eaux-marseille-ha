"""Custom services for the Eaux de Marseille integration.

Two user-callable actions, registered once at integration load time:

* ``eaux_marseille.refresh`` — request an immediate coordinator poll for
  a given config entry, instead of waiting for the next 1-hour cycle.
* ``eaux_marseille.reimport_statistics`` — re-run the historical monthly
  statistics import for a given config entry. Useful after a failed
  import (a HA repair issue is raised in that case, see ``statistics``).

Both services take a single ``config_entry`` parameter. The HA UI renders
this via the ``ConfigEntrySelector``, so users pick the contract from a
dropdown rather than typing an entry id.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError

from ._client_factory import build_client
from .const import CONF_CONTRACT_ID, DOMAIN
from .statistics import async_import_historical_statistics

if TYPE_CHECKING:
    from . import EauxDeMarseilleConfigEntry

_LOGGER = logging.getLogger(__name__)

SERVICE_REFRESH = "refresh"
SERVICE_REIMPORT_STATISTICS = "reimport_statistics"

ATTR_CONFIG_ENTRY = "config_entry"

_SCHEMA = vol.Schema({vol.Required(ATTR_CONFIG_ENTRY): str})


def _resolve_entry(hass: HomeAssistant, entry_id: str) -> EauxDeMarseilleConfigEntry:
    """Look up an entry by id and validate it belongs to us and is loaded."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_unknown_entry",
            translation_placeholders={"entry_id": entry_id},
        )
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="service_entry_not_loaded",
            translation_placeholders={"entry_id": entry_id, "state": str(entry.state)},
        )
    return entry  # type: ignore[return-value]  # narrowed by domain check above


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the Eaux de Marseille services.

    Idempotent: ``has_service`` short-circuits the second call so we can
    invoke this from ``async_setup`` (or any equivalent hook) without
    worrying about duplicate registration.
    """
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    async def _async_refresh(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call.data[ATTR_CONFIG_ENTRY])
        _LOGGER.info("User-triggered refresh for entry %s", entry.entry_id)
        await entry.runtime_data.coordinator.async_request_refresh()

    async def _async_reimport(call: ServiceCall) -> None:
        entry = _resolve_entry(hass, call.data[ATTR_CONFIG_ENTRY])
        _LOGGER.info("User-triggered statistics re-import for entry %s", entry.entry_id)
        # Use a fresh client so we don't disturb the coordinator's cached
        # session — re-importing a few years of history fires several
        # GETs and we don't want them to interleave with a regular poll.
        client = build_client(entry)
        try:
            await async_import_historical_statistics(hass, client, entry.data[CONF_CONTRACT_ID])
        finally:
            await client.close()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _async_refresh, schema=_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REIMPORT_STATISTICS, _async_reimport, schema=_SCHEMA
    )
