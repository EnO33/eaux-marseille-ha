"""DataUpdateCoordinator for Eaux de Marseille."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ConsumptionData, EauxDeMarseilleApiError, EauxDeMarseilleAuthError, EauxDeMarseilleClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(hours=1)


class EauxDeMarseilleCoordinator(DataUpdateCoordinator[ConsumptionData]):
    """Coordinator that polls the Eaux de Marseille portal every hour."""

    def __init__(self, hass: HomeAssistant, client: EauxDeMarseilleClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> ConsumptionData:
        try:
            await self.hass.async_add_executor_job(self.client.authenticate)
            return await self.hass.async_add_executor_job(self.client.fetch)
        except EauxDeMarseilleAuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except EauxDeMarseilleApiError as err:
            raise UpdateFailed(f"API error: {err}") from err
