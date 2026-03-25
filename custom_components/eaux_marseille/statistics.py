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
    try:
        # Wait for the recorder to be fully ready before accessing statistics.
        # Without this, the import can fail when the integration loads early
        # during HA startup (typical with HACS integrations).
        instance = get_instance(hass)
        await instance.async_db_ready

        _LOGGER.debug("Starting historical statistics import for contract %s", contract_id)

        statistic_id = f"{DOMAIN}:monthly_consumption_{contract_id}"

        existing = await instance.async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum"}
        )
        last_ts: float = existing[statistic_id][0]["start"] if existing.get(statistic_id) else 0.0
        _LOGGER.debug("Last imported timestamp: %s", last_ts)

        current_year = datetime.now(timezone.utc).year
        stats: list[StatisticData] = []
        running_sum = 0.0

        # If we already have data, retrieve the last sum to continue from there.
        if last_ts > 0 and existing.get(statistic_id):
            last_entry = existing[statistic_id][0]
            running_sum = last_entry.get("sum") or 0.0

        for year in range(_START_YEAR, current_year + 1):
            try:
                entries = await hass.async_add_executor_job(
                    client.fetch_monthly_range, year
                )
                _LOGGER.debug("Year %d: fetched %d entries", year, len(entries))
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("Could not fetch history for %d: %s", year, err)
                continue

            for entry in entries:
                date_str: str = entry.get("dateReleve", "")
                value: float | None = entry.get("volumeConsoEnM3")
                if not date_str or value is None:
                    continue

                dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                # Align to the start of the hour (required by HA recorder).
                dt = dt.replace(minute=0, second=0, microsecond=0)

                if dt.timestamp() <= last_ts:
                    continue

                consumption = round(float(value), 3)
                running_sum = round(running_sum + consumption, 3)

                stats.append(
                    StatisticData(
                        start=dt,
                        state=consumption,
                        sum=running_sum,
                    )
                )

        if not stats:
            _LOGGER.debug("No new historical statistics to import for contract %s", contract_id)
            return

        metadata = StatisticMetaData(
            has_mean=False,
            has_sum=True,
            name=f"Eaux de Marseille {contract_id} — Monthly consumption",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        )

        async_import_statistics(hass, metadata, stats)

        _LOGGER.info(
            "Imported %d monthly statistics for contract %s (total sum: %s m³)",
            len(stats), contract_id, running_sum,
        )

    except Exception as err:
        _LOGGER.exception("Error during historical statistics import: %s", err)
        raise