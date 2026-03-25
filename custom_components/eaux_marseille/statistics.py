"""Historical statistics importer for Eaux de Marseille.

Backfills monthly water consumption into the Home Assistant recorder
using the internal statistics API. Called once on initial setup.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    get_last_statistics,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant

from .api import EauxDeMarseilleClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_START_YEAR = 2024


async def async_import_historical_statistics(
    hass: HomeAssistant,
    client: EauxDeMarseilleClient,
    contract_id: str,
) -> None:
    """
    Import all available monthly statistics into the HA recorder.

    Skips months that are already present to avoid duplicates.
    Safe to call multiple times.
    """
    statistic_id = f"{DOMAIN}:{contract_id}_monthly_consumption"

    # Check what is already stored to avoid re-importing
    existing = await get_instance(hass).async_add_executor_job(
        lambda: get_last_statistics(hass, 1, statistic_id, True, {"mean"})
    )
    last_ts: float = existing[statistic_id][0]["start"] if existing.get(statistic_id) else 0.0

    current_year = datetime.now(timezone.utc).year
    stats: list[StatisticData] = []

    for year in range(_START_YEAR, current_year + 1):
        try:
            entries = await hass.async_add_executor_job(
                client.fetch_monthly_range, year
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Could not fetch history for %d: %s", year, err)
            continue

        for entry in entries:
            date_str: str = entry.get("dateReleve", "")
            value: float | None = entry.get("volumeConsoEnM3")
            if not date_str or value is None:
                continue

            dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
            if dt.timestamp() <= last_ts:
                continue

            stats.append(
                StatisticData(
                    start=dt,
                    mean=round(float(value), 3),
                    min=round(float(value), 3),
                    max=round(float(value), 3),
                )
            )

    if not stats:
        _LOGGER.debug("No new historical statistics to import for contract %s", contract_id)
        return

    # Build metadata — mean_type and unit_class required since HA 2025.x
    metadata_kwargs: dict = {
        "has_mean": True,
        "has_sum": False,
        "name": f"Eaux de Marseille {contract_id} — Monthly consumption",
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_of_measurement": UnitOfVolume.CUBIC_METERS,
    }

    # Add newer required fields if available in this HA version
    try:
        from homeassistant.components.recorder.models import MeanType  # noqa: PLC0415
        metadata_kwargs["mean_type"] = MeanType.ARITHMETIC
    except ImportError:
        pass

    metadata = StatisticMetaData(**metadata_kwargs)

    async_import_statistics(hass, metadata, stats)
    _LOGGER.info(
        "Imported %d monthly statistics for contract %s", len(stats), contract_id
    )