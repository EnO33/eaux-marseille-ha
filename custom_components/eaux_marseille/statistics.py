"""Historical statistics importer for Eaux de Marseille.

Backfills monthly water consumption into the Home Assistant recorder
using the external-statistics API — plus a daily series for contracts
whose meter exposes daily telemetry (JOURNEE granularity).

The full monthly series is re-imported on every run and the recorder
points are overwritten (``async_add_external_statistics`` upserts by
``(statistic_id, start)``). This is deliberate: the portal revises
recent months — the current month grows from a partial value to its
final total, and quarterly-read meters get their months reconciled
when a reading lands. An "import once, never touch again" approach
froze those months on stale partial values (#31). Re-deriving the
whole series from the live data each run keeps the Energy dashboard
correct and is self-healing for already-corrupted statistics.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
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
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .api import EauxDeMarseilleClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_START_YEAR = 2024
_ISSUE_STATS_IMPORT_FAILED = "statistics_import_failed_{contract_id}"


async def async_import_historical_statistics(
    hass: HomeAssistant,
    client: EauxDeMarseilleClient,
    contract_id: str,
) -> None:
    """Re-import the full monthly statistics series into the HA recorder.

    Always re-derives every month from the live portal data and
    overwrites the recorder points, so revised/partial months are
    corrected rather than frozen (#31).

    Failures surface as a HA repair issue (Settings -> Repairs) so the
    user gets actionable feedback instead of a silent log line. A
    previously raised issue is cleared on the next successful run.
    """
    issue_id = _ISSUE_STATS_IMPORT_FAILED.format(contract_id=contract_id)
    try:
        # Recorder may not be ready yet at HACS startup time.
        instance = get_instance(hass)
        await instance.async_db_ready

        _LOGGER.debug("Starting statistics (re)import for contract %s", contract_id)

        stats = await _collect_series(client.fetch_monthly_range)
        if stats:
            statistic_id = f"{DOMAIN}:monthly_consumption_{contract_id}"
            async_add_external_statistics(
                hass,
                _build_metadata(contract_id, statistic_id, "Monthly consumption"),
                stats,
            )
            _LOGGER.info(
                "Imported %d monthly statistics for contract %s (total sum: %s m³)",
                len(stats),
                contract_id,
                stats[-1]["sum"],
            )
        else:
            _LOGGER.debug(
                "No monthly statistics available to import for contract %s",
                contract_id,
            )

        # Contracts whose meter exposes daily telemetry (JOURNEE
        # granularity) also get a daily statistic. There's no upfront
        # probe: ``fetch_daily_range`` is tolerant and returns an empty
        # list for contracts without it, so a monthly-only contract just
        # yields no daily series and we skip the import.
        daily_stats = await _collect_series(client.fetch_daily_range)
        if daily_stats:
            statistic_id = f"{DOMAIN}:daily_consumption_{contract_id}"
            async_add_external_statistics(
                hass,
                _build_metadata(contract_id, statistic_id, "Daily consumption"),
                daily_stats,
            )
            _LOGGER.info(
                "Imported %d daily statistics for contract %s (total sum: %s m³)",
                len(daily_stats),
                contract_id,
                daily_stats[-1]["sum"],
            )
        else:
            _LOGGER.debug(
                "No daily telemetry to import for contract %s",
                contract_id,
            )
    except Exception:
        _LOGGER.exception("Error during historical statistics import")
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="statistics_import_failed",
            translation_placeholders={"contract_id": contract_id},
            learn_more_url="https://github.com/EnO33/eaux-marseille-ha/issues",
        )
        return
    # On success, clear any previously raised repair issue.
    ir.async_delete_issue(hass, DOMAIN, issue_id)


async def _collect_series(
    fetch_range: Callable[[int], Awaitable[list[dict[str, Any]]]],
) -> list[StatisticData]:
    """Fetch every entry from ``_START_YEAR`` to now and build the series.

    ``fetch_range`` is a per-year fetcher (monthly or daily — same entry
    shape either way). The cumulative ``sum`` is recomputed from zero
    over the full series every time, so overwriting the recorder points
    yields an internally consistent set whatever the portal has revised
    since the last run.

    Entries are deduplicated by reading date and then accumulated in
    chronological order. Two reasons:

    * The portal serves the JOURNEE series newest-first; feeding that
      order into a running sum would assign the largest sum to the
      earliest day, leaving a statistic whose cumulative total decreases
      over time (it reads as empty/broken in the Energy dashboard).
    * A year's query window can return a boundary day that also belongs
      to the adjacent year — a ``dateReleve`` like
      ``2026-01-01T00:00:00+02:00`` (22:00 UTC the previous day) falls
      inside the prior year's UTC window. Without dedup that day is
      counted in both yearly fetches and inflates the cumulative total.
    """
    by_date: dict[str, dict[str, Any]] = {}
    current_year = datetime.now(UTC).year

    for year in range(_START_YEAR, current_year + 1):
        try:
            entries = await fetch_range(year)
        except Exception as err:
            _LOGGER.warning("Could not fetch history for %d: %s", year, err)
            continue
        _LOGGER.debug("Year %d: fetched %d entries", year, len(entries))

        for entry in entries:
            date = entry.get("dateReleve", "")
            if date:
                by_date[date] = entry

    stats: list[StatisticData] = []
    running_sum = 0.0
    for date in sorted(by_date):
        stat = _entry_to_stat(by_date[date], running_sum)
        if stat is None:
            continue
        running_sum = stat["sum"]
        stats.append(stat)

    return stats


def _entry_to_stat(
    entry: dict[str, Any],
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

    consumption = round(float(value), 3)
    return StatisticData(
        start=dt,
        state=consumption,
        sum=round(running_sum + consumption, 3),
    )


def _build_metadata(contract_id: str, statistic_id: str, label: str) -> StatisticMetaData:
    """Build the recorder metadata for a consumption statistic.

    ``mean_type=StatisticMeanType.NONE`` is the canonical replacement for
    the legacy ``has_mean=False`` flag (which HA core deprecated and
    removed in 2026.4). Water consumption is a counter, so no mean is
    recorded.

    ``unit_class`` must be set explicitly to the volume class. The
    recorder does not derive it for external statistics, and the Energy
    dashboard's water source picker filters strictly on
    ``unit_class == "volume"`` (the statistics-graph card's picker does
    too) — leaving it ``None`` makes the statistic invisible in both, even
    though its unit is m³. Entity-backed water statistics get ``"volume"``
    automatically; we mirror that. The literal matches
    ``homeassistant.util.unit_conversion.VolumeConverter.UNIT_CLASS`` (not
    imported, to keep this module loadable under the lightweight test
    stubs).
    """
    metadata: StatisticMetaData = {
        "mean_type": StatisticMeanType.NONE,
        "has_sum": True,
        "name": f"Eaux de Marseille {contract_id} — {label}",
        "source": DOMAIN,
        "statistic_id": statistic_id,
        "unit_class": "volume",
        "unit_of_measurement": UnitOfVolume.CUBIC_METERS,
    }
    return metadata
