"""Tests for the Eaux de Marseille integration setup.

These tests require a full Home Assistant environment and only run in CI.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.eaux_marseille import EauxDeMarseilleData

from .conftest import MOCK_CONSUMPTION

pytestmark = [pytest.mark.ha_required, pytest.mark.usefixtures("enable_custom_integrations")]


async def test_setup_entry(hass: HomeAssistant, mock_client: MagicMock, mock_config_entry) -> None:
    """Integration sets up correctly from a config entry."""
    with (
        patch(
            "custom_components.eaux_marseille.EauxDeMarseilleClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
            return_value=MOCK_CONSUMPTION,
        ),
        patch(
            "custom_components.eaux_marseille.async_import_historical_statistics",
        ),
    ):
        result = await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert isinstance(mock_config_entry.runtime_data, EauxDeMarseilleData)
    assert mock_config_entry.runtime_data.client is not None
    assert mock_config_entry.runtime_data.coordinator is not None


async def test_unload_entry(hass: HomeAssistant, mock_client: MagicMock, mock_config_entry) -> None:
    """Integration unloads correctly."""
    with (
        patch(
            "custom_components.eaux_marseille.EauxDeMarseilleClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
            return_value=MOCK_CONSUMPTION,
        ),
        patch(
            "custom_components.eaux_marseille.async_import_historical_statistics",
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    mock_client.close.assert_called()


async def test_statistics_reimported_daily(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """The historical statistics import re-runs on a daily timer.

    Regression for the Energy-dashboard gap (#30): a fresh contract that
    had no data at setup time never got its monthly statistic because the
    import only ran once. It must now re-run on a daily cadence.
    """
    mock_import = AsyncMock()
    with (
        patch(
            "custom_components.eaux_marseille.EauxDeMarseilleClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
            return_value=MOCK_CONSUMPTION,
        ),
        patch(
            "custom_components.eaux_marseille.async_import_historical_statistics",
            new=mock_import,
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        # One import at setup.
        assert mock_import.await_count == 1

        # Advance time past the daily interval — the tracker fires again.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=1, minutes=1))
        await hass.async_block_till_done()
        assert mock_import.await_count == 2

        # And again the next day.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=2, minutes=2))
        await hass.async_block_till_done()
        assert mock_import.await_count == 3


async def test_daily_reimport_timer_cancelled_on_unload(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry
) -> None:
    """Unloading the entry stops the daily re-import timer (no leak)."""
    mock_import = AsyncMock()
    with (
        patch(
            "custom_components.eaux_marseille.EauxDeMarseilleClient",
            return_value=mock_client,
        ),
        patch(
            "custom_components.eaux_marseille.coordinator.EauxDeMarseilleCoordinator._async_update_data",
            return_value=MOCK_CONSUMPTION,
        ),
        patch(
            "custom_components.eaux_marseille.async_import_historical_statistics",
            new=mock_import,
        ),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        count_at_unload = mock_import.await_count

        # Fire a time change well past the interval — the timer must be
        # gone, so the import count stays put.
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=1, minutes=1))
        await hass.async_block_till_done()
        assert mock_import.await_count == count_at_unload
