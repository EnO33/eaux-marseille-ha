"""Eaux de Marseille integration for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from ._client_factory import build_client
from .api import EauxDeMarseilleClient
from .const import CONF_CONTRACT_ID, DOMAIN
from .coordinator import EauxDeMarseilleCoordinator
from .services import async_register_services
from .statistics import async_import_historical_statistics

PLATFORMS: list[Platform] = [Platform.SENSOR]

# How often to re-run the historical statistics import after the initial
# one. The import is idempotent (already-imported months are skipped via
# the recorder's last timestamp), so a daily cadence is cheap: it lets a
# freshly-activated contract pick up its statistic within a day of the
# portal exposing data, and refreshes the current month on the Energy
# dashboard without needing a Home Assistant restart.
_STATS_REIMPORT_INTERVAL = timedelta(days=1)

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
    client = build_client(entry)

    coordinator = EauxDeMarseilleCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = EauxDeMarseilleData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # async_import_historical_statistics handles its own errors and
    # surfaces them as HA repair issues; no need to wrap a second time.
    # Accepts any positional arg (None / Event / datetime) so the same
    # callback works whether invoked directly, from the HA-started event,
    # or from the daily time-interval tracker.
    async def _run_import(*_: Any) -> None:
        await async_import_historical_statistics(hass, client, entry.data[CONF_CONTRACT_ID])

    if hass.is_running:
        hass.async_create_task(_run_import())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _run_import)

    # Re-run the import daily so fresh contracts pick up their statistic
    # once the portal starts serving data, and mature contracts get the
    # new month on the Energy dashboard without a restart. The unsub is
    # registered with the entry so the timer is cancelled on unload.
    entry.async_on_unload(async_track_time_interval(hass, _run_import, _STATS_REIMPORT_INTERVAL))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: EauxDeMarseilleConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.client.close()
    return unload_ok
