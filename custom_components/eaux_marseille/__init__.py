"""Eaux de Marseille integration for Home Assistant."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant

from .api import EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID
from .coordinator import EauxDeMarseilleCoordinator
from .statistics import async_import_historical_statistics

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


@dataclass
class EauxDeMarseilleData:
    """Runtime data attached to a config entry."""

    client: EauxDeMarseilleClient
    coordinator: EauxDeMarseilleCoordinator


# Type alias for our typed config entries.
type EauxDeMarseilleConfigEntry = ConfigEntry[EauxDeMarseilleData]


async def async_setup_entry(hass: HomeAssistant, entry: EauxDeMarseilleConfigEntry) -> bool:
    """Set up Eaux de Marseille from a config entry."""
    client = EauxDeMarseilleClient(
        login=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        contract_id=entry.data[CONF_CONTRACT_ID],
    )

    coordinator = EauxDeMarseilleCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EauxDeMarseilleData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _run_import(_event: Event | None = None) -> None:
        try:
            await async_import_historical_statistics(hass, client, entry.data[CONF_CONTRACT_ID])
        except Exception:
            _LOGGER.exception("Failed to import historical statistics")

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
