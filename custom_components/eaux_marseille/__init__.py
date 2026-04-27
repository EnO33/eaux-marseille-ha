"""Eaux de Marseille integration for Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import config_validation as cv

from .api import EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID, CONF_PROVIDER, DEFAULT_PROVIDER, DOMAIN, Provider
from .coordinator import EauxDeMarseilleCoordinator
from .services import async_register_services
from .statistics import async_import_historical_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

# We are a config-entry-only integration: there is no YAML schema, the
# user configures us via the UI. Hassfest requires this declaration as
# soon as ``async_setup`` is defined (added when we registered services).
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class EauxDeMarseilleData:
    """Runtime data attached to a config entry."""

    client: EauxDeMarseilleClient
    coordinator: EauxDeMarseilleCoordinator


# Type alias for our typed config entries.
type EauxDeMarseilleConfigEntry = ConfigEntry[EauxDeMarseilleData]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration-wide services.

    Called once when Home Assistant first loads the integration component
    (independent of any config entry). The ``config`` argument is empty
    for config-flow-only integrations like this one.
    """
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EauxDeMarseilleConfigEntry) -> bool:
    """Set up Eaux de Marseille from a config entry."""
    # Entries created before 1.9.0 don't store the provider; default to SEM.
    provider = Provider(entry.data.get(CONF_PROVIDER, DEFAULT_PROVIDER.value))
    client = EauxDeMarseilleClient(
        login=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        contract_id=entry.data[CONF_CONTRACT_ID],
        provider=provider,
    )

    coordinator = EauxDeMarseilleCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EauxDeMarseilleData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # async_import_historical_statistics handles its own errors and
    # surfaces them as HA repair issues; no need to wrap a second time.
    async def _run_import(_event: Event | None = None) -> None:
        await async_import_historical_statistics(hass, client, entry.data[CONF_CONTRACT_ID])

    if hass.is_running:
        hass.async_create_task(_run_import())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _run_import)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: EauxDeMarseilleConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.close()
    return unload_ok
