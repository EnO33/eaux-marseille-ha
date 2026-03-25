"""Eaux de Marseille integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .api import EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID, DOMAIN, ENTRY_CLIENT, ENTRY_COORDINATOR
from .coordinator import EauxDeMarseilleCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Eaux de Marseille from a config entry."""
    client = EauxDeMarseilleClient(
        login=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        contract_id=entry.data[CONF_CONTRACT_ID],
    )

    coordinator = EauxDeMarseilleCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        ENTRY_CLIENT: client,
        ENTRY_COORDINATOR: coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        entry_data[ENTRY_CLIENT].close()
    return unload_ok
