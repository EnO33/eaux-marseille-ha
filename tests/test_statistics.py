"""Tests for the historical statistics importer.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.core import HomeAssistant

from custom_components.eaux_marseille.const import DOMAIN
from custom_components.eaux_marseille.statistics import async_import_historical_statistics

from .conftest import MOCK_CONTRACT_ID, MOCK_MONTHLY_ENTRIES

pytestmark = pytest.mark.ha_required


@pytest.fixture
def mock_recorder(hass: HomeAssistant):
    """Mock the recorder instance."""
    instance = MagicMock()
    # async_db_ready is an asyncio.Event in the real recorder
    db_ready = asyncio.Event()
    db_ready.set()
    type(instance).async_db_ready = PropertyMock(return_value=db_ready)
    instance.async_add_executor_job = hass.async_add_executor_job

    with patch(
        "custom_components.eaux_marseille.statistics.get_instance",
        return_value=instance,
    ):
        yield instance


@pytest.fixture
def mock_get_last_stats():
    """Mock get_last_statistics to return no existing data."""
    with patch(
        "custom_components.eaux_marseille.statistics.get_last_statistics",
        return_value={},
    ) as mock:
        yield mock


@pytest.fixture
def mock_add_external_stats():
    """Mock async_add_external_statistics."""
    with patch(
        "custom_components.eaux_marseille.statistics.async_add_external_statistics",
    ) as mock:
        yield mock


async def test_import_creates_statistics(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_get_last_stats,
    mock_add_external_stats,
) -> None:
    """Import creates statistics from monthly data."""
    mock_client.fetch_monthly_range.return_value = MOCK_MONTHLY_ENTRIES

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    metadata, stats = mock_add_external_stats.call_args.args[1], mock_add_external_stats.call_args.args[2]

    assert metadata["source"] == DOMAIN
    assert metadata["statistic_id"] == f"{DOMAIN}:monthly_consumption_{MOCK_CONTRACT_ID}"
    assert metadata["has_sum"] is True
    assert metadata["has_mean"] is False

    assert len(stats) == 3
    assert stats[0]["state"] == 3.0
    assert stats[0]["sum"] == 3.0
    assert stats[1]["state"] == 4.5
    assert stats[1]["sum"] == 7.5
    assert stats[2]["state"] == 2.0
    assert stats[2]["sum"] == 9.5


async def test_import_skips_existing(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_add_external_stats,
) -> None:
    """Import skips entries older than the last imported timestamp."""
    last_ts = datetime(2024, 8, 15, tzinfo=timezone.utc).timestamp()
    statistic_id = f"{DOMAIN}:monthly_consumption_{MOCK_CONTRACT_ID}"

    with patch(
        "custom_components.eaux_marseille.statistics.get_last_statistics",
        return_value={
            statistic_id: [{"start": last_ts, "sum": 7.5}]
        },
    ):
        mock_client.fetch_monthly_range.return_value = MOCK_MONTHLY_ENTRIES
        await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    stats = mock_add_external_stats.call_args.args[2]
    assert len(stats) == 1
    assert stats[0]["state"] == 2.0
    assert stats[0]["sum"] == 9.5


async def test_import_no_new_data(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_get_last_stats,
    mock_add_external_stats,
) -> None:
    """Import does nothing when API returns no entries."""
    mock_client.fetch_monthly_range.return_value = []

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_not_called()


async def test_import_handles_api_failure(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_get_last_stats,
    mock_add_external_stats,
) -> None:
    """Import continues when one year fails to fetch."""
    mock_client.fetch_monthly_range.side_effect = [
        Exception("API down"),
        MOCK_MONTHLY_ENTRIES,
        [],
    ]

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    stats = mock_add_external_stats.call_args.args[2]
    assert len(stats) == 3


async def test_import_skips_entries_without_date(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_recorder,
    mock_get_last_stats,
    mock_add_external_stats,
) -> None:
    """Import skips entries with missing date or volume."""
    mock_client.fetch_monthly_range.return_value = [
        {"dateReleve": "", "volumeConsoEnM3": 3.0},
        {"dateReleve": "2024-07-15T00:00:00+02:00", "volumeConsoEnM3": None},
        {"dateReleve": "2024-08-15T00:00:00+02:00", "volumeConsoEnM3": 4.5},
    ]

    await async_import_historical_statistics(hass, mock_client, MOCK_CONTRACT_ID)

    mock_add_external_stats.assert_called_once()
    stats = mock_add_external_stats.call_args.args[2]
    assert len(stats) == 1
    assert stats[0]["state"] == 4.5
