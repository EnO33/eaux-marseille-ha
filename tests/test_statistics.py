"""Tests for the historical statistics importer.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.core import HomeAssistant

from custom_components.eaux_marseille.const import DOMAIN
from custom_components.eaux_marseille.statistics import async_import_historical_statistics

from .conftest import MOCK_CONTRACT_ID, MOCK_MONTHLY_ENTRIES

pytestmark = pytest.mark.ha_required


@pytest.fixture
def mock_recorder(hass: HomeAssistant):
    """Mock the recorder instance."""
    instance = MagicMock()
    # async_db_ready must be awaitable — use a resolved Future.
    future = asyncio.get_event_loop().create_future()
    future.set_result(True)
    type(instance).async_db_ready = PropertyMock(return_value=future)
    instance.async_add_executor_job = hass.async_add_executor_job

    with patch(
        "custom_components.eaux_marseille.statistics.get_instance",
        return_value=instance,
    ):
        yield instance


@pytest.fixture
def mock_add_external_stats():
    """Mock async_add_external_statistics."""
    with patch(
        "custom_components.eaux_marseille.statistics.async_add_external_statistics",
    ) as mock:
        yield mock


def _only_year(target_year: int, entries: list):
    """Return a fetch_monthly_range side effect that serves entries for one year.

    Year-independent so the test doesn't break when the CI clock rolls
    into a new year (the importer walks _START_YEAR..current_year).
    """

    def _side_effect(year: int):
        return entries if year == target_year else []

    return _side_effect


async def test_import_creates_statistics(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Import creates statistics from monthly data."""
    mock_client.fetch_monthly_range.side_effect = _only_year(2024, MOCK_MONTHLY_ENTRIES)

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    metadata, stats = (
        mock_add_external_stats.call_args.args[1],
        mock_add_external_stats.call_args.args[2],
    )

    assert metadata["source"] == DOMAIN
    assert metadata["statistic_id"] == f"{DOMAIN}:monthly_consumption_{MOCK_CONTRACT_ID}"
    assert metadata["has_sum"] is True
    # has_mean=False was deprecated and removed in HA 2026.4 in favour of
    # the typed StatisticMeanType.NONE (see issue #19).
    assert metadata["mean_type"] == StatisticMeanType.NONE

    assert len(stats) == 3
    assert stats[0]["state"] == 3.0
    assert stats[0]["sum"] == 3.0
    assert stats[1]["state"] == 4.5
    assert stats[1]["sum"] == 7.5
    assert stats[2]["state"] == 2.0
    assert stats[2]["sum"] == 9.5


async def test_import_reimports_full_series_every_run(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """The full series is re-derived on every run, not skipped (#31).

    The importer no longer reads the last-imported timestamp, so months
    are never skipped: a partial/stale month gets overwritten with its
    current value. Running the import twice produces the identical full
    series both times.
    """
    mock_client.fetch_monthly_range.side_effect = _only_year(2024, MOCK_MONTHLY_ENTRIES)
    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)
    first = mock_add_external_stats.call_args.args[2]

    mock_add_external_stats.reset_mock()
    mock_client.fetch_monthly_range.side_effect = _only_year(2024, MOCK_MONTHLY_ENTRIES)
    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)
    second = mock_add_external_stats.call_args.args[2]

    # Same full 3-month series each time — nothing is skipped on re-run.
    assert len(first) == len(second) == 3
    assert [s["start"] for s in first] == [s["start"] for s in second]
    assert [s["sum"] for s in first] == [s["sum"] for s in second] == [3.0, 7.5, 9.5]


async def test_import_no_data(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Import does nothing when the portal returns no entries for any year."""
    mock_client.fetch_monthly_range.return_value = []

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_not_called()


async def test_import_handles_api_failure(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Import continues when one year fails to fetch."""

    def _side_effect(year: int):
        if year == 2024:
            raise RuntimeError("API down")
        if year == 2025:
            return MOCK_MONTHLY_ENTRIES
        return []

    mock_client.fetch_monthly_range.side_effect = _side_effect

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    stats = mock_add_external_stats.call_args.args[2]
    assert len(stats) == 3


async def test_daily_statistic_imported_when_telemetry_available(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Contracts with daily telemetry also get a daily statistic (#29)."""
    mock_client.is_daily_available.return_value = True
    mock_client.fetch_monthly_range.side_effect = _only_year(2024, MOCK_MONTHLY_ENTRIES)
    mock_client.fetch_daily_range.side_effect = _only_year(
        2024,
        [
            {"dateReleve": "2024-07-15T00:00:00+02:00", "volumeConsoEnM3": 0.2},
            {"dateReleve": "2024-07-16T00:00:00+02:00", "volumeConsoEnM3": 0.3},
        ],
    )

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    # Two imports: monthly first, then daily.
    assert mock_add_external_stats.call_count == 2
    daily_metadata = mock_add_external_stats.call_args_list[1].args[1]
    daily_stats = mock_add_external_stats.call_args_list[1].args[2]
    assert daily_metadata["statistic_id"] == f"{DOMAIN}:daily_consumption_{MOCK_CONTRACT_ID}"
    assert "Daily consumption" in daily_metadata["name"]
    assert len(daily_stats) == 2
    assert daily_stats[0]["sum"] == 0.2
    assert daily_stats[1]["sum"] == 0.5


async def test_no_daily_statistic_for_monthly_only_contract(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Monthly-only contracts (probe False) get exactly one import."""
    mock_client.is_daily_available.return_value = False
    mock_client.fetch_monthly_range.side_effect = _only_year(2024, MOCK_MONTHLY_ENTRIES)

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    assert mock_add_external_stats.call_count == 1
    mock_client.fetch_daily_range.assert_not_called()


async def test_import_skips_entries_without_date(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Import skips entries with missing date or volume."""
    mock_client.fetch_monthly_range.side_effect = _only_year(
        2024,
        [
            {"dateReleve": "", "volumeConsoEnM3": 3.0},
            {"dateReleve": "2024-07-15T00:00:00+02:00", "volumeConsoEnM3": None},
            {"dateReleve": "2024-08-15T00:00:00+02:00", "volumeConsoEnM3": 4.5},
        ],
    )

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    stats = mock_add_external_stats.call_args.args[2]
    assert len(stats) == 1
    assert stats[0]["state"] == 4.5
