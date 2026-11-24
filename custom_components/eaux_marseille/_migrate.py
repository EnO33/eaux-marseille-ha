"""Config entry migrations.

1.1 -> 1.2 (#130): daily statistics fed by the mobility fallback were dated
in the host's time zone, so on a host running in UTC their points sit at
00:00 UTC instead of midnight Paris time. The fixed import writes new points
at the right hour but cannot move the old ones, which would stay in the
recorder next to them. The daily statistic is cleared once; the import that
follows setup rebuilds it from the portal.
"""

from __future__ import annotations

import logging

# Not re-exported in the recorder package's ``__all__`` (see ``statistics``).
from homeassistant.components.recorder import get_instance  # type: ignore[attr-defined]
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_CONTRACT_ID
from .statistics import daily_statistic_id

_LOGGER = logging.getLogger(__name__)


async def async_migrate(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Bring ``entry`` to the current version; ``False`` refuses a downgrade."""
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        _clear_daily_statistic(hass, entry.data[CONF_CONTRACT_ID])
        hass.config_entries.async_update_entry(entry, minor_version=2)
    return True


def _clear_daily_statistic(hass: HomeAssistant, contract_id: str) -> None:
    """Queue the deletion of the contract's daily statistic, if the recorder runs.

    The recorder processes its queue in order, so the deletion lands before
    the points written by the import that follows setup.
    """
    if "recorder" not in hass.config.components:
        return
    _LOGGER.info("Clearing the daily statistic; the next import rebuilds it with corrected dates")
    get_instance(hass).async_clear_statistics([daily_statistic_id(contract_id)])
