"""Historical statistics importer for Eaux de Marseille.

Backfills monthly water consumption into the Home Assistant recorder
using the external-statistics API. Safe to call multiple times — months
that are already present are skipped.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

# `get_instance` is the documented public helper but isn't re-exported
# in __all__ on the recorder package, so mypy flags the import.
from homeassistant.components.recorder import get_instance  # type: ignore[attr-defined]
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
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
    """Import the available monthly statistics into the HA recorder."""
    try:
        # Recorder may not be ready yet at HACS startup time.
        instance = get_instance(hass)
        await instance.async_db_ready

        statistic_id = f"{DOMAIN}:monthly_consumption_{contract_id}"
        last_ts, running_sum = await _load_last_imported(hass, statistic_id)

        _LOGGER.debug(
            "Starting historical statistics import for contract %s (last_ts=%s)",
            contract_id,
            last_ts,
        )

        stats = await _collect_new_stats(client, last_ts, running_sum)
        if not stats:
            _LOGGER.debug(
                "No new historical statistics to import for contract %s",
                contract_id,
            )
            return

        async_add_external_statistics(hass, _build_metadata(contract_id, statistic_id), stats)
        _LOGGER.info(
            "Imported %d monthly statistics for contract %s (total sum: %s m³)",
            len(stats),
            contract_id,
            stats[-1]["sum"],
        )
    except Exception:
        _LOGGER.exception("Error during historical statistics import")
        raise


async def _load_last_imported(
    hass: HomeAssistant,
    statistic_id: str,
) -> tuple[float, float]:
    """Return the timestamp and running sum of the latest stored stat."""
    instance = get_instance(hass)
    existing = await instance.async_add_executor_job(
        get_last_statistics,
        hass,
        1,
        statistic_id,
        True,
        {"sum"},
    )
    if not existing.get(statistic_id):
        return 0.0, 0.0
    last_entry = existing[statistic_id][0]
    return float(last_entry["start"]), float(last_entry.get("sum") or 0.0)


async def _collect_new_stats(
    client: EauxDeMarseilleClient,
    last_ts: float,
    running_sum: float,
) -> list[StatisticData]:
    """Fetch and convert all months newer than ``last_ts``."""
    stats: list[StatisticData] = []
    current_year = datetime.now(UTC).year

    # If we already imported some history, only refetch from that year
    # forward — re-walking the years before ``last_ts`` would just hit
    # the portal for entries we'd discard. On a fresh entry, ``last_ts``
    # is 0.0 and we start from ``_START_YEAR``.
    if last_ts > 0:
        start_year = max(_START_YEAR, datetime.fromtimestamp(last_ts, tz=UTC).year)
    else:
        start_year = _START_YEAR

    for year in range(start_year, current_year + 1):
        try:
            entries = await client.fetch_monthly_range(year)
        except Exception as err:
            _LOGGER.warning("Could not fetch history for %d: %s", year, err)
            continue
        _LOGGER.debug("Year %d: fetched %d entries", year, len(entries))

        for entry in entries:
            stat = _entry_to_stat(entry, last_ts, running_sum)
            if stat is None:
                continue
            running_sum = stat["sum"]
            stats.append(stat)

    return stats


def _entry_to_stat(
    entry: dict[str, Any],
    last_ts: float,
    running_sum: float,
) -> StatisticData | None:
    """Convert a portal entry to :class:`StatisticData`, or skip it."""
    date_str = entry.get("dateReleve", "")
    value = entry.get("volumeConsoEnM3")
    if not date_str or value is None:
        return None

    # Recorder requires timestamps aligned to the hour.
    dt = (
        datetime.fromisoformat(date_str)
        .astimezone(UTC)
        .replace(
            minute=0,
            second=0,
            microsecond=0,
        )
    )
    if dt.timestamp() <= last_ts:
        return None

    consumption = round(float(value), 3)
    return StatisticData(
        start=dt,
        state=consumption,
        sum=round(running_sum + consumption, 3),
    )


def _build_metadata(contract_id: str, statistic_id: str) -> StatisticMetaData:
    """Build the recorder metadata for the monthly-consumption statistic.

    ``mean_type=StatisticMeanType.NONE`` is the canonical replacement for
    the legacy ``has_mean=False`` flag (which HA core deprecated and
    removed in 2026.4). Water consumption is a counter, so no mean is
    recorded.
    """
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"Eaux de Marseille {contract_id} — Monthly consumption",
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=UnitOfVolume.CUBIC_METERS,
    )
